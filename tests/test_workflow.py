import base64
import copy
import io
import json
from pathlib import Path
import sqlite3
import fitz
from PIL import Image, ImageChops, PngImagePlugin
import pytest
from cryptography.exceptions import InvalidTag
from agents.deid_agent import DeidAgent, DetectionError
from engine.auth import Auth
from engine.deid import DeidEngine
from engine.documents import DocumentProcessor
from engine.service import DeidService
from engine.vault import PHIVault


class FixtureDetector:
    """Test double for external detection, never enabled in the application."""
    def __init__(self, results):
        self.results = copy.deepcopy(results)
        self.calls = 0

    def run_multimodal_deid(self, *args, **kwargs):
        result = self.results[self.calls]
        self.calls += 1
        return result

    def run_deid(self, text):
        return copy.deepcopy(self.results[0])


@pytest.fixture
def report():
    results = []
    with fitz.open() as doc:
        for name in ["Alice Morgan", "Bob Stone"]:
            page = doc.new_page(width=400, height=500)
            page.insert_text((25, 45), f"Patient: {name}", fontsize=12)
            page.insert_text((25, 90), f"Patient: {name}", fontsize=12)
            page.insert_text((25, 170), "Findings: Diabetes. Glucose 120 mg/dL.", fontsize=12)
            page.draw_rect(fitz.Rect(20, 200, 380, 300), color=(0, 0, 0))
            page.insert_text((25, 230), "Blood pressure 120/80 mmHg", fontsize=12)
            entities = []
            for rect in page.search_for(name):
                entities.append(dict(text=name, label="PERSON", box_2d=[rect.y0 / 500 * 1000, rect.x0 / 400 * 1000, rect.y1 / 500 * 1000, rect.x1 / 400 * 1000]))
            results.append(dict(full_text=page.get_text(), personal_data=entities))
        doc.set_metadata(dict(title="Alice Morgan", author="Private Patient"))
        doc.embfile_add("original.txt", b"SECRET EMBEDDED PATIENT DATA")
        doc[0].add_text_annot((350, 400), "Alice Morgan private annotation")
        original = doc.tobytes()
    return original, results


@pytest.fixture
def accounts(tmp_path):
    service = DeidService(tmp_path)
    service.auth.add_user("admin", "admin-password-for-tests", "admin")
    service.auth.add_user("doctor", "doctor-password-for-tests", "doctor")
    return service, service.auth.login("admin", "admin-password-for-tests"), service.auth.login("doctor", "doctor-password-for-tests")


def test_multipage_layout_and_private_restoration(accounts, report, tmp_path):
    service, admin, doctor = accounts
    original, results = report
    detector = FixtureDetector(results)
    analysis = service.analyze(admin, original, "Alice-private-report.pdf", detector)
    assert detector.calls == 2
    masked = service.preview(admin, analysis)
    assert "Alice Morgan" not in masked.masked_text and "Bob Stone" not in masked.masked_text
    assert "Glucose 120 mg/dL" in masked.masked_text
    assert set(masked.mapping.values()) == {"Alice Morgan", "Bob Stone"}
    assert len(masked.mapping) == 2
    with fitz.open(stream=masked.masked, filetype="pdf") as pdf:
        assert len(pdf) == 2 and pdf.embfile_count() == 0
        assert not pdf.metadata.get("title") and not pdf.metadata.get("author")
        for index, page in enumerate(pdf):
            assert page.rect == fitz.Rect(0, 0, 400, 500)
            assert not page.get_text().strip()
            assert not list(page.annots() or [])
            assert not page.get_links()
            # Outside PHI boxes, pixels, table lines and clinical text are unchanged.
            assert ImageChops.difference(analysis.pages[index].image.crop((0, 250, 800, 700)), masked.previews[index].crop((0, 250, 800, 700))).getbbox() is None
            for ent in results[index]["personal_data"]:
                y0, x0, y1, x1 = ent["box_2d"]
                pixel = masked.previews[index].getpixel((int((x0 + x1) / 2 / 1000 * 800), int((y0 + y1) / 2 / 1000 * 1000)))
                assert pixel == (255, 255, 255)
            assert "Patient:" not in masked.masked_text
            assert "[PHI_" not in masked.masked_text
    reference = service.share(admin, analysis, "Alice-private-report.pdf", reviewed=True)
    public = service.documents(doctor)[reference]
    assert "original_file_b64" not in public
    assert "Alice" not in json.dumps(public) and "Bob Stone" not in json.dumps(public)
    assert original not in (tmp_path / "vault.sqlite3").read_bytes()
    assert b"Alice Morgan" not in (tmp_path / "vault.sqlite3").read_bytes()
    assert b"Alice-private-report" not in (tmp_path / "shared.sqlite3").read_bytes()
    with pytest.raises(ValueError, match="not yet"):
        service.restore(admin, reference)
    with pytest.raises(PermissionError):
        service.restore(doctor, reference)
    service.return_report(doctor, reference, "Continue current medication.")
    restored = service.restore(admin, reference)
    assert restored["original"] == original
    assert restored["extension"] == ".pdf"
    assert restored["text"] == "\n\f\n".join(r["full_text"] for r in results)
    assert restored["doctor_notes"] == "Continue current medication."
    # Reopening storage preserves both keys and records.
    assert DeidService(tmp_path).restore(admin, reference)["original"] == original


