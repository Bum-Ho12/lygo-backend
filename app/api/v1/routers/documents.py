import asyncio
import json
import re
import time
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Factory, require, tenant_db
from app.core.config import settings
from app.core.errors import NotFound, ProblemError
from app.core.pagination import Page, keyset_page
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.documents import (
    Document,
    DocumentField,
    DocumentSection,
    Entity,
    EntityMention,
    Flag,
    IngestEvent,
)
from app.db.models.documents import Page as PageRow
from app.domain.audit import service as audit
from app.domain.documents.service import ingest_upload
from app.domain.matters.service import get_or_404
from app.engine.compare.aligner import compare_documents
from app.engine.compare.documents import FieldDiff, diff_fields
from app.engine.compare.models import ComparisonResult
from app.ingestion.pipeline import load_sections
from app.ingestion.scan import Scanner, get_scanner
from app.storage.base import ObjectNotFound, StorageError
from app.storage.resolver import StorageResolver, get_resolver
from app.workers.ingest import Dispatcher, get_dispatcher

router = APIRouter(prefix="/firms/{firm_id}", tags=["documents"])
Db = Annotated[AsyncSession, Depends(tenant_db)]
Limit = Annotated[int | None, Query(ge=1, le=200)]
Resolver = Annotated[StorageResolver, Depends(get_resolver)]
Scan = Annotated[Scanner, Depends(get_scanner)]
Disp = Annotated[Dispatcher, Depends(get_dispatcher)]
DocRead = Annotated[TenantContext, Depends(require("documents.read"))]


class DocumentOut(BaseModel):
    id: uuid.UUID
    scope: str
    client_id: uuid.UUID | None
    matter_id: uuid.UUID | None
    title: str
    original_filename: str
    mime_type: str
    size_bytes: int
    status: str
    status_detail: str
    doc_type: str | None
    practice_area: str | None
    tags: list[str]
    sensitivity: str
    page_count: int
    shared_with_clients: bool
    version_group_id: uuid.UUID
    version_no: int
    created_at: datetime
    duplicate: bool = False


class EventOut(BaseModel):
    stage: str
    status: str
    detail: str
    at: datetime


class DocumentDetail(DocumentOut):
    events: list[EventOut]
    flag_counts: dict[str, int]


class FlagOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    rule_id: str
    severity: str
    title: str
    explanation: str
    span: dict[str, Any] | None
    extracted: dict[str, Any]
    status: str
    resolution_note: str


class KnowledgePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    tags: list[str] | None = None
    shared_with_clients: bool | None = None


class FlagPatch(BaseModel):
    status: Literal["open", "acknowledged", "dismissed"]
    note: str = Field(default="", max_length=2000)


class CompareIn(BaseModel):
    left_document_id: uuid.UUID
    right_document_id: uuid.UUID


class CompareOut(BaseModel):
    left_document_id: uuid.UUID
    right_document_id: uuid.UUID
    result: ComparisonResult
    field_diff: list[FieldDiff] = []


class FieldOut(BaseModel):
    kind: str
    key: str
    value: str
    section_path: str


class EntityOut(BaseModel):
    id: uuid.UUID
    kind: str
    display_name: str
    mentions: int
    documents: list[uuid.UUID]


def _doc(d: Document, duplicate: bool = False) -> DocumentOut:
    return DocumentOut(
        id=d.id, scope=d.scope, client_id=d.client_id, matter_id=d.matter_id, title=d.title,
        original_filename=d.original_filename, mime_type=d.mime_type, size_bytes=d.size_bytes, status=d.status,
        status_detail=d.status_detail, doc_type=d.doc_type, practice_area=d.practice_area, tags=list(d.tags or []),
        sensitivity=d.sensitivity, page_count=d.page_count, shared_with_clients=d.shared_with_clients,
        version_group_id=d.version_group_id, version_no=d.version_no, created_at=d.created_at, duplicate=duplicate,
    )


def _flag(f: Flag) -> FlagOut:
    return FlagOut(id=f.id, document_id=f.document_id, rule_id=f.rule_id, severity=f.severity, title=f.title,
                   explanation=f.explanation, span=f.span, extracted=f.extracted, status=f.status, resolution_note=f.resolution_note)


