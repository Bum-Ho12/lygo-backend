import asyncio
import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Factory, require, tenant_db
from app.core.errors import Forbidden, NotFound, ProblemError
from app.core.pagination import Page, keyset_page
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.chat import Chat, MatterFact, Message
from app.db.models.documents import Document
from app.db.models.rag import Chunk
from app.db.models.tenancy import Firm, Matter
from app.domain.audit import service as audit
from app.domain.matters.service import get_or_404
from app.engine.ai.guardrails.advice import DISCLAIMER
from app.engine.ai.guardrails.citations import Source
from app.engine.ai.providers.base import LlmError, LLMProvider
from app.engine.ai.providers.factory import get_llm
from app.engine.ai.settings import AiSettings
from app.engine.orchestrator.chat import MAX_SOURCE_CHARS, TurnResult, run_chat_turn
from app.engine.orchestrator.generate import generate_verified
from app.engine.rag.embeddings.base import Embedder
from app.engine.rag.embeddings.factory import get_embedder

router = APIRouter(prefix="/firms/{firm_id}", tags=["ai"])
Db = Annotated[AsyncSession, Depends(tenant_db)]
Llm = Annotated[LLMProvider | None, Depends(get_llm)]
Emb = Annotated[Embedder | None, Depends(get_embedder)]
UseAi = Annotated[TenantContext, Depends(require("ai.use"))]


def _need(llm: LLMProvider | None, emb: Embedder | None) -> tuple[LLMProvider, Embedder]:
    if llm is None or emb is None:
        raise ProblemError(503, "ai_unavailable", "AI unavailable", "No language or embedding model is configured.")
    return llm, emb


class ChatIn(BaseModel):
    matter_id: uuid.UUID | None = None
    title: str = Field(default="", max_length=200)


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    status: str
    approval_status: str
    citations: list[dict[str, Any]]
    prompt_version: str | None
    created_at: datetime


class ChatOut(BaseModel):
    id: uuid.UUID
    audience: str
    matter_id: uuid.UUID | None
    title: str
    created_at: datetime


class ChatDetail(ChatOut):
    messages: list[MessageOut]


class TurnOut(BaseModel):
    chat_id: uuid.UUID
    message_id: uuid.UUID
    status: str
    approval_status: str
    content: str | None
    citations: list[dict[str, Any]]


class ReviewIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=2000)
    edited_content: str | None = Field(default=None, max_length=8000)


class AiSettingsPatch(BaseModel):
    client_chatbot_enabled: bool | None = None
    require_approval_for_client: bool | None = None
    reading_level: Literal["simple", "standard", "technical"] | None = None
    firm_prompt: str | None = Field(default=None, max_length=1000)


class FactIn(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)
    confirmed: bool = False


class FactPatch(BaseModel):
    value: str | None = Field(default=None, min_length=1, max_length=2000)
    status: Literal["unconfirmed", "confirmed"] | None = None


class FactOut(BaseModel):
    id: uuid.UUID
    key: str
    value: str
    status: str
    source: str


class ResearchIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


def _turn(r: TurnResult) -> TurnOut:
    return TurnOut(chat_id=r.chat_id, message_id=r.message_id, status=r.status, approval_status=r.approval_status, content=r.content, citations=r.citations)


def _msg(m: Message) -> MessageOut:
    return MessageOut(id=m.id, role=m.role, content=m.content, status=m.status, approval_status=m.approval_status,
                      citations=m.citations, prompt_version=m.prompt_version, created_at=m.created_at)


