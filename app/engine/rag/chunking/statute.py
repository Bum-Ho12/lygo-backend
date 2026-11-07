"""Statutes: Part -> Section -> Subsection. Each section becomes a chunk keeping its number, title and Part.

Recognised layout (typical of Kenyan Acts):
    PART II - LICENSING
    12. Licence required.
    (1) A person shall not ...
    (2) ...
"""
import re

from app.engine.rag.chunking.base import RawChunk, split_text

_PART = re.compile(r"^\s*PART\s+([IVXLC]+|\d+)\b\s*[-–—:.]?\s*(.*)$", re.IGNORECASE)
_SECTION = re.compile(r"^\s*(\d+[A-Z]?)\.\s+(.+?)\s*$")
_SUB = re.compile(r"^\s*\((\d+|[a-z]|[ivx]+)\)\s*")


def chunk_statute(text: str) -> list[RawChunk]:
    out: list[RawChunk] = []
    part = ""
    cur_num = ""
    cur_title = ""
    cur_lines: list[str] = []

    def flush() -> None:
        nonlocal cur_num, cur_title, cur_lines
        if cur_num:
            body = "\n".join(cur_lines).strip()
            header = f"Section {cur_num}. {cur_title}".strip()
            prefix = f"[{part}] " if part else ""
            full = f"{prefix}{header}\n{body}".strip()
            for i, piece in enumerate(split_text(full)):
                ref = f"s.{cur_num}" if i == 0 else f"s.{cur_num} (part {i + 1})"
                out.append(RawChunk(text=piece, section_ref=ref, heading=cur_title, ord=len(out)))
        cur_num, cur_title, cur_lines = "", "", []

    for line in text.splitlines():
        if not line.strip():
            cur_lines.append("")
            continue
        pm = _PART.match(line)
        if pm and len(line) < 120:
            flush()
            part = f"Part {pm.group(1).upper()}" + (f" - {pm.group(2).strip()}" if pm.group(2).strip() else "")
            continue
        sm = _SECTION.match(line)
        if sm and not _SUB.match(line):
            title, _, rest = sm.group(2).partition(". ")
            flush()
            cur_num, cur_title = sm.group(1), title.rstrip(".")
            if rest:
                cur_lines.append(rest)
            continue
        cur_lines.append(line.strip())
    flush()
    return out
