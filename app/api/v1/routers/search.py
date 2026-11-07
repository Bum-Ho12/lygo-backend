import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Factory, UserId, require, tenant_db
from app.core.errors import NotFound, ProblemError
from app.core.tenancy import TenantContext
from app.engine.rag.embeddings.base import Embedder
from app.engine.rag.embeddings.factory import get_embedder
from app.engine.rag.retrieval.hybrid import SCOPES, Hit, search

router = APIRouter(tags=["search"])
Emb = Annotated[Embedder | None, Depends(get_embedder)]


class CitationOut(BaseModel):
    chunk_id: uuid.UUID
    source_type: str
    source_id: uuid.UUID
    scope: str
    title: str
    section_ref: str
    page: int | None
    version: str
    effective_from: date | None


class HitOut(BaseModel):
    citation: CitationOut
    text: str
    score: float
    vector_rank: int | None
    text_rank: int | None


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    scopes: list[str] = Field(default_factory=lambda: sorted(SCOPES))
    matter_id: uuid.UUID | None = None
    as_at: date | None = None
    limit: int = Field(default=8, ge=1, le=25)


def _out(h: Hit) -> HitOut:
    c = h.citation
    return HitOut(
        citation=CitationOut(chunk_id=c.chunk_id, source_type=c.source_type, source_id=c.source_id, scope=c.scope, title=c.title,
                             section_ref=c.section_ref, page=c.page, version=c.version_label, effective_from=c.effective_from),
        text=h.text, score=h.score, vector_rank=h.vector_rank, text_rank=h.text_rank,
    )


def _need(e: Embedder | None) -> Embedder:
    if e is None:
        raise ProblemError(503, "search_unavailable", "Search unavailable", "No embedding model is configured.")
    return e


@router.get("/corpus/search", response_model=list[HitOut])
async def corpus_search(
    _: UserId, factory: Factory, embedder: Emb, q: Annotated[str, Query(min_length=1, max_length=1000)],
    as_at: date | None = None, limit: Annotated[int, Query(ge=1, le=25)] = 8,
) -> list[HitOut]:
    """Public law only; needs no tenant context (public rows are readable by every authenticated caller)."""
    async with factory() as db, db.begin():
        return [_out(h) for h in await search(db, _need(embedder), q, scopes={"public"}, limit=limit, as_at=as_at)]


@router.get("/corpus/documents/{doc_id}/sections/{ref:path}", response_model=list[HitOut])
async def corpus_section(doc_id: uuid.UUID, ref: str, _: UserId, factory: Factory) -> list[HitOut]:
    """Open a citation: the text of one section (e.g. `s.12`) of a corpus document."""
    async with factory() as db, db.begin():
        rows = (
            await db.execute(
                text(
                    "SELECT id, source_type, source_id, scope, title, section_ref, page, version_label, effective_from, text FROM chunks "
                    "WHERE source_id = :d AND scope = 'public' AND (section_ref = :r OR section_ref LIKE :rp) ORDER BY ord"
                ),
                {"d": doc_id, "r": ref, "rp": ref.replace("%", "") + " (%"},
            )
        ).all()
    if not rows:
        raise NotFound("section")
    return [
        HitOut(citation=CitationOut(chunk_id=r.id, source_type=r.source_type, source_id=r.source_id, scope=r.scope, title=r.title,
                                    section_ref=r.section_ref, page=r.page, version=r.version_label, effective_from=r.effective_from),
               text=r.text, score=1.0, vector_rank=None, text_rank=None)
        for r in rows
    ]


@router.post("/firms/{firm_id}/search", response_model=list[HitOut])
async def firm_search(
    body: SearchIn, db: Annotated[AsyncSession, Depends(tenant_db)], embedder: Emb,
    ctx: Annotated[TenantContext, Depends(require("ai.use"))],
) -> list[HitOut]:
    """Hybrid search over public law, firm knowledge and client documents. What a caller can actually see is decided
    by row-level security (clients: own documents + shared knowledge + public law), never by this filter."""
    unknown = set(body.scopes) - SCOPES
    if unknown:
        raise ProblemError(422, "invalid_scope", "Invalid scope", f"Unknown scopes: {sorted(unknown)}")
    hits = await search(db, _need(embedder), body.query, scopes=set(body.scopes), limit=body.limit, matter_id=body.matter_id, as_at=body.as_at)
    return [_out(h) for h in hits]
