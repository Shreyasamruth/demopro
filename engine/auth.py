"""Server-side sessions and password verification. No client-selected roles."""
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import time
from contextlib import contextmanager


class Auth:
    def __init__(self, data_dir):
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "auth.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS users (name TEXT PRIMARY KEY, salt BLOB, password BLOB, role TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, name TEXT, expires REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS failures (name TEXT PRIMARY KEY, count INTEGER, retry_after REAL)")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _hash(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)

    def add_user(self, username, password, role):
        if role not in {"admin", "doctor"} or not username.strip() or len(password) < 12:
            raise ValueError("Use a username, role admin/doctor, and a password of at least 12 characters.")
        salt = secrets.token_bytes(16)
        with self.connect() as db:
            db.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (username, salt, self._hash(password, salt), role))

    def login(self, username, password):
        with self.connect() as db:
            failure = db.execute("SELECT count, retry_after FROM failures WHERE name=?", (username,)).fetchone()
            if failure and failure[1] > time.time():
                raise PermissionError("Too many attempts. Try again later.")
            row = db.execute("SELECT salt,password FROM users WHERE name=?", (username,)).fetchone()
            actual = self._hash(password, row[0] if row else b"unknown-user-000")
            if not row or not hmac.compare_digest(actual, row[1]):
                count = (failure[0] if failure else 0) + 1
                db.execute("INSERT OR REPLACE INTO failures VALUES (?,?,?)", (username, count, time.time() + (60 if count >= 5 else 0)))
            else:
                db.execute("DELETE FROM failures WHERE name=?", (username,))
                token = secrets.token_urlsafe(32)
                db.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), username, time.time() + 8 * 3600))
                return token
        raise PermissionError("Invalid username or password.")

    def require(self, token, roles=("admin", "doctor")):
        if not isinstance(token, str):
            raise PermissionError("Sign in to continue.")
        with self.connect() as db:
            row = db.execute("SELECT u.name,u.role FROM sessions s JOIN users u ON u.name=s.name WHERE s.digest=? AND s.expires>?", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if not row or row[1] not in roles:
            raise PermissionError("This operation requires an authorized account.")
        return {"name": row[0], "role": row[1]}

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),))
