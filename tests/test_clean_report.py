"""Regression checks for complete field removal on the user's report layout."""
import io
import fitz
import numpy as np
from PIL import Image, ImageChops, ImageDraw
import pytest
from engine.documents import DocumentProcessor
from utils.ocr_handler import OCRHandler
from utils.pdf_handler import RenderedPage
from utils.field_layout import remove_text_fields
from tests.test_workflow import FixtureDetector, accounts


def clinical_template():
    with fitz.open() as pdf:
        page = pdf.new_page(width=800, height=650)
        page.draw_rect(fitz.Rect(0, 0, 800, 90), fill=(.02, .17, .29), color=None)
        page.insert_text((50, 50), "EXAMPLE CLINIC", fontsize=24, color=(1, 1, 1))
        page.draw_rect(fitz.Rect(30, 110, 770, 270), fill=(.94, .94, .94), color=(.02, .17, .29))
        rows = [(50, 145, "PATIENT NAME:", 185, "Alicia Marsh", "PERSON"),
                (50, 195, "PATIENT ID:", 185, "PID-421387", "ID"),
                (450, 145, "REPORT DATE:", 610, "21/10/2026", "DATE"),
                (450, 195, "REFERRING DR:", 610, "DR. TORRES", "PERSON")]
        entities = []
        for label_x, y, label, value_x, value, kind in rows:
            page.insert_text((label_x, y), label, fontsize=12)
            page.insert_text((value_x, y), value, fontsize=12)
            rect = page.search_for(value)[0]
            # Reproduce shifted AI boxes as in the screenshot. They must not be used.
            entities.append(dict(text=value, label=kind,
                                 box_2d=[(rect.y0 + 12) / 650 * 1000, rect.x0 / 800 * 1000,
                                         (rect.y1 + 12) / 650 * 1000, rect.x1 / 800 * 1000]))
        page.insert_text((50, 240), "AGE / GENDER:", fontsize=12)
        page.insert_text((185, 240), "67 Yrs / Female", fontsize=12)
        page.insert_text((450, 240), "MODALITY:", fontsize=12)
        page.insert_text((610, 240), "CT BRAIN", fontsize=12)
        page.insert_text((50, 330), "FINDINGS:", fontsize=16)
        page.insert_text((50, 360), "No focal lesion. Glucose 120 mg/dL.", fontsize=12)
        return pdf.tobytes(), dict(full_text=page.get_text(), personal_data=entities)


@pytest.mark.parametrize("scanned", [False, True])
def test_complete_fields_removed_from_grey_panel_and_clinical_content_unchanged(scanned):
    if scanned and not OCRHandler.tesseract_command():
        pytest.skip("Install Tesseract to run the actual scanned-image alignment test")
    raw, result = clinical_template()
    processor = DocumentProcessor()
    if scanned:
        raw = processor.pdf.render(raw)[0].png()
    analysis = processor.analyze(raw, "report.png" if scanned else "report.pdf", FixtureDetector([result]))
    output = processor.mask(analysis)
    clean = output.previews[0]
    source = analysis.pages[0].image
    background = source.getpixel((400, 500))
    for left, top, right, bottom in [(48, 130, 390, 153), (48, 180, 390, 203),
                                     (448, 130, 750, 153), (448, 180, 750, 203)]:
        region = clean.crop((left * 2, top * 2, right * 2, bottom * 2))
        assert region.getextrema() == tuple((v, v) for v in background)
    # Age, modality, clinical findings, letterhead and the panel border survive intact.
    for rect in [(0, 0, 1600, 200), (40, 430, 1550, 520), (0, 560, 1600, 1300)]:
        assert ImageChops.difference(source.crop(rect), clean.crop(rect)).getbbox() is None
    for forbidden in ["PATIENT NAME", "PATIENT ID", "REPORT DATE", "REFERRING DR", "Alicia", "PID-421387", "TORRES", "[PHI_", "REDACTED"]:
        assert forbidden not in output.masked_text
    assert "AGE / GENDER:" in output.masked_text
    assert "67 Yrs / Female" in output.masked_text
    assert "Glucose 120 mg/dL" in output.masked_text
    assert output.restoration_text != output.masked_text