# --- chats -------------------------------------------------------------------------------------------------
@router.post("/chats", status_code=201, response_model=ChatOut)
async def create_chat(body: ChatIn, db: Db, ctx: UseAi) -> ChatOut:
    assert ctx.firm_id
    audience = "client" if ctx.client_id else "advocate"
    if audience == "client":
        firm = await db.get(Firm, ctx.firm_id)
        if not AiSettings.from_firm(firm.ai_settings if firm else None).client_chatbot_enabled:
            raise Forbidden("The client assistant is not enabled for this firm.")
    if body.matter_id is not None:
        await get_or_404(db, Matter, body.matter_id, "matter")
    chat = Chat(firm_id=ctx.firm_id, client_id=ctx.client_id, matter_id=body.matter_id, user_id=ctx.user_id, audience=audience, title=body.title)
    db.add(chat)
    await db.flush()
    await audit.record(db, ctx, "chat.create", target=f"chat:{chat.id}", meta={"audience": audience})
    return ChatOut(id=chat.id, audience=chat.audience, matter_id=chat.matter_id, title=chat.title, created_at=chat.created_at)


@router.get("/chats", response_model=Page[ChatOut])
async def list_chats(db: Db, ctx: UseAi, cursor: str | None = None, limit: Annotated[int | None, Query(ge=1, le=200)] = None) -> Page[ChatOut]:
    stmt = select(Chat)
    if ctx.client_id is not None:
        stmt = stmt.where(Chat.user_id == ctx.user_id)
    rows, nxt = await keyset_page(db, stmt, Chat.id, cursor=cursor, limit=limit)
    return Page[ChatOut](items=[ChatOut(id=c.id, audience=c.audience, matter_id=c.matter_id, title=c.title, created_at=c.created_at) for c in rows], next_cursor=nxt)


@router.get("/chats/{chat_id}", response_model=ChatDetail)
async def get_chat(chat_id: uuid.UUID, db: Db, ctx: UseAi) -> ChatDetail:
    c = await get_or_404(db, Chat, chat_id, "chat")
    if ctx.client_id is not None and c.user_id != ctx.user_id:
        raise NotFound("chat")
    msgs = (await db.execute(select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at, Message.id))).scalars()
    return ChatDetail(id=c.id, audience=c.audience, matter_id=c.matter_id, title=c.title, created_at=c.created_at, messages=[_msg(m) for m in msgs])


@router.post("/chats/{chat_id}/messages", response_model=TurnOut)
async def post_message(chat_id: uuid.UUID, body: MessageIn, request: Request, factory: Factory, llm: Llm, emb: Emb, ctx: UseAi):  # type: ignore[no-untyped-def]
    """Ask a question. Answers are fully verified before release, so the stream carries progress events and then the
    finished message (not raw tokens): first-token latency is traded for guaranteed grounding."""
    model, embedder = _need(llm, emb)
    if "text/event-stream" not in request.headers.get("accept", ""):
        return _turn(await run_chat_turn(factory, model, embedder, ctx, chat_id, body.content))

    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    async def on_phase(p: str) -> None:
        await queue.put(("status", {"phase": p}))

    job = asyncio.create_task(run_chat_turn(factory, model, embedder, ctx, chat_id, body.content, on_phase=on_phase))

    async def stream():  # type: ignore[no-untyped-def]
        while not (job.done() and queue.empty()):
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
            except TimeoutError:
                continue
        try:
            yield f"event: message\ndata: {_turn(job.result()).model_dump_json()}\n\n"
        except ProblemError as exc:
            yield f"event: error\ndata: {json.dumps({'status': exc.status, 'code': exc.code, 'detail': exc.detail})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# --- advocate review (approval mode) ----------------------------------------------------------------------
@router.get("/ai-outputs")
async def pending_outputs(db: Db, ctx: Annotated[TenantContext, Depends(require("ai.approve"))], status: Literal["pending"] = "pending") -> dict[str, list[dict[str, Any]]]:
    rows = (await db.execute(select(Message).where(Message.role == "assistant", Message.approval_status == status).order_by(Message.created_at).limit(50))).scalars().all()
    items = []
    for m in rows:
        q = (await db.execute(select(Message.content).where(Message.chat_id == m.chat_id, Message.role == "user", Message.created_at <= m.created_at).order_by(Message.created_at.desc()).limit(1))).scalar_one_or_none()
        items.append({"message_id": str(m.id), "chat_id": str(m.chat_id), "question": q or "", "answer": m.content, "citations": m.citations, "created_at": m.created_at.isoformat()})
    return {"items": items}


