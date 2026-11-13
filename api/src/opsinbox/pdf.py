"""Every use of PDFium goes through here, under one lock.

PDFium (through pypdfium2) is not thread-safe: two threads rendering at once can crash the
process. The API serves requests from a thread pool and the evaluation reads attachments in
parallel, so all access is serialised. Rendering a page takes milliseconds; the lock costs little.
"""

import io
import threading

import pypdfium2
from PIL import Image

from opsinbox.errors import ReadingError

_LOCK = threading.Lock()


def render_pages(data: bytes, scale: float = 2.0) -> list[Image.Image]:
    """Every page as an RGB image.

    Raises:
        ReadingError: when the bytes are not a readable PDF.

    """
    with _LOCK:
        try:
            document = pypdfium2.PdfDocument(data)
        except pypdfium2.PdfiumError as exc:
            raise ReadingError(f"cannot read the PDF: {exc}") from exc
        try:
            return [page.render(scale=scale).to_pil().convert("RGB") for page in document]
        finally:
            document.close()


def render_page(data: bytes, page: int, scale: float = 1.6) -> Image.Image:
    """One page as an RGB image.

    Raises:
        ReadingError: when the PDF cannot be read.
        IndexError: when the page does not exist.

    """
    with _LOCK:
        try:
            document = pypdfium2.PdfDocument(data)
        except pypdfium2.PdfiumError as exc:
            raise ReadingError(f"cannot read the PDF: {exc}") from exc
        try:
            if not 0 <= page < len(document):
                raise IndexError(f"page {page} of {len(document)}")
            image: Image.Image = document[page].render(scale=scale).to_pil().convert("RGB")
            return image
        finally:
            document.close()


def page_png(data: bytes, page: int = 0, scale: float = 1.6) -> bytes:
    """One page as PNG bytes."""
    buffer = io.BytesIO()
    render_page(data, page, scale).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def page_count(data: bytes) -> int:
    """Number of pages, or 0 when the bytes are not a readable PDF."""
    with _LOCK:
        try:
            document = pypdfium2.PdfDocument(data)
        except pypdfium2.PdfiumError:
            return 0
        try:
            return len(document)
        finally:
            document.close()


def text_layer(data: bytes) -> str:
    """The embedded text of every page ("" for a scan or an unreadable file)."""
    with _LOCK:
        try:
            document = pypdfium2.PdfDocument(data)
        except pypdfium2.PdfiumError:
            return ""
        try:
            return "\n".join(
                page.get_textpage().get_text_range().replace("\r\n", "\n") for page in document
            )
        finally:
            document.close()
