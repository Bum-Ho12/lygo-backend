"""Plain text -> clause tree. Deterministic, regex based; OCR/PDF parsers feed it text.

Recognises: "1.", "1.1", "1.1.1 Heading", "Clause 7", "Section 3", "ARTICLE IV" and ALL-CAPS headings.
Text before the first heading becomes a section with an empty number (preamble).
"""
import re

from app.ingestion.models import DocumentText, Section

_NUMBERED = re.compile(r"^\s*(?:(?:clause|section|article)\s+)?(\d+(?:\.\d+)*)[.)]?\s+(.*\S)?\s*$", re.IGNORECASE)
_WORD_HEADING = re.compile(r"^\s*(clause|section|article)\s+([IVXLC]+|\d+)\s*[:.\-–]?\s*(.*)$", re.IGNORECASE)
_CAPS = re.compile(r"^[A-Z][A-Z0-9 ,&/'\-]{3,60}$")
_MAX_HEADING_LEN = 80


def _split_heading(rest: str) -> tuple[str, str]:
    """If the text after a number looks like a heading ('Termination. Either party...'), split it."""
    rest = rest.strip()
    m = re.match(r"^([A-Z][^.:\n]{0,60}?)[.:\-–]\s+(.+)$", rest)
    if m and len(m.group(1).split()) <= 8:
        return m.group(1).strip(), m.group(2).strip()
    if len(rest) <= _MAX_HEADING_LEN and not rest.endswith((".", ";", ",")):
        return rest, ""
    return "", rest


def parse_structure(text: str, doc_type: str | None = None) -> DocumentText:
    sections: list[Section] = []
    cur: Section | None = None
    body: list[str] = []

    def flush() -> None:
        nonlocal cur, body
        if cur is not None:
            cur.text = "\n".join(body).strip()
            if cur.number or cur.heading or cur.text:
                sections.append(cur)
        cur, body = None, []

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            body.append("")
            continue
        m = _NUMBERED.match(line)
        w = _WORD_HEADING.match(line)
        if m and (m.group(1) or "").strip():
            flush()
            number = m.group(1)
            heading, rest = _split_heading(m.group(2) or "")
            cur = Section(number=number, heading=heading)
            if rest:
                body.append(rest)
        elif w:
            flush()
            cur = Section(number=w.group(2).upper(), heading=w.group(3).strip())
        elif _CAPS.match(line.strip()) and not line.strip().isdigit():
            flush()
            cur = Section(number="", heading=line.strip().title())
        else:
            if cur is None:
                cur = Section()  # preamble
            body.append(line.strip())
    flush()

    _assign_paths(sections)
    return DocumentText(sections=sections, doc_type=doc_type)


def _assign_paths(sections: list[Section]) -> None:
    seen: dict[str, int] = {}
    for i, s in enumerate(sections):
        base = s.number or f"~{i}"
        parts = base.split(".")
        path = "/".join(".".join(parts[: j + 1]) for j in range(len(parts)))
        seen[path] = seen.get(path, 0) + 1
        s.path = path if seen[path] == 1 else f"{path}#{seen[path]}"
