"""Render visible pages and export a fresh PDF containing sanitized pixels only."""
from dataclasses import dataclass
import io
import fitz
from PIL import Image


@dataclass
class RenderedPage:
    image: Image.Image
    width: float
    height: float
    native_text: str = ""
    # Native text locations are normalized to the displayed, rotated page.
    words: list = None
    position_source: str = ""

    def png(self):
        buffer = io.BytesIO()
        self.image.save(buffer, format="PNG")
        return buffer.getvalue()


class PDFHandler:
    def render(self, source):
        pages = []
        with fitz.open(stream=source, filetype="pdf") as doc:
            if doc.needs_pass:
                raise ValueError("Unlock the PDF before processing it.")
            if not 1 <= len(doc) <= 30:
                raise ValueError("Process PDFs containing between 1 and 30 pages.")
            for page in doc:
                if page.rect.width * page.rect.height * 4 > 40_000_000:
                    raise ValueError("PDF page is too large to process safely.")
                pix = page.get_pixmap(dpi=144, colorspace=fitz.csRGB, alpha=False)
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                words = []
                for word in page.get_text("words"):
                    rect = fitz.Rect(word[:4]) * page.rotation_matrix
                    words.append((word[4], [rect.y0 / page.rect.height * 1000,
                                           rect.x0 / page.rect.width * 1000,
                                           rect.y1 / page.rect.height * 1000,
                                           rect.x1 / page.rect.width * 1000]))
                pages.append(RenderedPage(image, page.rect.width, page.rect.height, page.get_text(), words,
                                          "pdf" if words else ""))
        return pages

    def export(self, pages, sanitized_images):
        if len(pages) != len(sanitized_images):
            raise ValueError("Every source page must have a sanitized output.")
        with fitz.open() as doc:
            for source, image in zip(pages, sanitized_images):
                page = doc.new_page(width=source.width, height=source.height)
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                page.insert_image(page.rect, stream=buffer.getvalue())
            doc.set_metadata({})
            return doc.tobytes(garbage=4, deflate=True)