def _tags(raw: str | None) -> list[str]:
    return sorted({t.strip().lower()[:50] for t in (raw or "").split(",") if t.strip()})[:20]


# --- door 1: firm knowledge base -------------------------------------------------------------------
@router.post("/knowledge/documents", status_code=201, response_model=DocumentOut)
async def upload_knowledge(
    response: Response, factory: Factory, resolver: Resolver, scanner: Scan, dispatcher: Disp,
    ctx: Annotated[TenantContext, Depends(require("knowledge.manage"))],
    file: Annotated[UploadFile, File()], title: Annotated[str | None, Form()] = None, tags: Annotated[str | None, Form()] = None,
) -> DocumentOut:
    doc, dup = await ingest_upload(factory, resolver, scanner, dispatcher, ctx, file, scope="firm_kb", client_id=None, title=title, tags=_tags(tags))
    response.status_code = 200 if dup else 201
    return _doc(doc, dup)


@router.get("/knowledge/documents", response_model=Page[DocumentOut])
async def list_knowledge(db: Db, ctx: DocRead, cursor: str | None = None, limit: Limit = None) -> Page[DocumentOut]:
    stmt = select(Document).where(Document.scope == "firm_kb", Document.deleted_at.is_(None))
    rows, nxt = await keyset_page(db, stmt, Document.id, cursor=cursor, limit=limit)
    return Page[DocumentOut](items=[_doc(d) for d in rows], next_cursor=nxt)


@router.patch("/knowledge/documents/{doc_id}", response_model=DocumentOut)
async def patch_knowledge(doc_id: uuid.UUID, body: KnowledgePatch, db: Db, ctx: Annotated[TenantContext, Depends(require("knowledge.manage"))]) -> DocumentOut:
    d = await get_or_404(db, Document, doc_id, "document")
    if d.scope != "firm_kb":
        raise NotFound("document")
    changed = body.model_dump(exclude_unset=True)
    if "tags" in changed:
        changed["tags"] = _tags(",".join(changed["tags"] or []))
    for k, v in changed.items():
        setattr(d, k, v)
    if "shared_with_clients" in changed:  # retrieval visibility follows the document
        await db.execute(text("UPDATE chunks SET shared_with_clients = :v WHERE source_id = :s"), {"v": d.shared_with_clients, "s": d.id})
    await audit.record(db, ctx, "knowledge.update", target=f"document:{d.id}", meta={"fields": sorted(changed)})
    return _doc(d)


# --- door 2: client documents ----------------------------------------------------------------------
@router.post("/clients/{client_id}/documents", status_code=201, response_model=DocumentOut)
async def upload_client_document(
    client_id: uuid.UUID, response: Response, factory: Factory, resolver: Resolver, scanner: Scan, dispatcher: Disp,
    ctx: Annotated[TenantContext, Depends(require("documents.upload"))], file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None, tags: Annotated[str | None, Form()] = None,
    matter_id: Annotated[uuid.UUID | None, Form()] = None, previous_version_id: Annotated[uuid.UUID | None, Form()] = None,
) -> DocumentOut:
    doc, dup = await ingest_upload(
        factory, resolver, scanner, dispatcher, ctx, file, scope="client", client_id=client_id, matter_id=matter_id,
        title=title, tags=_tags(tags), previous_version_id=previous_version_id,
    )
    response.status_code = 200 if dup else 201
    return _doc(doc, dup)


@router.get("/clients/{client_id}/documents", response_model=Page[DocumentOut])
async def list_client_documents(client_id: uuid.UUID, db: Db, ctx: DocRead, cursor: str | None = None, limit: Limit = None) -> Page[DocumentOut]:
    stmt = select(Document).where(Document.client_id == client_id, Document.deleted_at.is_(None))
    rows, nxt = await keyset_page(db, stmt, Document.id, cursor=cursor, limit=limit)
    return Page[DocumentOut](items=[_doc(d) for d in rows], next_cursor=nxt)


