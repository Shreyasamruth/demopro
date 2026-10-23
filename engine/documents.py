"""Page-aware analysis and irreversible public copies; originals stay private."""
from dataclasses import dataclass
import io
import re
from pathlib import Path
from engine.deid import DeidEngine
from utils.pdf_handler import PDFHandler
from utils.ocr_handler import OCRHandler, validate_box


@dataclass
class Analysis:
    original: bytes
    extension: str
    pages: list
    detections: list
    texts: list


@dataclass
class MaskedDocument:
    original: bytes
    masked: bytes
    extension: str
    masked_text: str
    mapping: dict
    phi_count: int
    previews: list


def matches(value, text):
    pattern = r"\s+".join(re.escape(p) for p in value.split())
    return list(re.finditer(pattern, text, re.I)) if pattern else []


class DocumentProcessor:
    def __init__(self):
        self.engine, self.pdf, self.ocr = DeidEngine(), PDFHandler(), OCRHandler()

    def analyze(self, original, filename, detector):
        extension = Path(filename).suffix.lower()
        if not original or len(original) > 25 * 1024 * 1024:
            raise ValueError("Upload a nonempty document of at most 25 MiB.")
        if extension == ".pdf":
            pages = self.pdf.render(original)
        elif extension in {".png", ".jpg", ".jpeg"}:
            pages = [self.ocr.render(original)]
        elif extension == ".txt":
            text = original.decode("utf-8")
            result = detector.run_deid(text)
            return Analysis(original, extension, [], [self._entities(result, visual=False)], [text])
        else:
            raise ValueError("Supported formats: PDF, PNG, JPG, JPEG, UTF-8 TXT.")
        detections, texts = [], []
        for page in pages:
            result = detector.run_multimodal_deid(page.png(), mime_type="image/png")
            if not isinstance(result.get("full_text"), str) or not result["full_text"].strip():
                raise ValueError("OCR returned no transcription. Nothing was shared.")
            # Do not publish native hidden PDF text. The export is visible pixels only.
            text = result["full_text"]
            detections.append(self._entities(result, visual=True))
            texts.append(text)
        return Analysis(original, extension, pages, detections, texts)

    @staticmethod
    def _entities(result, visual):
        if not isinstance(result, dict) or not isinstance(result.get("personal_data"), list):
            raise ValueError("PHI detection response is invalid.")
        entities = []
        for entity in result["personal_data"]:
            if not isinstance(entity, dict) or not isinstance(entity.get("text"), str) or not entity["text"].strip() or not isinstance(entity.get("label"), str) or not entity["label"].strip():
                raise ValueError("PHI detection is missing text or label.")
            clean = dict(text=entity["text"].strip(), label=entity["label"].strip())
            if visual:
                clean["box_2d"] = validate_box(entity.get("box_2d"))
            entities.append(clean)
        return entities

    @staticmethod
    def _native_boxes(page, value):
        # Match across line breaks using PDF word coordinates, not page-global boxes.
        words = page.words or []
        text, offsets = "", []
        for word, box in words:
            start = len(text)
            text += word + " "
            offsets.append((start, len(text) - 1, box))
        boxes = []
        for match in matches(value, text):
            for start, end, box in offsets:
                if start < match.end() and end > match.start():
                    boxes.append(box)
        return boxes

    def mask(self, analysis):
        expected_pages = len(analysis.pages) if analysis.pages else 1
        if len(analysis.texts) != expected_pages or len(analysis.detections) != expected_pages:
            raise ValueError("Every page must be analyzed before sharing.")
        combined, spans, clean_images, count = "", [], [], 0
        for index, (text, entities) in enumerate(zip(analysis.texts, analysis.detections)):
            page = analysis.pages[index] if analysis.pages else None
            boxes, offset = [], len(combined)
            all_entities = list(entities)
            # Regex supplement catches obvious IDs/contact fields omitted by the model.
            for entity in self.engine.detect_phi(text):
                covered = any(m.start() <= entity["start"] and m.end() >= entity["end"]
                              for e in entities for m in matches(e["text"], text))
                if not covered:
                    if page:
                        native_boxes = self._native_boxes(page, entity["text"])
                        if not native_boxes:
                            raise ValueError("A rule-detected PHI value has no image location. Correct its bounding box before sharing.")
                        boxes.extend(native_boxes)
                    all_entities.append(entity)
            for entity in all_entities:
                occurrences = matches(entity["text"], text)
                for match in occurrences:
                    spans.append(dict(text=match.group(), start=offset + match.start(), end=offset + match.end(), label=entity["label"]))
                if page:
                    native_boxes = self._native_boxes(page, entity["text"])
                    boxes.extend(native_boxes)
                    if "box_2d" in entity:
                        boxes.append(validate_box(entity["box_2d"]))
                    elif not native_boxes:
                        raise ValueError("Cannot locate a PHI value on the page. Nothing was shared.")
                    # For scans, repeated text needs a box for every occurrence.
                    if not native_boxes and occurrences:
                        same = {tuple(e["box_2d"]) for e in entities if e["text"].casefold() == entity["text"].casefold() and "box_2d" in e}
                        if len(same) < len(occurrences):
                            raise ValueError("Repeated PHI needs a separate bounding box for every occurrence.")
                elif not occurrences:
                    raise ValueError("A detected PHI value was not found in the text.")
            count += len(all_entities)
            combined += text + ("\n\f\n" if index < len(analysis.texts) - 1 else "")
            if page:
                clean_images.append(self.ocr.redact(page, boxes))
        masked_text, mapping = self.engine.mask_text(combined, spans, reversible=True)
        if analysis.extension == ".pdf":
            output, extension = self.pdf.export(analysis.pages, clean_images), ".pdf"
        elif analysis.pages:
            buffer = io.BytesIO()
            clean_images[0].save(buffer, format="PNG")
            output, extension = buffer.getvalue(), ".png"
        else:
            output, extension = masked_text.encode("utf-8"), ".txt"
        return MaskedDocument(analysis.original, output, extension, masked_text, mapping, count, clean_images)
