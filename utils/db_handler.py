"""Only masked reports and review notes belong in the shared database."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager


class DBHandler:
    def __init__(self, data_dir):
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "shared.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_document(self, doc_id, masked_text, file_b64, file_ext, phi_count):
        data = dict(title=f"Clinical report {doc_id[:8]}", masked_text=masked_text, file_b64=file_b64,
                    file_ext=file_ext, phi_count=phi_count, doctor_notes="", reviewer="", status="Pending Review",
                    created_at=datetime.now(timezone.utc).isoformat(), updated_at="")
        with self.connect() as db:
            db.execute("INSERT INTO documents VALUES (?,?)", (doc_id, json.dumps(data)))

    def get_all_documents(self):
        with self.connect() as db:
            return {doc_id: json.loads(data) for doc_id, data in db.execute("SELECT id,data FROM documents ORDER BY rowid DESC")}

    def get(self, doc_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM documents WHERE id=?", (doc_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown document reference.")
        return json.loads(row[0])

    def update_doctor_notes(self, doc_id, notes, reviewer):
        if not notes.strip():
            raise ValueError("Enter clinical notes before returning the report.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM documents WHERE id=?", (doc_id,)).fetchone()
            if not row:
                raise KeyError("Unknown document reference.")
            data = json.loads(row[0])
            if data["status"] != "Pending Review":
                raise ValueError("This report has already been returned.")
            data.update(doctor_notes=notes.strip(), reviewer=reviewer, status="Reviewed", updated_at=datetime.now(timezone.utc).isoformat())
            db.execute("UPDATE documents SET data=? WHERE id=?", (json.dumps(data), doc_id))