# --- shared ----------------------------------------------------------------------------------------
@router.get("/documents/{doc_id}", response_model=DocumentDetail)
async def get_document(doc_id: uuid.UUID, db: Db, ctx: DocRead) -> DocumentDetail:
    d = await get_or_404(db, Document, doc_id, "document")
    events = (await db.execute(select(IngestEvent).where(IngestEvent.document_id == d.id).order_by(IngestEvent.id))).scalars()
    counts = dict((await db.execute(select(Flag.severity, func.count()).where(Flag.document_id == d.id, Flag.status != "dismissed").group_by(Flag.severity))).all())
    return DocumentDetail(**_doc(d).model_dump(), events=[EventOut(stage=e.stage, status=e.status, detail=e.detail, at=e.at) for e in events], flag_counts=counts)


@router.get("/documents/{doc_id}/content")
async def document_content(doc_id: uuid.UUID, db: Db, resolver: Resolver, ctx: DocRead) -> StreamingResponse:
    d = await get_or_404(db, Document, doc_id, "document")
    try:
        storage = await resolver.for_destination(db, d.firm_id, d.storage_destination_id)
    except StorageError as exc:
        raise ProblemError(503, "storage_unavailable", "Storage unavailable", str(exc)) from exc
    safe = re.sub(r'[^A-Za-z0-9._ -]', "_", d.original_filename)
    stream = storage.get(d.storage_key)
    try:
        first = await anext(stream)
    except ObjectNotFound as exc:
        raise NotFound("document content") from exc

    async def body():  # type: ignore[no-untyped-def]
        yield first
        async for chunk in stream:
            yield chunk

    return StreamingResponse(
        body(), media_type=d.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{safe}"', "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.get("/documents/{doc_id}/events")
async def document_events(doc_id: uuid.UUID, request: Request, factory: Factory, ctx: DocRead, after: int = 0) -> StreamingResponse:
    """Server-Sent Events: the document's processing timeline, live. Resume with `?after=<last event id>` (or Last-Event-ID).
    Ends with `event: done` once the document reaches a final status."""
    last = max(after, int(request.headers.get("last-event-id", "0") or 0))
    async with tenant_session(factory, ctx) as db:
        await get_or_404(db, Document, doc_id, "document")

    async def stream():  # type: ignore[no-untyped-def]
        nonlocal last
        deadline = time.monotonic() + settings.sse_max_seconds
        while time.monotonic() < deadline and not await request.is_disconnected():
            async with tenant_session(factory, ctx) as db:  # short transactions: nothing is held open while idle
                rows = (await db.execute(select(IngestEvent).where(IngestEvent.document_id == doc_id, IngestEvent.id > last).order_by(IngestEvent.id))).scalars().all()
                d = await db.get(Document, doc_id)
            for e in rows:
                last = e.id
                yield f"id: {e.id}\nevent: stage\ndata: {json.dumps({'stage': e.stage, 'status': e.status, 'detail': e.detail, 'at': e.at.isoformat()})}\n\n"
            if d is None or (not rows and d.status in {"processed", "needs_review", "failed"}):
                yield f"event: done\ndata: {json.dumps({'status': d.status if d else 'missing'})}\n\n"
                return
            await asyncio.sleep(settings.sse_poll_seconds)
        yield "event: timeout\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/documents/{doc_id}/flags", response_model=list[FlagOut])
async def document_flags(doc_id: uuid.UUID, db: Db, ctx: Annotated[TenantContext, Depends(require("flags.read"))]) -> list[FlagOut]:
    await get_or_404(db, Document, doc_id, "document")
    rows = (await db.execute(select(Flag).where(Flag.document_id == doc_id).order_by(Flag.created_at, Flag.id))).scalars()
    order = {"critical": 0, "warning": 1, "caution": 2, "info": 3}
    return sorted((_flag(f) for f in rows), key=lambda f: order.get(f.severity, 9))


@router.patch("/flags/{flag_id}", response_model=FlagOut)
async def patch_flag(flag_id: uuid.UUID, body: FlagPatch, db: Db, ctx: Annotated[TenantContext, Depends(require("flags.manage"))]) -> FlagOut:
    f = await get_or_404(db, Flag, flag_id, "flag")
    if body.status == "dismissed" and len(body.note.strip()) < 3:
        raise ProblemError(422, "note_required", "Reason required", "Dismissing a flag requires a short reason.")
    f.status, f.resolution_note = body.status, body.note.strip()
    f.resolved_by = None if body.status == "open" else ctx.user_id
    await audit.record(db, ctx, "flag.update", target=f"flag:{f.id}", meta={"status": body.status})
    return _flag(f)


