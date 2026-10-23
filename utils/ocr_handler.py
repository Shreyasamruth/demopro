"""Flatten image metadata and permanently replace PHI pixels."""
import io
import math
from PIL import Image, ImageDraw, ImageOps
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
        clean = Image.new("RGB", page.image.size)
        clean.paste(page.image)
        draw = ImageDraw.Draw(clean)
        width, height = clean.size
        for box in boxes:
            ymin, xmin, ymax, xmax = validate_box(box)
            # Round outwards, with a small margin for anti-aliased glyph edges.
            rect = (max(0, math.floor(xmin * width / 1000) - 2),
                    max(0, math.floor(ymin * height / 1000) - 2),
                    min(width - 1, math.ceil(xmax * width / 1000) + 2),
                    min(height - 1, math.ceil(ymax * height / 1000) + 2))
            draw.rectangle(rect, fill="black")
        return clean
