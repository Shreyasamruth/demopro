"""AES-256-GCM encrypted original documents and token maps, separate from sharing."""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from contextlib import contextmanager
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class PHIVault:
    def __init__(self, data_dir):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "vault.sqlite3"
        key_path = self.root / "vault.key"
        # Never silently replace the key for an existing vault.
        if not key_path.exists():
            if self.path.exists():
                raise RuntimeError("Vault key is missing. Restore the original key; do not regenerate it.")
            try:
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(AESGCM.generate_key(bit_length=256))
            except FileExistsError:
                pass
        key = key_path.read_bytes()
        if len(key) != 32:
            raise ValueError("Vault key must be 256 bits.")
        self.cipher = AESGCM(key)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, nonce BLOB, ciphertext BLOB)")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def store(self, doc_id, original, mapping, extension, source_name, masked_digest, restoration_text=None):
        data = dict(original=base64.b64encode(original).decode(), mapping=mapping, extension=extension, source_name=source_name, masked_digest=masked_digest)
        if restoration_text is not None:
            data["restoration_text"] = restoration_text
        payload = json.dumps(data).encode()
        nonce = secrets.token_bytes(12)
        encrypted = self.cipher.encrypt(nonce, payload, doc_id.encode())
        with self.connect() as db:
            db.execute("INSERT INTO records VALUES (?,?,?)", (doc_id, nonce, encrypted))

    def load(self, doc_id):
        with self.connect() as db:
            row = db.execute("SELECT nonce,ciphertext FROM records WHERE id=?", (doc_id,)).fetchone()
        if row is None:
            raise KeyError("No private record exists for this reference.")
        return json.loads(self.cipher.decrypt(row[0], row[1], doc_id.encode()))

    def delete(self, doc_id):
        with self.connect() as db:
            db.execute("DELETE FROM records WHERE id=?", (doc_id,))

    def demask_text(self, text, doc_id):
        mapping = self.load(doc_id)["mapping"]
        if not mapping:
            return text
        # One substitution pass avoids recursively interpreting patient text as tokens.
        return re.sub("|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)), lambda m: mapping[m.group()], text)