@router.post("/documents/{doc_id}:reprocess", status_code=202)
async def reprocess(doc_id: uuid.UUID, factory: Factory, dispatcher: Disp, ctx: Annotated[TenantContext, Depends(require("documents.tag"))]) -> dict[str, str]:
    assert ctx.firm_id
    async with tenant_session(factory, ctx) as db:
        d = await get_or_404(db, Document, doc_id, "document")
        d.status = "received"
        await audit.record(db, ctx, "document.reprocess", target=f"document:{d.id}")
    await dispatcher.dispatch(ctx.firm_id, doc_id)
    return {"status": "queued"}


@router.get("/review-queue")
async def review_queue(db: Db, ctx: Annotated[TenantContext, Depends(require("documents.tag"))]) -> dict[str, list[dict[str, Any]]]:
    rows = (
        await db.execute(
            select(PageRow.document_id, PageRow.n, PageRow.ocr_confidence, Document.title)
            .join(Document, Document.id == PageRow.document_id)
            .where(PageRow.needs_review.is_(True), Document.deleted_at.is_(None))
            .order_by(PageRow.document_id, PageRow.n).limit(500)
        )
    ).all()
    return {"items": [{"document_id": str(d), "page": n, "ocr_confidence": c, "title": t} for d, n, c, t in rows]}


# --- comparison (deterministic engine; ADR 0005). Firm-only: may span several clients' documents. ------
@router.post("/comparisons", response_model=CompareOut)
async def compare(body: CompareIn, db: Db, ctx: Annotated[TenantContext, Depends(require("compare.run"))]) -> CompareOut:
    texts, fields = [], []
    for doc_id in (body.left_document_id, body.right_document_id):
        d = await get_or_404(db, Document, doc_id, "document")
        rows = list((await db.execute(select(DocumentSection).where(DocumentSection.document_id == d.id).order_by(DocumentSection.ord))).scalars())
        if not rows:
            raise ProblemError(422, "not_processed", "Document not ready", f"Document {doc_id} has no extracted text yet (status: {d.status}).")
        texts.append(load_sections(rows))
        fields.append((await db.execute(select(DocumentField.kind, DocumentField.key, DocumentField.normalized).where(DocumentField.document_id == d.id))).all())
    await audit.record(db, ctx, "comparison.run", target=f"documents:{body.left_document_id},{body.right_document_id}")
    return CompareOut(
        left_document_id=body.left_document_id, right_document_id=body.right_document_id, result=compare_documents(*texts),
        field_diff=diff_fields([(r[0], r[1], r[2]) for r in fields[0]], [(r[0], r[1], r[2]) for r in fields[1]]),
    )


@router.get("/documents/{doc_id}/fields", response_model=list[FieldOut])
async def document_fields(doc_id: uuid.UUID, db: Db, ctx: DocRead) -> list[FieldOut]:
    await get_or_404(db, Document, doc_id, "document")
    rows = (await db.execute(select(DocumentField).where(DocumentField.document_id == doc_id).order_by(DocumentField.kind, DocumentField.key, DocumentField.id))).scalars()
    return [FieldOut(kind=f.kind, key=f.key, value=f.value, section_path=f.section_path) for f in rows]


@router.get("/entities", response_model=list[EntityOut])
async def list_entities(db: Db, ctx: Annotated[TenantContext, Depends(require("entities.read"))], q: Annotated[str | None, Query(min_length=2, max_length=100)] = None) -> list[EntityOut]:
    """Named parties/references found in the firm's documents (firm-only; foundation for conflict checks)."""
    stmt = select(Entity).order_by(Entity.display_name).limit(100)
    if q:
        stmt = stmt.where(Entity.name_norm.contains(" ".join(q.lower().split())))
    out = []
    for e in (await db.execute(stmt)).scalars():
        docs = list((await db.execute(select(EntityMention.document_id).where(EntityMention.entity_id == e.id).distinct())).scalars())
        out.append(EntityOut(id=e.id, kind=e.kind, display_name=e.display_name, mentions=len(docs), documents=docs))
    return out
