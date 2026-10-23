"""Authorization boundary for the admin -> doctor -> admin workflow."""
import base64
import hashlib
from pathlib import Path
from datetime import datetime, timezone
import uuid
from engine.auth import Auth
from engine.documents import DocumentProcessor
from engine.vault import PHIVault
from utils.db_handler import DBHandler


class DeidService:
    def __init__(self, data_dir):
        self.auth = Auth(data_dir)
        self._vault = PHIVault(data_dir)
        self._db = DBHandler(data_dir)
        self.processor = DocumentProcessor()
        with self._db.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS audit (at TEXT, actor TEXT, action TEXT, reference TEXT)")

    def _audit(self, actor, action, reference):
        with self._db.connect() as db:
            db.execute("INSERT INTO audit VALUES (?,?,?,?)", (datetime.now(timezone.utc).isoformat(), actor, action, reference))

    def analyze(self, session, original, filename, detector):
        self.auth.require(session, ("admin",))
        return self.processor.analyze(original, filename, detector)

    def preview(self, session, analysis):
        self.auth.require(session, ("admin",))
        return self.processor.mask(analysis)

    def share(self, session, analysis, source_name, *, reviewed=False):
        user = self.auth.require(session, ("admin",))
        if not reviewed:
            raise ValueError("Review every masked page before sharing.")
        result = self.processor.mask(analysis)
        # Analysis may take time: revalidate before persistence.
        self.auth.require(session, ("admin",))
        doc_id = uuid.uuid4().hex
        digest = hashlib.sha256(result.masked).hexdigest()
        self._vault.store(doc_id, result.original, result.mapping, analysis.extension, Path(source_name).name, digest)
        try:
            self._db.add_document(doc_id, result.masked_text, base64.b64encode(result.masked).decode(), result.extension, result.phi_count)
        except Exception:
            self._vault.delete(doc_id)
            raise
        self._audit(user["name"], "shared", doc_id)
        return doc_id

    def documents(self, session):
        self.auth.require(session)
        return self._db.get_all_documents()

    def return_report(self, session, doc_id, notes):
        user = self.auth.require(session, ("doctor",))
        self._db.update_doctor_notes(doc_id, notes, user["name"])
        self._audit(user["name"], "returned", doc_id)

    def restore(self, session, doc_id):
        user = self.auth.require(session, ("admin",))
        shared = self._db.get(doc_id)
        if shared["status"] != "Reviewed":
            raise ValueError("The report has not yet been returned by a doctor.")
        private = self._vault.load(doc_id)
        if hashlib.sha256(base64.b64decode(shared["file_b64"])).hexdigest() != private["masked_digest"]:
            raise ValueError("The shared report was modified. Restoration has been blocked.")
        self._audit(user["name"], "restored", doc_id)
        return dict(original=base64.b64decode(private["original"]), extension=private["extension"],
                    text=self._vault.demask_text(shared["masked_text"], doc_id), doctor_notes=shared["doctor_notes"])
