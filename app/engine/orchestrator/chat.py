"""Chat turn orchestration (docs/02 flows 2 & 3). Transaction layout:

  A. caller's own tenant context : validate chat, store the question, retrieve (row-level security limits what is visible)
  -- no database connection held while the LLM runs --
  B. firm worker context         : store the assistant reply (+ escalation task), because a client user may not read
                                   a reply that is still awaiting advocate approval.
"""
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import Forbidden, NotFound, ProblemError
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.chat import Chat, MatterFact, Message
from app.db.models.tenancy import Firm
from app.db.models.work import Task
from app.domain.audit import service as audit
from app.engine.ai.guardrails.advice import DISCLAIMER, ESCALATION, check_question
from app.engine.ai.guardrails.citations import Source
from app.engine.ai.guardrails.grounding import ABSTAIN_MESSAGE, is_grounded
from app.engine.ai.providers.base import LlmError, LLMProvider
from app.engine.ai.settings import AiSettings
from app.engine.memory import conversation
from app.engine.orchestrator.generate import generate_verified
from app.engine.rag.embeddings.base import Embedder, EmbeddingError
from app.engine.rag.retrieval.hybrid import Hit, search

MAX_QUESTION_CHARS = 4000
MAX_SOURCES = 6
SOURCE_LIMIT = {"case_build": 10}  # building a case needs more material than answering a question
MAX_SOURCE_CHARS = 2400
Phase = Callable[[str], Awaitable[None]]


@dataclass
class TurnResult:
    chat_id: uuid.UUID
    message_id: uuid.UUID
    status: str  # ok | abstained | deflected
    approval_status: str
    content: str | None  # None while a client's answer awaits advocate approval
    citations: list[dict[str, Any]] = field(default_factory=list)


def sources_from_hits(hits: list[Hit], limit: int = MAX_SOURCES) -> list[Source]:
    return [
        Source(i, str(h.citation.chunk_id), h.citation.title, h.citation.section_ref, h.text[:MAX_SOURCE_CHARS])
        for i, h in enumerate(hits[:limit], start=1)
    ]


def citation_objects(sources: list[Source], hits: list[Hit], used: list[int]) -> list[dict[str, Any]]:
    out = []
    for n in used:
        c = hits[n - 1].citation
        out.append({
            "marker": n, "chunk_id": str(c.chunk_id), "source_type": c.source_type, "source_id": str(c.source_id), "scope": c.scope,
            "title": c.title, "section_ref": c.section_ref, "version": c.version_label, "verified": True,
        })
    return out


async def _monthly_usage(factory: async_sessionmaker[AsyncSession], firm_id: uuid.UUID) -> int:
    ctx = TenantContext(firm_id=firm_id, user_id=uuid.UUID(int=0), role="worker")
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with tenant_session(factory, ctx) as db:
        return int((await db.execute(select(func.count()).select_from(Message).where(Message.role == "assistant", Message.created_at >= start))).scalar_one())