def test_scans_require_independent_alignment_but_digital_pdf_does_not(monkeypatch):
    raw, result = clinical_template()
    processor = DocumentProcessor()
    png = processor.pdf.render(raw)[0].png()
    monkeypatch.setattr(OCRHandler, "tesseract_command", staticmethod(lambda: None))
    with pytest.raises(ValueError, match="Install Tesseract"):
        processor.analyze(png, "scan.png", FixtureDetector([result]))
    assert processor.mask(processor.analyze(raw, "report.pdf", FixtureDetector([result]))).masked


def test_wrong_transcribed_phi_is_not_shared_even_with_valid_ai_box():
    raw, result = clinical_template()
    result["personal_data"][0]["text"] = "Not on this report"
    with pytest.raises(ValueError, match="could not be aligned"):
        DocumentProcessor().analyze(raw, "report.pdf", FixtureDetector([result]))


@pytest.mark.parametrize("background", [(255, 255, 255), (239, 239, 239), (215, 230, 243)])
def test_background_fill_has_no_rectangular_patch_and_destroys_entire_field(background):
    original = Image.new("RGB", (600, 240), background)
    ImageDraw.Draw(original).text((70, 80), "PATIENT NAME: ALICE MORGAN", fill="black")
    page = RenderedPage(original, 600, 240)
    clean = OCRHandler().redact(page, [[300, 100, 450, 500]])
    expected = Image.new("RGB", original.size, background)
    assert ImageChops.difference(clean, expected).getbbox() is None
    assert original.getbbox() is not None  # The source object was never edited.


def test_textured_background_reconstruction_never_uses_original_phi_pixels():
    yy, xx = np.indices((240, 600))
    background = np.stack([180 + xx % 60, 190 + yy % 50, 195 + (xx + yy) % 40], axis=2).astype(np.uint8)
    first, second = Image.fromarray(background.copy()), Image.fromarray(background.copy())
    ImageDraw.Draw(first).rectangle((60, 70, 280, 115), fill="black")
    ImageDraw.Draw(second).rectangle((60, 70, 280, 115), fill=(245, 20, 90))
    handler = OCRHandler()
    box = [[250, 80, 550, 520]]
    one = np.array(handler.redact(RenderedPage(first, 600, 240), box))
    two = np.array(handler.redact(RenderedPage(second, 600, 240), box))
    assert np.array_equal(one, two)
    assert one[70:116, 60:281].min() > 100
    assert np.array_equal(one[150:], background[150:])


def test_public_text_removes_labels_without_touching_clinical_sections():
    text = "Patient Name: Alice\nPatient ID: PID-1\nReferring Dr: Dr. Torres\nFindings: glucose 120 mg/dL."
    entities = []
    for value in ["Alice", "PID-1", "Torres"]:
        start = text.index(value)
        entities.append(dict(start=start, end=start + len(value)))
    assert remove_text_fields(text, entities) == "Findings: glucose 120 mg/dL."


def test_older_tokenized_documents_still_restore(accounts):
    import base64
    import hashlib
    service, admin, doctor = accounts
    reference = "older-document"
    original = b"Patient: Alice\nFindings: glucose 120 mg/dL."
    tokenized, mapping = service.processor.engine.mask_text(original.decode(),
        [dict(text="Alice", start=9, end=14, label="PERSON")], reversible=True)
    service._vault.store(reference, original, mapping, ".txt", "older.txt", hashlib.sha256(tokenized.encode()).hexdigest())
    service._db.add_document(reference, tokenized, base64.b64encode(tokenized.encode()).decode(), ".txt", 1)
    service.return_report(doctor, reference, "Reviewed")
    restored = service.restore(admin, reference)
    assert restored["original"] == original
    assert restored["text"] == original.decode()
