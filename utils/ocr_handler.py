"""Flatten image metadata and permanently replace PHI pixels."""
import io
import math
import csv
import os
import shutil
import subprocess
from collections import Counter
import cv2
import numpy as np
from PIL import Image, ImageOps
from utils.pdf_handler import RenderedPage


def validate_box(box):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError("Every visual PHI detection needs a bounding box.")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
        raise ValueError("PHI coordinates must be finite numbers.")
    ymin, xmin, ymax, xmax = box
    if not (0 <= ymin < ymax <= 1000 and 0 <= xmin < xmax <= 1000):
        raise ValueError("PHI boxes must be ordered and normalized from 0 to 1000.")
    return list(box)


class OCRHandler:
    @staticmethod
    def tesseract_command():
        configured = os.getenv("TESSERACT_CMD")
        if configured:
            command = shutil.which(configured)
            if not command:
                raise ValueError("TESSERACT_CMD does not point to an available executable.")
            return command
        command = shutil.which("tesseract")
        if command:
            return command
        for path in [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]:
            if os.path.isfile(path):
                return path
        return None

    def locate_words(self, page):
        """Independent OCR positions correct approximate visual-model coordinates."""
        if page.words:
            return
        command = self.tesseract_command()
        if not command:
            raise ValueError("Install Tesseract OCR (or set TESSERACT_CMD) to align scanned PHI and its labels before sharing.")
        try:
            result = subprocess.run([command, "stdin", "stdout", "-l", os.getenv("DEID_OCR_LANG", "eng"),
                                     "--psm", "11", "tsv"], input=page.png(), capture_output=True,
                                    timeout=60, check=True)
        except (subprocess.SubprocessError, OSError) as exc:
            raise ValueError("Local OCR alignment failed. Check Tesseract and its language data; nothing was shared.") from exc
        words = []
        for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"):
            text = (row.get("text") or "").strip()
            if row.get("level") != "5" or not text:
                continue
            x, y, width, height = (int(row[k]) for k in ["left", "top", "width", "height"])
            if width > 0 and height > 0:
                words.append((text, [y / page.image.height * 1000, x / page.image.width * 1000,
                                     (y + height) / page.image.height * 1000, (x + width) / page.image.width * 1000]))
        page.words = words
        page.position_source = "ocr"

    def render(self, source):
        with Image.open(io.BytesIO(source)) as image:
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("Convert animated or multi-frame images to PDF first.")
            if image.width * image.height > 40_000_000:
                raise ValueError("Image is too large to process safely.")
            oriented = ImageOps.exif_transpose(image)
            # Compose transparency onto white, then create a pixel-only RGB image.
            rgba = oriented.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
        return RenderedPage(rgb, rgb.width, rgb.height, words=[])

    def redact(self, page, boxes):
        """Erase full fields into their own backgrounds; export contains no overlays."""
        pixels = np.array(page.image.convert("RGB"), copy=True)
        height, width = pixels.shape[:2]
        mask = np.zeros((height, width), dtype=np.uint8)
        rectangles = []
        for box in boxes:
            ymin, xmin, ymax, xmax = validate_box(box)
            x0, y0 = max(0, math.floor(xmin * width / 1000) - 3), max(0, math.floor(ymin * height / 1000) - 3)
            x1, y1 = min(width, math.ceil(xmax * width / 1000) + 3), min(height, math.ceil(ymax * height / 1000) + 3)
            rectangles.append((x0, y0, x1, y1))
            mask[y0:y1, x0:x1] = 255
        if not rectangles:
            return Image.fromarray(pixels)
        if np.all(mask):
            raise ValueError("The whole page is selected. Correct the PHI locations before sharing.")
        # Sample only outside ALL PHI regions, so original PHI cannot bleed back in.
        fills, complex_mask = [], np.zeros_like(mask)
        for x0, y0, x1, y1 in rectangles:
            margin = 8
            left, top, right, bottom = max(0, x0 - margin), max(0, y0 - margin), min(width, x1 + margin), min(height, y1 + margin)
            region_mask = mask[top:bottom, left:right]
            samples = pixels[top:bottom, left:right][region_mask == 0]
            if not len(samples):
                raise ValueError("No background is visible around a PHI field. Adjust its selection before sharing.")
            # A dominant local paper colour gives an exact fill on white/grey forms.
            quantized = (samples // 8).astype(np.int16)
            bins = quantized[:, 0] * 1024 + quantized[:, 1] * 32 + quantized[:, 2]
            mode, amount = Counter(bins.tolist()).most_common(1)[0]
            color = np.median(samples[bins == mode], axis=0).astype(np.uint8)
            fills.append(((x0, y0, x1, y1), color))
            if amount / len(samples) < .65:
                complex_mask[y0:y1, x0:x1] = 255
        # Destroy sensitive pixels before any reconstruction takes place.
        for (x0, y0, x1, y1), color in fills:
            pixels[y0:y1, x0:x1] = color
        if np.any(complex_mask):
            repaired = cv2.inpaint(pixels, mask, 3, cv2.INPAINT_TELEA)
            pixels[complex_mask != 0] = repaired[complex_mask != 0]
        return Image.fromarray(pixels)
