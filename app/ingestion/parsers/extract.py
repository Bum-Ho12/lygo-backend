"""Raw text extraction: PDF text layer (OCR fallback per page), DOCX, images (OCR), plain text/CSV."""
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.ingestion.ocr.base import OcrEngine

MIN_TEXT_CHARS = 20  # fewer characters on a PDF page => treat it as scanned


@dataclass
class PageText:
    n: int
    text: str
    confidence: float | None = None  # None for text-layer pages
    needs_review: bool = False
    method: str = "text"  # text | ocr | none


@dataclass
class Extraction:
    pages: list[PageText] = field(default_factory=list)
    password_protected: bool = False
    notes: list[str] = field(default_factory=list)


def _ocr_page(ocr: OcrEngine, png: bytes, n: int, ex: Extraction) -> PageText:
    if not ocr.available:
        ex.notes.append("ocr_unavailable")
        return PageText(n, "", None, True, "none")
    r = ocr.recognize(png)
    return PageText(n, r.text, r.confidence, r.confidence < settings.ocr_review_threshold, "ocr")


def _pdf(path: Path, ocr: OcrEngine) -> Extraction:
    import pymupdf

    ex = Extraction()
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            ex.password_protected = True
            return ex
        for i, page in enumerate(doc, start=1):
            text = page.get_text().strip()
            if len(text) >= MIN_TEXT_CHARS:
                ex.pages.append(PageText(i, text))
            else:
                png = page.get_pixmap(dpi=200).tobytes("png")
                ex.pages.append(_ocr_page(ocr, png, i, ex))
    return ex


def _docx(path: Path) -> Extraction:
    import docx

    d = docx.Document(str(path))
    lines = [p.text for p in d.paragraphs]
    for table in d.tables:
        lines.extend(" | ".join(c.text.strip() for c in row.cells) for row in table.rows)
    return Extraction(pages=[PageText(1, "\n".join(lines).strip())])


def _text(path: Path) -> Extraction:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return Extraction(pages=[PageText(1, text.strip())])


def extract(kind: str, path: Path, ocr: OcrEngine) -> Extraction:
    if kind == "pdf":
        return _pdf(path, ocr)
    if kind == "docx":
        return _docx(path)
    if kind == "text":
        return _text(path)
    if kind == "image":
        ex = Extraction()
        ex.pages.append(_ocr_page(ocr, path.read_bytes(), 1, ex))
        return ex
    raise ValueError(f"unsupported kind {kind!r}")