@router.post("/ai-outputs/{message_id}:approve")
async def review_output(message_id: uuid.UUID, body: ReviewIn, db: Db, ctx: Annotated[TenantContext, Depends(require("ai.approve"))]) -> dict[str, str]:
    m = await get_or_404(db, Message, message_id, "message")
    if m.role != "assistant" or m.approval_status != "pending":
        raise ProblemError(409, "not_pending", "Not pending", "Only answers awaiting review can be approved or rejected.")
    if body.approve:
        if body.edited_content is not None and body.edited_content.strip() != m.content:
            m.meta = {**m.meta, "original_content": m.content, "edited_by_advocate": True}
            m.content = body.edited_content.strip()
        m.approval_status = "approved"
    else:
        m.approval_status = "rejected"
    m.approved_by, m.approved_at, m.reviewer_note = ctx.user_id, datetime.now().astimezone(), body.note.strip()
    await audit.record(db, ctx, "ai.review", target=f"message:{m.id}", meta={"approved": body.approve, "edited": bool(m.meta.get("edited_by_advocate"))})
    return {"message_id": str(m.id), "approval_status": m.approval_status}


# --- firm AI settings -------------------------------------------------------------------------------------------
@router.get("/ai-settings")
async def get_ai_settings(db: Db, ctx: Annotated[TenantContext, Depends(require("firm.manage"))]) -> dict[str, Any]:
    firm = await db.get(Firm, ctx.firm_id)
    return AiSettings.from_firm(firm.ai_settings if firm else None).model_dump()


@router.patch("/ai-settings")
async def patch_ai_settings(body: AiSettingsPatch, db: Db, ctx: Annotated[TenantContext, Depends(require("firm.manage"))]) -> dict[str, Any]:
    firm = await db.get(Firm, ctx.firm_id)
    assert firm is not None
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    merged = AiSettings.from_firm({**(firm.ai_settings or {}), **changes})
    firm.ai_settings = {**(firm.ai_settings or {}), **merged.model_dump()}
    await audit.record(db, ctx, "ai.settings", target=f"firm:{firm.id}", meta={"fields": sorted(changes)})  # values (e.g. prompt text) are not logged
    return merged.model_dump()


# --- advocate tools -----------------------------------------------------------------------------------------------
@router.post("/matters/{matter_id}/research", response_model=TurnOut)
async def research(matter_id: uuid.UUID, body: ResearchIn, factory: Factory, llm: Llm, emb: Emb, ctx: Annotated[TenantContext, Depends(require("research.run"))]) -> TurnOut:
    """Verified-citation research on a matter (docs/02 flow 3). Output is a draft for advocate review."""
    model, embedder = _need(llm, emb)
    assert ctx.firm_id
    async with tenant_session(factory, ctx) as db:
        await get_or_404(db, Matter, matter_id, "matter")
        chat = Chat(firm_id=ctx.firm_id, client_id=None, matter_id=matter_id, user_id=ctx.user_id, audience="advocate", title=body.question[:80])
        db.add(chat)
        await db.flush()
        chat_id = chat.id
    return _turn(await run_chat_turn(factory, model, embedder, ctx, chat_id, body.question, task="research"))


