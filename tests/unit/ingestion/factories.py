"""Synthetic documents for tests (no real client data)."""
import io
import struct

import docx
import pymupdf
from PIL import Image

from app.ingestion.ocr.base import OcrResult


def png_bytes(color: str = "white", size: tuple[int, int] = (200, 100)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def pdf_text(*pages: str) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 550, 800), text, fontsize=10)
    out = doc.tobytes()
    doc.close()
    return out


def pdf_scanned(n: int = 1) -> bytes:
    """Pages containing only an image (no text layer), like a scan."""
    doc = pymupdf.open()
    for _ in range(n):
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(40, 40, 400, 240), stream=png_bytes("gray", (400, 200)))
    out = doc.tobytes()
    doc.close()
    return out


def pdf_encrypted(text: str = "secret content that is long enough") -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), text)
    out = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    doc.close()
    return out


def docx_bytes(*paragraphs: str) -> bytes:
    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


class FakeOcr:
    def __init__(self, text: str = "scanned text from the page", confidence: float = 0.95, available: bool = True) -> None:
        self.text, self.confidence, self.available = text, confidence, available
        self.calls = 0

    def recognize(self, image_png: bytes) -> OcrResult:
        self.calls += 1
        return OcrResult(self.text, self.confidence)


def clamd_reply(reply: bytes):  # type: ignore[no-untyped-def]
    """Handler for a fake clamd that checks the INSTREAM framing, then answers `reply`."""

    async def handle(reader, writer):  # type: ignore[no-untyped-def]
        assert await reader.readexactly(10) == b"zINSTREAM\0"
        received = b""
        while True:
            (n,) = struct.unpack("!I", await reader.readexactly(4))
            if n == 0:
                break
            received += await reader.readexactly(n)
        handle.received = received  # type: ignore[attr-defined]
        writer.write(reply + b"\0")
        await writer.drain()
        writer.close()

    return handle
