"""Contracts and client documents: one chunk per clause (long clauses are split with overlap)."""
from app.engine.rag.chunking.base import RawChunk, split_text
from app.ingestion.models import Section


def chunk_sections(sections: list[Section]) -> list[RawChunk]:
    out: list[RawChunk] = []
    for s in sections:
        ref = f"clause {s.number}" if s.number else (s.heading or "preamble")
        body = s.full_text
        for i, piece in enumerate(split_text(body)):
            out.append(RawChunk(text=piece, section_ref=ref if i == 0 else f"{ref} (part {i + 1})", heading=s.heading, page=s.page, ord=len(out)))
    return out
