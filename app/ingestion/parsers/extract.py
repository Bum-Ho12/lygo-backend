"""Raw text extraction: PDF text layer (OCR fallback per page), DOCX, images (OCR), plain text/CSV."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _roman(n: int) -> str:
    out = ""
    for v, s in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                 (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out, n = out + s, n - v
    return out


def _letters(n: int) -> str:
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(ord("a") + r) + out
    return out


def _fmt_number(n: int, fmt: str) -> str:
    if fmt in ("lowerRoman", "upperRoman"):
        s = _roman(n)
        return s.upper() if fmt == "upperRoman" else s
    if fmt in ("lowerLetter", "upperLetter"):
        s = _letters(n)
        return s.upper() if fmt == "upperLetter" else s
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt == "none":
        return ""
    return str(n)  # decimal and anything unrecognised


class _DocxNumbering:
    """Rebuilds Word's automatic list numbers, which are not stored in the paragraph text.

    Judgments are cited by paragraph number, so "1. The Plaintiff..." must keep its "1.". Handles numbering set
    directly on a paragraph or inherited from its style, multi-level lists (%1.%2), start values and
    level restarts. Lists sharing an abstract definition continue one sequence, as Word does.
    """

    def __init__(self, document: Any) -> None:
        self.levels: dict[str, dict[int, tuple[str, str, int]]] = {}  # abstractNumId -> ilvl -> (fmt, text, start)
        self.num_to_abs: dict[str, str] = {}
        self.overrides: dict[str, dict[int, int]] = {}  # numId -> ilvl -> startOverride
        self.counters: dict[str, list[int]] = {}
        self.started: set[str] = set()
        try:
            root = document.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            return  # no numbering part: nothing is auto-numbered
        for an in root.iter(f"{_W}abstractNum"):
            lv: dict[int, tuple[str, str, int]] = {}
            for lvl in an.iter(f"{_W}lvl"):
                fmt, text, start = lvl.find(f"{_W}numFmt"), lvl.find(f"{_W}lvlText"), lvl.find(f"{_W}start")
                lv[int(lvl.get(f"{_W}ilvl", "0"))] = (
                    fmt.get(f"{_W}val", "decimal") if fmt is not None else "decimal",
                    text.get(f"{_W}val", "") if text is not None else "",
                    int(start.get(f"{_W}val", "1")) if start is not None else 1,
                )
            self.levels[an.get(f"{_W}abstractNumId", "")] = lv
        for num in root.iter(f"{_W}num"):
            nid, absref = num.get(f"{_W}numId", ""), num.find(f"{_W}abstractNumId")
            if absref is None:
                continue
            self.num_to_abs[nid] = absref.get(f"{_W}val", "")
            for ov in num.iter(f"{_W}lvlOverride"):
                so = ov.find(f"{_W}startOverride")
                if so is not None:
                    self.overrides.setdefault(nid, {})[int(ov.get(f"{_W}ilvl", "0"))] = int(so.get(f"{_W}val", "1"))

    @staticmethod
    def _num_pr(paragraph: Any) -> tuple[str, int] | None:
        """(numId, ilvl) from the paragraph, else from its style chain."""
        candidates = [paragraph._p.pPr]
        style = paragraph.style
        while style is not None:
            candidates.append(style.element.pPr)
            style = style.base_style
        num_id: str | None = None
        ilvl: int | None = None
        for ppr in candidates:
            npr = ppr.find(f"{_W}numPr") if ppr is not None else None
            if npr is None:
                continue
            n, i = npr.find(f"{_W}numId"), npr.find(f"{_W}ilvl")
            if num_id is None and n is not None:
                num_id = n.get(f"{_W}val")
            if ilvl is None and i is not None:
                ilvl = int(i.get(f"{_W}val", "0"))
            if num_id is not None and ilvl is not None:
                break
        if not num_id or num_id == "0":  # numId 0 explicitly removes numbering
            return None
        return num_id, ilvl or 0

    def label(self, paragraph: Any) -> str:
        pr = self._num_pr(paragraph)
        if pr is None:
            return ""
        num_id, ilvl = pr
        abs_id = self.num_to_abs.get(num_id)
        levels = self.levels.get(abs_id or "")
        if not levels or ilvl not in levels:
            return ""
        fmt, text, _ = levels[ilvl]
        if fmt == "bullet":
            return "•"
        counts = self.counters.setdefault(abs_id or "", [0] * 9)
        if num_id not in self.started:  # first use of this list instance: apply start / startOverride
            self.started.add(num_id)
            for lv, (_, _, start) in levels.items():
                if lv < 9:
                    counts[lv] = self.overrides.get(num_id, {}).get(lv, start) - 1
        counts[ilvl] += 1
        for deeper in range(ilvl + 1, 9):  # a higher level restarts the levels below it
            counts[deeper] = levels[deeper][2] - 1 if deeper in levels else 0
        out = text
        for lv in range(ilvl + 1):
            lv_fmt = levels[lv][0] if lv in levels else "decimal"
            out = out.replace(f"%{lv + 1}", _fmt_number(max(counts[lv], 0), lv_fmt))
        return out.strip()


def _docx(path: Path) -> Extraction:
    import docx

    d = docx.Document(str(path))
    numbering = _DocxNumbering(d)
    lines = []
    for p in d.paragraphs:
        label = numbering.label(p)  # always advance: Word numbers empty list paragraphs too
        lines.append(f"{label} {p.text}" if label and p.text.strip() else p.text)
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
