"""Chunking primitives. ~800-token chunks with overlap as the fallback (docs/04); 1 token ~ 4 chars."""
import re
from dataclasses import dataclass

MAX_CHARS = 3200
OVERLAP_CHARS = 300
_SENT = re.compile(r"(?<=[.;:!?])\s+")


@dataclass
class RawChunk:
    text: str
    section_ref: str = ""
    heading: str = ""
    page: int | None = None
    ord: int = 0


def split_text(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Split on sentence boundaries into <= max_chars pieces, each carrying `overlap` chars from the previous."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    pieces: list[str] = []
    cur = ""
    for sent in _SENT.split(text):
        while len(sent) > max_chars:  # a single enormous sentence: hard split
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(sent[:max_chars])
            sent = sent[max_chars - overlap :]
        if cur and len(cur) + len(sent) + 1 > max_chars:
            pieces.append(cur)
            cur = cur[-overlap:].lstrip() + " " + sent if overlap else sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        pieces.append(cur)
    return pieces