@router.post("/documents/{doc_id}/summary")
async def summarise_document(doc_id: uuid.UUID, factory: Factory, llm: Llm, ctx: Annotated[TenantContext, Depends(require("research.run"))]) -> dict[str, Any]:
    if llm is None:
        raise ProblemError(503, "ai_unavailable", "AI unavailable", "No language model is configured.")
    async with tenant_session(factory, ctx) as db:
        doc = await get_or_404(db, Document, doc_id, "document")
        firm = await db.get(Firm, ctx.firm_id)
        ai = AiSettings.from_firm(firm.ai_settings if firm else None)
        chunks = (await db.execute(select(Chunk).where(Chunk.source_id == doc.id).order_by(Chunk.ord).limit(6))).scalars().all()
        title = doc.title
    if not chunks:
        raise ProblemError(409, "not_indexed", "Document not indexed", "The document has no searchable text yet.")
    sources = [Source(i, str(c.id), title, c.section_ref, c.text[:MAX_SOURCE_CHARS]) for i, c in enumerate(chunks, start=1)]
    try:
        gen = await generate_verified(llm, "summarise", "advocate", f"Summarise the document '{title}'.", sources, ai)
    except LlmError as exc:
        raise ProblemError(503, "ai_unavailable", "AI unavailable", "The assistant is temporarily unavailable.") from exc
    async with tenant_session(factory, ctx) as db:
        await audit.record(db, ctx, "ai.summary", target=f"document:{doc_id}", meta={"status": gen.status, "model": gen.model})
    cites = [{"marker": n, "section_ref": sources[n - 1].section_ref, "chunk_id": sources[n - 1].chunk_id} for n in gen.used]
    return {"status": gen.status, "summary": gen.text if gen.status == "ok" else None, "citations": cites, "notice": "Draft for advocate review. " + DISCLAIMER.split(",")[0]}


# --- matter facts (memory) ------------------------------------------------------------------------------------------
@router.get("/matters/{matter_id}/facts", response_model=list[FactOut])
async def list_facts(matter_id: uuid.UUID, db: Db, ctx: Annotated[TenantContext, Depends(require("facts.read"))]) -> list[FactOut]:
    await get_or_404(db, Matter, matter_id, "matter")
    rows = (await db.execute(select(MatterFact).where(MatterFact.matter_id == matter_id).order_by(MatterFact.key, MatterFact.id))).scalars()
    return [FactOut(id=f.id, key=f.key, value=f.value, status=f.status, source=f.source) for f in rows]


@router.post("/matters/{matter_id}/facts", status_code=201, response_model=FactOut)
async def add_fact(matter_id: uuid.UUID, body: FactIn, db: Db, ctx: Annotated[TenantContext, Depends(require("facts.propose"))]) -> FactOut:
    from app.core.permissions import has_permission

    assert ctx.firm_id
    m = await get_or_404(db, Matter, matter_id, "matter")
    confirmed = body.confirmed and has_permission(ctx.role, "facts.manage")
    if body.confirmed and not confirmed:
        raise Forbidden("Only advocates can confirm facts.")
    f = MatterFact(firm_id=ctx.firm_id, client_id=m.client_id, matter_id=m.id, key=body.key, value=body.value,
                   status="confirmed" if confirmed else "unconfirmed", created_by=ctx.user_id, confirmed_by=ctx.user_id if confirmed else None)
    db.add(f)
    await db.flush()
    await audit.record(db, ctx, "fact.create", target=f"fact:{f.id}")
    return FactOut(id=f.id, key=f.key, value=f.value, status=f.status, source=f.source)


@router.patch("/matters/{matter_id}/facts/{fact_id}", response_model=FactOut)
async def patch_fact(matter_id: uuid.UUID, fact_id: uuid.UUID, body: FactPatch, db: Db, ctx: Annotated[TenantContext, Depends(require("facts.manage"))]) -> FactOut:
    f = await get_or_404(db, MatterFact, fact_id, "fact")
    if f.matter_id != matter_id:
        raise NotFound("fact")
    if body.value is not None:
        f.value = body.value
        f.status, f.confirmed_by = "unconfirmed", None  # an edited fact must be re-confirmed
    if body.status is not None:
        f.status, f.confirmed_by = body.status, (ctx.user_id if body.status == "confirmed" else None)
    await audit.record(db, ctx, "fact.update", target=f"fact:{f.id}", meta={"status": f.status})
    return FactOut(id=f.id, key=f.key, value=f.value, status=f.status, source=f.source)