async def run_chat_turn(
    factory: async_sessionmaker[AsyncSession], llm: LLMProvider, embedder: Embedder, ctx: TenantContext, chat_id: uuid.UUID, question: str,
    *, task: str | None = None, on_phase: Phase | None = None,
) -> TurnResult:
    assert ctx.firm_id
    question = question.strip()
    if not question or len(question) > MAX_QUESTION_CHARS:
        raise ProblemError(422, "invalid_question", "Invalid question", f"The question must be 1-{MAX_QUESTION_CHARS} characters.")
    phase = on_phase or _noop
    if await _monthly_usage(factory, ctx.firm_id) >= settings.ai_monthly_message_cap:
        raise ProblemError(429, "ai_quota_exceeded", "AI quota exceeded", "This firm has reached its monthly AI message limit.")

    await phase("retrieving")
    async with tenant_session(factory, ctx) as db:
        chat = await db.get(Chat, chat_id)
        if chat is None:
            raise NotFound("chat")
        firm = await db.get(Firm, ctx.firm_id)
        ai = AiSettings.from_firm(firm.ai_settings if firm else None)
        audience = chat.audience
        if audience == "client" and not ai.client_chatbot_enabled:
            raise Forbidden("The client assistant is not enabled for this firm.")
        task = task or ("explain" if audience == "client" else "research")
        client_id, matter_id, chat_user, summary_text = chat.client_id, chat.matter_id, chat.user_id, chat.summary
        if chat_user != ctx.user_id and ctx.client_id is not None:
            raise NotFound("chat")  # a client user may only continue their own conversations
        rows = (await db.execute(select(Message.role, Message.content, Message.approval_status).where(Message.chat_id == chat_id).order_by(Message.created_at, Message.id))).all()
        history_rows = [(r, c) for r, c, a in rows if a != "rejected"]
        db.add(Message(firm_id=ctx.firm_id, client_id=client_id, chat_id=chat_id, role="user", content=question, task=task))
        await db.flush()
        facts: list[str] = []
        if audience == "advocate" and matter_id:
            facts = [f"{f.key}: {f.value}" for f in (await db.execute(select(MatterFact).where(MatterFact.matter_id == matter_id, MatterFact.status == "confirmed"))).scalars()]
        try:
            hits = await search(db, embedder, question, scopes={"public", "firm_kb", "client"}, limit=SOURCE_LIMIT.get(task, MAX_SOURCES), matter_id=matter_id)
        except EmbeddingError as exc:
            raise ProblemError(503, "search_unavailable", "Search unavailable", "The search service is temporarily unavailable.") from exc
        total_before = len(rows) + 1

    advice = check_question(question) if audience == "client" else None
    grounded = is_grounded(hits, question)
    sources = sources_from_hits(hits, SOURCE_LIMIT.get(task, MAX_SOURCES)) if grounded else []
    await phase("drafting")
    extra = (
        "The user is asking for a decision, a prediction or a document. Explain ONLY the general legal position found in the sources. "
        "Do not recommend any action, do not predict an outcome, and do not draft any document."
        if advice and advice.advice_seeking else ""
    )
    try:
        gen = await generate_verified(
            llm, task, audience, question, sources, ai, history=conversation.to_history(history_rows), facts=facts, summary=summary_text, extra_instruction=extra
        )
    except LlmError as exc:
        raise ProblemError(503, "ai_unavailable", "AI unavailable", "The assistant is temporarily unavailable. Please try again.") from exc

    await phase("verifying")
    deflect = bool(advice and advice.advice_seeking)
    if gen.status == "ok":
        content = gen.text
        if audience == "client":
            content = f"{content}\n\n{DISCLAIMER}" + (f"\n\n{ESCALATION}" if deflect else "")
        status = "deflected" if deflect else "ok"
        citations = citation_objects(sources, hits, gen.used)
    else:
        content = ABSTAIN_MESSAGE + (f" {ESCALATION}" if deflect else "")
        status, citations = ("deflected" if deflect else "abstained"), []
    needs_review = audience == "client" and ai.require_approval_for_client and gen.status == "ok"
    approval = "pending" if needs_review else "not_required"
    meta = {
        "attempts": gen.attempts, "abstain_reason": gen.abstain_reason, "removed": [{"reason": r.reason, "sentence": r.sentence[:300]} for r in gen.removed],
        "directives_removed": gen.directives_removed, "injection_suspected": gen.injection_hits > 0, "advice_category": advice.category if advice else "",
        "prompt_tokens": gen.prompt_tokens, "completion_tokens": gen.completion_tokens, "sources": len(sources), "grounded": grounded,
    }
    worker = TenantContext(firm_id=ctx.firm_id, user_id=ctx.user_id, role="worker")
    msg_id = uuid.uuid4()
    async with tenant_session(factory, worker) as db:
        db.add(Message(
            id=msg_id, firm_id=ctx.firm_id, client_id=client_id, chat_id=chat_id, role="assistant", content=content, task=task, status=status,
            approval_status=approval, prompt_version=gen.prompt_version or None, model=gen.model or llm.model, citations=citations, meta=meta,
        ))
        if deflect:  # route the question to the firm: an advocate task (docs/04 guardrail 2)
            db.add(Task(firm_id=ctx.firm_id, client_id=client_id, matter_id=matter_id, title="Client asked for advice via the assistant",
                        description=question[:1000], kind="escalation", created_by=ctx.user_id))
        await audit.record(db, worker, "ai.answer", target=f"message:{msg_id}", meta={"status": status, "approval": approval, "model": gen.model})
        await db.flush()
    await _maybe_summarise(factory, llm, ctx, chat_id, total_before + 1)
    visible = None if (ctx.client_id is not None and approval == "pending") else content
    return TurnResult(chat_id, msg_id, status, approval, visible, [] if visible is None else citations)


async def _noop(_: str) -> None: ...


async def _maybe_summarise(factory: async_sessionmaker[AsyncSession], llm: LLMProvider, ctx: TenantContext, chat_id: uuid.UUID, total: int) -> None:
    """Best effort: a failed summary must never fail the user's turn."""
    worker = TenantContext(firm_id=ctx.firm_id, user_id=ctx.user_id, role="worker")
    try:
        async with tenant_session(factory, worker) as db:
            chat = await db.get(Chat, chat_id)
            if chat is None or not conversation.needs_summary(total, chat.summarised_upto):
                return
            rows = (await db.execute(select(Message.role, Message.content).where(Message.chat_id == chat_id, Message.approval_status != "rejected").order_by(Message.created_at, Message.id))).all()
            older = [(r, c) for r, c in rows[: max(0, len(rows) - conversation.KEEP_VERBATIM)]][chat.summarised_upto :]
            if not older:
                return
            new_summary = await conversation.summarise(llm, chat.summary, older)
            await db.execute(update(Chat).where(Chat.id == chat_id).values(summary=new_summary, summarised_upto=chat.summarised_upto + len(older)))
    except Exception:  # noqa: BLE001 - memory is an optimisation
        return
