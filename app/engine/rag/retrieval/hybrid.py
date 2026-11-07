"""Hybrid retrieval: pgvector cosine + Postgres full text, merged with reciprocal-rank fusion.

Tenant isolation is enforced by RLS on `chunks`, not by the filters below; the `scopes` filter only narrows
what the caller asked for.
"""
import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.rag.embeddings.base import Embedder
from app.engine.rag.indexer import vec_literal
from app.engine.rag.retrieval.fusion import rrf

CANDIDATES = 40
SCOPES = frozenset({"public", "firm_kb", "client"})


@dataclass(frozen=True)
class Citation:
    chunk_id: uuid.UUID
    source_type: str
    source_id: uuid.UUID
    scope: str
    title: str
    section_ref: str
    page: int | None
    version_label: str
    effective_from: date | None


@dataclass(frozen=True)
class Hit:
    citation: Citation
    text: str
    score: float
    vector_rank: int | None
    text_rank: int | None
    vector_score: float | None = None  # cosine similarity of the best vector match (None if only found by full text)


class Reranker(Protocol):
    async def rerank(self, query: str, hits: list[Hit]) -> list[Hit]: ...


_COLS = "id, source_type, source_id, scope, title, section_ref, page, version_label, effective_from, text"
_VCOLS = _COLS + ", 1 - (embedding <=> CAST(:q AS vector)) AS sim"
_FILTER = """
    scope = ANY(:scopes)
    AND (CAST(:matter_id AS uuid) IS NULL OR matter_id = CAST(:matter_id AS uuid) OR scope <> 'client')
    AND (effective_from IS NULL OR effective_from <= :as_at)
    AND (effective_to IS NULL OR effective_to > :as_at)
    AND (app_client_id() IS NULL OR scope <> 'client'
         OR EXISTS (SELECT 1 FROM documents d WHERE d.id = chunks.source_id AND d.status = 'processed'))
"""  # clients' search/assistant never draws on documents the firm hasn't finished (e.g. needing OCR review)
# SQL below is assembled only from the module-level constants above; every caller-supplied value is a bind parameter.
_VECTOR = text(f"SELECT {_VCOLS} FROM chunks WHERE {_FILTER} ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k")  # nosec B608
_FULLTEXT = text(
    f"SELECT {_COLS} FROM chunks, to_tsquery('english', :tsq) AS tq "  # nosec B608
    f"WHERE {_FILTER} AND tsv @@ tq ORDER BY ts_rank_cd(tsv, tq) DESC, id LIMIT :k"
)


def keywords(query: str, limit: int = 12) -> list[str]:
    """Distinct alphanumeric terms of >= 3 chars (stop words are dropped later by the text-search config)."""
    seen: dict[str, None] = {}
    for w in re.findall(r"[a-z0-9]+", query.lower()):
        if len(w) >= 3:
            seen.setdefault(w)
    return list(seen)[:limit]


def _hit_row(r: object) -> tuple[Citation, str]:
    m = r._mapping  # type: ignore[attr-defined]
    return (
        Citation(m["id"], m["source_type"], m["source_id"], m["scope"], m["title"], m["section_ref"], m["page"], m["version_label"], m["effective_from"]),
        m["text"],
    )


async def search(
    db: AsyncSession, embedder: Embedder, query: str, *, scopes: set[str] | frozenset[str] = SCOPES, limit: int = 8,
    matter_id: uuid.UUID | None = None, as_at: date | None = None, reranker: Reranker | None = None,
) -> list[Hit]:
    query = query.strip()
    if not query:
        return []
    scopes = set(scopes) & SCOPES
    if not scopes:
        return []
    qv = await embedder.embed_query(query)
    params = {"scopes": sorted(scopes), "matter_id": matter_id, "as_at": as_at or date.today(), "k": CANDIDATES}
    # pgvector >= 0.8: keep scanning the HNSW index when RLS/filters discard candidates (harmless placeholder GUC on older versions)
    await db.execute(text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)"))
    vec_rows = (await db.execute(_VECTOR, {**params, "q": vec_literal(qv)})).all()
    kws = keywords(query)  # OR semantics: natural-language questions rarely contain every word of a passage
    txt_rows = (await db.execute(_FULLTEXT, {**params, "tsq": " | ".join(kws)})).all() if kws else []

    by_id: dict[uuid.UUID, tuple[Citation, str]] = {}
    v_rank: dict[uuid.UUID, int] = {}
    v_sim: dict[uuid.UUID, float] = {}
    t_rank: dict[uuid.UUID, int] = {}
    for rank, r in enumerate(vec_rows, start=1):
        c, t = _hit_row(r)
        by_id[c.chunk_id], v_rank[c.chunk_id] = (c, t), rank
        v_sim[c.chunk_id] = float(r._mapping["sim"])
    for rank, r in enumerate(txt_rows, start=1):
        c, t = _hit_row(r)
        by_id[c.chunk_id], t_rank[c.chunk_id] = (c, t), rank
    fused = rrf([[_hit_row(r)[0].chunk_id for r in vec_rows], [_hit_row(r)[0].chunk_id for r in txt_rows]])
    hits = [
        Hit(by_id[cid][0], by_id[cid][1], round(score, 6), v_rank.get(cid), t_rank.get(cid), v_sim.get(cid))
        for cid, score in fused[:limit]
    ]
    return await reranker.rerank(query, hits) if reranker else hits