def test_scan_and_image_metadata_removed(report):
    original, results = report
    processor = DocumentProcessor()
    page = processor.pdf.render(original)[0]
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("patient", "Alice Morgan")
    buffer = io.BytesIO()
    page.image.save(buffer, format="PNG", pnginfo=metadata)
    analysis = processor.analyze(buffer.getvalue(), "scan.png", FixtureDetector([results[0]]))
    masked = processor.mask(analysis)
    with Image.open(io.BytesIO(masked.masked)) as clean:
        assert clean.size == page.image.size
        assert "patient" not in clean.info
        assert not clean.getexif()
    assert "Alice Morgan" not in masked.masked_text
    # Multi-page image-only PDF still processes each page separately.
    scan_pdf = processor.pdf.export([page, page], [page.image, page.image])
    detector = FixtureDetector([results[0], results[0]])
    scanned = processor.analyze(scan_pdf, "scanned.pdf", detector)
    assert detector.calls == 2 and all(not p.native_text for p in scanned.pages)
    assert len(processor.mask(scanned).previews) == 2


def test_text_tokens_distinct_repeat_and_adjacent():
    engine = DeidEngine()
    text = "Alice Bob Alice"
    entities = [dict(text="Alice", start=0, end=5, label="PERSON"), dict(text="Bob", start=6, end=9, label="PERSON"), dict(text="Alice", start=10, end=15, label="PERSON")]
    masked, mapping = engine.mask_text(text, entities, reversible=True)
    assert len(mapping) == 2
    assert set(mapping.values()) == {"Alice", "Bob"}
    assert masked.split()[0] == masked.split()[2]
    assert masked.split()[0] != masked.split()[1]
    # Overlapping detections don't produce duplicate/partial replacements.
    overlap = entities + [dict(text="Ali", start=0, end=3, label="NAME")]
    _, mapping2 = engine.mask_text(text, overlap, reversible=True)
    assert set(mapping2.values()) == {"Alice", "Bob"}
    with pytest.raises(ValueError):
        engine.mask_text(text, [dict(text="wrong", start=0, end=5, label="PERSON")])


def test_text_workflow(accounts):
    service, admin, doctor = accounts
    raw = b"Patient: Alice\nMRN: A-123\nFindings: diabetes, glucose 120 mg/dL."
    analysis = service.analyze(admin, raw, "report.txt", FixtureDetector([dict(personal_data=[dict(text="Alice", label="PERSON")])]))
    reference = service.share(admin, analysis, "report.txt", reviewed=True)
    public = service.documents(doctor)[reference]
    assert "Alice" not in public["masked_text"] and "A-123" not in public["masked_text"]
    service.return_report(doctor, reference, "Reviewed.")
    assert service.restore(admin, reference)["original"] == raw


@pytest.mark.parametrize("box", [None, [0, 0, 0, 0], [-1, 0, 10, 10], [0, 0, 1001, 10], [0, 0, float('nan'), 10], [True, 0, 10, 10]])
def test_invalid_boxes_block_sharing(report, box):
    original, results = report
    results[0]["personal_data"][0]["box_2d"] = box
    with pytest.raises(ValueError):
        DocumentProcessor().analyze(original, "report.pdf", FixtureDetector(results))


def test_local_ocr_finds_repeats_and_unlocated_rules_block_sharing(report):
    original, results = report
    processor = DocumentProcessor()
    page = processor.pdf.render(original)[0]
    scan = page.png()
    results[0]["personal_data"].pop()
    analysis = processor.analyze(scan, "scan.png", FixtureDetector([results[0]]))
    clean = processor.mask(analysis)
    for entity in report[1][0]["personal_data"]:
        y0, x0, y1, x1 = entity["box_2d"]
        assert clean.previews[0].getpixel((int((x0 + x1) * .4), int((y0 + y1) * .5))) == (255, 255, 255)
    # A transcribed occurrence absent from actual page geometry must still fail.
    analysis.texts[0] += "\nAlice Morgan"
    with pytest.raises(ValueError, match="Repeated"):
        processor.mask(analysis)
    result = dict(full_text="Contact alice@example.com", personal_data=[])
    analysis = processor.analyze(scan, "scan.png", FixtureDetector([result]))
    with pytest.raises(ValueError, match="no image location"):
        processor.mask(analysis)


