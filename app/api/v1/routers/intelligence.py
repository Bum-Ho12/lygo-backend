"""Advocate-side intelligence tools: fact/cite check, matter chronology, case builder. Firm roles only (never the client portal)."""
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Factory, require, tenant_db
from app.api.v1.routers.ai import Emb, Llm, TurnOut, _need, _turn
from app.core.errors import NotFound
from app.core.pagination import Page, keyset_page
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.chat import Chat
from app.db.models.factcheck import FactCheck, FactCheckItem
from app.db.models.tenancy import Matter
from app.domain.matters.chronology import build_chronology
from app.domain.matters.service import get_or_404
from app.engine.factcheck.service import NOTICE, run_fact_check
from app.engine.orchestrator.chat import run_chat_turn

router = APIRouter(prefix="/firms/{firm_id}", tags=["intelligence"])
Db = Annotated[AsyncSession, Depends(tenant_db)]


class FactCheckIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    matter_id: uuid.UUID | None = Field(default=None, description="Required to check factual claims; without it only citations are checked")
    as_at: date | None = Field(default=None, description="Law in force on this date (default today)")


class ItemOut(BaseModel):
    kind: str
    text: str
    verdict: str
    detail: dict[str, Any]


class FactCheckOut(BaseModel):
    id: uuid.UUID
    matter_id: uuid.UUID | None
    as_at: str
    summary: dict[str, Any]
    items: list[ItemOut]
    model: str | None
    notice: str = NOTICE
    created_at: datetime


class FactCheckBrief(BaseModel):
    id: uuid.UUID
    matter_id: uuid.UUID | None
    summary: dict[str, Any]
    created_at: datetime


class CaseBuilderIn(BaseModel):
    objective: str = Field(min_length=3, max_length=4000, description="What the advocate is trying to establish or defend")


class EventOut(BaseModel):
    date: date
    kind: str
    title: str
    snippet: str
    document_id: uuid.UUID | None
    section_path: str
    page: int | None
    status: str


async def _load(db: AsyncSession, fc: FactCheck) -> FactCheckOut:
    items = (await db.execute(select(FactCheckItem).where(FactCheckItem.fact_check_id == fc.id).order_by(FactCheckItem.ord))).scalars()
    return FactCheckOut(id=fc.id, matter_id=fc.matter_id, as_at=fc.as_at, summary=fc.summary, model=fc.model, created_at=fc.created_at,
                        items=[ItemOut(kind=i.kind, text=i.content, verdict=i.verdict, detail=i.detail) for i in items])


@router.post("/fact-checks", status_code=201, response_model=FactCheckOut)
async def create_fact_check(body: FactCheckIn, factory: Factory, llm: Llm, emb: Emb, ctx: Annotated[TenantContext, Depends(require("factcheck.run"))]) -> FactCheckOut:
    """Cite-check (deterministic) + claim-check (model proposes, code verifies quotes verbatim). A draft aid, never a final opinion."""
    model, embedder = _need(llm, emb)
    fc_id = await run_fact_check(factory, model, embedder, ctx, body.text, matter_id=body.matter_id, as_at=body.as_at or datetime.now(UTC).date())
    async with tenant_session(factory, ctx) as db:
        fc = await db.get(FactCheck, fc_id)
        assert fc is not None
        return await _load(db, fc)


@router.get("/fact-checks", response_model=Page[FactCheckBrief])
async def list_fact_checks(db: Db, ctx: Annotated[TenantContext, Depends(require("factcheck.run"))], cursor: str | None = None, limit: Annotated[int | None, Query(ge=1, le=100)] = None) -> Page[FactCheckBrief]:
    rows, nxt = await keyset_page(db, select(FactCheck), FactCheck.id, cursor=cursor, limit=limit)
    return Page[FactCheckBrief](items=[FactCheckBrief(id=r.id, matter_id=r.matter_id, summary=r.summary, created_at=r.created_at) for r in rows], next_cursor=nxt)


@router.get("/fact-checks/{fact_check_id}", response_model=FactCheckOut)
async def get_fact_check(fact_check_id: uuid.UUID, db: Db, ctx: Annotated[TenantContext, Depends(require("factcheck.run"))]) -> FactCheckOut:
    fc = await get_or_404(db, FactCheck, fact_check_id, "fact check")
    return await _load(db, fc)


@router.get("/matters/{matter_id}/chronology", response_model=list[EventOut])
async def chronology(
    matter_id: uuid.UUID, db: Db, ctx: Annotated[TenantContext, Depends(require("facts.read"))],
    after: date | None = None, before: date | None = None,
) -> list[EventOut]:
    """Every dated event on the file (document dates, deadlines, matter opening) in order, each linked to its source."""
    matter = await get_or_404(db, Matter, matter_id, "matter")
    return [
        EventOut(date=e.date, kind=e.kind, title=e.title, snippet=e.snippet, document_id=e.document_id, section_path=e.section_path, page=e.page, status=e.status)
        for e in await build_chronology(db, matter)
        if (after is None or e.date >= after) and (before is None or e.date <= before)
    ]


@router.post("/matters/{matter_id}/case-builder", response_model=TurnOut)
async def case_builder(matter_id: uuid.UUID, body: CaseBuilderIn, factory: Factory, llm: Llm, emb: Emb, ctx: Annotated[TenantContext, Depends(require("drafts.create"))]) -> TurnOut:
    """A verified-citation case outline (issues, facts, law, arguments for/against, gaps). Advocate-only draft; kept as an advocate chat."""
    model, embedder = _need(llm, emb)
    assert ctx.firm_id
    async with tenant_session(factory, ctx) as db:
        if await db.get(Matter, matter_id) is None:
            raise NotFound("matter")
        chat = Chat(firm_id=ctx.firm_id, client_id=None, matter_id=matter_id, user_id=ctx.user_id, audience="advocate", title=f"Case outline: {body.objective[:60]}")
        db.add(chat)
        await db.flush()
        chat_id = chat.id
    return _turn(await run_chat_turn(factory, model, embedder, ctx, chat_id, body.objective, task="case_build"))
