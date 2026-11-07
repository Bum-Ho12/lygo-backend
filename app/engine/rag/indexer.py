"""Write chunks (text + embedding) for documents and corpus sources. Embeddings go in via raw SQL (`::vector`)."""
import uuid
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.rag.chunking.base import RawChunk
from app.engine.rag.embeddings.base import Embedder, EmbeddingError

_INSERT = text(
    """
    INSERT INTO chunks (firm_id, client_id, matter_id, scope, shared_with_clients, source_type, source_id, title,
                        section_ref, heading, page, ord, text, embedding, embed_model, effective_from, effective_to, version_label)
    VALUES (:firm_id, :client_id, :matter_id, :scope, :shared, :source_type, :source_id, :title,
            :section_ref, :heading, :page, :ord, :text, CAST(:embedding AS vector), :embed_model, :eff_from, :eff_to, :version_label)
    """
)


def vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


async def replace_chunks(
    db: AsyncSession, embedder: Embedder, chunks: list[RawChunk], *, source_type: str, source_id: uuid.UUID, title: str,
    scope: str, firm_id: uuid.UUID | None, client_id: uuid.UUID | None = None, matter_id: uuid.UUID | None = None,
    shared_with_clients: bool = False, effective_from: date | None = None, effective_to: date | None = None, version_label: str = "",
) -> int:
    """Replace all chunks of one source atomically (idempotent re-index). Returns the number written."""
    vectors = await embedder.embed([c.text for c in chunks]) if chunks else []
    if len(vectors) != len(chunks) or any(len(v) != embedder.dim for v in vectors):
        raise EmbeddingError("embedder returned the wrong number or size of vectors")
    await db.execute(text("DELETE FROM chunks WHERE source_id = :s"), {"s": source_id})
    rows: list[dict[str, Any]] = [
        {
            "firm_id": firm_id, "client_id": client_id, "matter_id": matter_id, "scope": scope, "shared": shared_with_clients,
            "source_type": source_type, "source_id": source_id, "title": title[:300], "section_ref": c.section_ref[:100],
            "heading": c.heading[:300], "page": c.page, "ord": i, "text": c.text, "embedding": vec_literal(v),
            "embed_model": embedder.model, "eff_from": effective_from, "eff_to": effective_to, "version_label": version_label,
        }
        for i, (c, v) in enumerate(zip(chunks, vectors, strict=True))
    ]
    if rows:
        await db.execute(_INSERT, rows)
    return len(rows)