def test_no_detection_success_on_error(accounts, report):
    service, admin, doctor = accounts
    class BrokenDetector:
        def run_multimodal_deid(self, *args, **kwargs):
            raise DetectionError("Quota exhausted")
    with pytest.raises(DetectionError):
        service.analyze(admin, report[0], "report.pdf", BrokenDetector())
    assert service.documents(doctor) == {}
    with pytest.raises(ValueError, match="transcription"):
        service.analyze(admin, report[0], "report.pdf", FixtureDetector([dict(full_text="", personal_data=[])]))


def test_missing_key_and_invalid_provider_response(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(DetectionError, match="GOOGLE_API_KEY"):
        DeidAgent()
    class BadLLM:
        def invoke(self, message):
            return type("Response", (), {"content": "not valid json"})()
    agent = object.__new__(DeidAgent)
    agent.llm = BadLLM()
    with pytest.raises(DetectionError, match="Nothing was shared"):
        agent.run_deid("private input")


def test_authorization_and_sessions(accounts, report):
    service, admin, doctor = accounts
    for token in [None, "forged-token", doctor]:
        with pytest.raises(PermissionError):
            service.analyze(token, report[0], "report.pdf", FixtureDetector(report[1]))
    with pytest.raises(PermissionError):
        service.auth.login("admin", "incorrect")
    with pytest.raises(PermissionError):
        service.auth.login("unknown", "incorrect")
    analysis = service.analyze(admin, report[0], "report.pdf", FixtureDetector(report[1]))
    with pytest.raises(ValueError, match="Review every"):
        service.share(admin, analysis, "report.pdf")
    with pytest.raises(PermissionError):
        service.share(doctor, analysis, "report.pdf", reviewed=True)
    reference = service.share(admin, analysis, "report.pdf", reviewed=True)
    with pytest.raises(PermissionError):
        service.return_report(admin, reference, "Notes")
    with pytest.raises(ValueError):
        service.return_report(doctor, reference, "  ")
    service.auth.logout(admin)
    with pytest.raises(PermissionError):
        service.restore(admin, reference)


def test_vault_tampering_and_lost_key(tmp_path):
    vault = PHIVault(tmp_path)
    vault.store("doc1", b"original PHI", {"[TOKEN]": "Alice"}, ".txt", "private.txt", "digest")
    assert vault.demask_text("[TOKEN]", "doc1") == "Alice"
    with sqlite3.connect(vault.path) as db:
        db.execute("UPDATE records SET id='doc2'")
    with pytest.raises(InvalidTag):
        vault.load("doc2")
    (tmp_path / "vault.key").unlink()
    with pytest.raises(RuntimeError, match="key is missing"):
        PHIVault(tmp_path)


def test_shared_copy_tampering_blocks_restoration(accounts, report):
    service, admin, doctor = accounts
    analysis = service.analyze(admin, report[0], "report.pdf", FixtureDetector(report[1]))
    reference = service.share(admin, analysis, "report.pdf", reviewed=True)
    service.return_report(doctor, reference, "Reviewed")
    with service._db.connect() as db:
        record = service._db.get(reference)
        record["file_b64"] = base64.b64encode(b"different report").decode()
        db.execute("UPDATE documents SET data=? WHERE id=?", (json.dumps(record), reference))
    with pytest.raises(ValueError, match="modified"):
        service.restore(admin, reference)


def test_rotated_pdf_preserves_orientation_and_page_boxes(report):
    original, results = report
    with fitz.open(stream=original, filetype="pdf") as doc:
        doc[0].set_rotation(90)
        rotated = doc.tobytes()
    for entity in results[0]["personal_data"]:
        y0, x0, y1, x1 = entity["box_2d"]
        entity["box_2d"] = [x0, 1000 - y1, x1, 1000 - y0]
    processor = DocumentProcessor()
    analysis = processor.analyze(rotated, "rotated.pdf", FixtureDetector(results))
    masked = processor.mask(analysis)
    with fitz.open(stream=masked.masked, filetype="pdf") as doc:
        assert doc[0].rect == fitz.Rect(0, 0, 500, 400)
        assert doc[1].rect == fitz.Rect(0, 0, 400, 500)
    for entity in results[0]["personal_data"]:
        y0, x0, y1, x1 = entity["box_2d"]
        x, y = int((x0 + x1) * .5), int((y0 + y1) * .4)
        assert masked.previews[0].getpixel((x, y)) == (255, 255, 255)
    analysis.detections.pop()
    with pytest.raises(ValueError, match="Every page"):
        processor.mask(analysis)
