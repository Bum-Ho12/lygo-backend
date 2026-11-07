"""Document processing pipeline. See docs/05-ingestion-pipeline.md.

Stages (each recorded in ingest_events): parse -> structure -> classify -> rules. Idempotent: re-running
replaces derived data (pages, sections, open flags) and keeps human-resolved flags.
"""
import asyncio
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.tenancy import TenantContext, tenant_session
from app.db.models.documents import (
    Document,
    DocumentField,
    DocumentSection,
    Entity,
    EntityMention,
    Flag,
    IngestEvent,
    Page,
)
from app.engine.packs.registry import PackNotAvailable, cached_rule_pack, list_packs
from app.engine.rag.chunking.sections import chunk_sections
from app.engine.rag.embeddings.base import Embedder, EmbeddingError
from app.engine.rag.indexer import replace_chunks
from app.engine.rules.evaluator import evaluate_pack
from app.ingestion.classify.classifier import classify
from app.ingestion.fields import extract as extract_fields_and_entities
from app.ingestion.models import DocumentText, Section
from app.ingestion.ocr.base import OcrEngine
from app.ingestion.parsers.extract import Extraction, extract
from app.ingestion.sniff import Sniffed, sniff
from app.ingestion.structure import parse_structure
from app.storage.base import StorageDestination
from app.storage.resolver import StorageResolver

WORKER_USER = uuid.UUID(int=0)


def worker_ctx(doc: Document) -> TenantContext:
    # Firm-level context (no client_id): the pipeline legitimately touches client and KB rows alike.
    return TenantContext(firm_id=doc.firm_id, user_id=doc.uploaded_by or WORKER_USER, role="worker")


async def _event(db: AsyncSession, doc: Document, stage: str, status: str, detail: str = "") -> None:
    db.add(IngestEvent(firm_id=doc.firm_id, client_id=doc.client_id, document_id=doc.id, stage=stage, status=status, detail=detail[:1000]))


def _assign_pages(doc_text: DocumentText, ex: Extraction) -> None:
    pages = [(p.n, " ".join(p.text.split())) for p in ex.pages]
    for s in doc_text.sections:
        probe = " ".join(s.full_text.split())[:40]
        if probe:
            s.page = next((n for n, t in pages if probe in t), None)


def _kind_for(doc: Document) -> str:
    return {"application/pdf": "pdf", "text/plain": "text"}.get(doc.mime_type) or (
        "docx" if "wordprocessingml" in doc.mime_type else "image"
    )


async def _download(storage: "StorageDestination", key: str, dest: Path) -> None:
    with dest.open("wb") as f:
        async for chunk in storage.get(key):
            f.write(chunk)


async def process_document(
    factory: async_sessionmaker[AsyncSession], resolver: StorageResolver, ocr: OcrEngine,
    firm_id: uuid.UUID, document_id: uuid.UUID, embedder: Embedder | None = None,
) -> str:
    """Run the pipeline for one document; returns the final status. Never raises for document-level problems."""
    probe_ctx = TenantContext(firm_id=firm_id, user_id=WORKER_USER, role="worker")
    async with tenant_session(factory, probe_ctx) as db:
        doc = await db.get(Document, document_id)
        if doc is None or doc.deleted_at is not None:
            return "missing"
        doc.status, doc.status_detail = "processing", ""
        await _event(db, doc, "start", "ok")
        ctx, key, kind, scope = worker_ctx(doc), doc.storage_key, _kind_for(doc), doc.scope
        storage = await resolver.for_destination(db, firm_id, doc.storage_destination_id)

    try:
        with tempfile.TemporaryDirectory(prefix="lygo-ingest-") as tmp:
            path = Path(tmp) / "original"
            await _download(storage, key, path)
            ex = await asyncio.to_thread(extract, kind, path, ocr)
        status = await _persist(factory, ctx, document_id, ex, scope)
        await _index(factory, ctx, document_id, embedder)
        return status
    except Exception as exc:  # noqa: BLE001 - any failure must end in a recorded 'failed' state
        async with tenant_session(factory, ctx) as db:
            d = await db.get(Document, document_id)
            if d is not None:
                d.status, d.status_detail = "failed", f"{type(exc).__name__}: {exc}"[:500]
                await _event(db, d, "pipeline", "error", d.status_detail)
        return "failed"


async def _persist(
    factory: async_sessionmaker[AsyncSession], ctx: TenantContext, document_id: uuid.UUID, ex: Extraction, scope: str
) -> str:
    async with tenant_session(factory, ctx) as db:
        doc = await db.get(Document, document_id)
        assert doc is not None
        for model in (Page, DocumentSection, DocumentField, EntityMention):
            await db.execute(delete(model).where(model.document_id == doc.id))  # type: ignore[attr-defined]
        await db.execute(delete(Flag).where(Flag.document_id == doc.id, Flag.status == "open"))

        if ex.password_protected:
            doc.status, doc.status_detail = "needs_review", "password_protected"
            await _event(db, doc, "parse", "error", "password-protected document; upload an unprotected copy")
            return doc.status
        for p in ex.pages:
            db.add(Page(firm_id=doc.firm_id, client_id=doc.client_id, document_id=doc.id, n=p.n, text=p.text,
                        ocr_confidence=p.confidence, needs_review=p.needs_review, method=p.method))
        doc.page_count = len(ex.pages)
        text = "\n\n".join(p.text for p in ex.pages if p.text)
        low = [p.n for p in ex.pages if p.needs_review]
        await _event(db, doc, "parse", "ok", f"{len(ex.pages)} page(s)" + (f"; {len(low)} need review" if low else ""))
        if not text.strip():
            doc.status, doc.status_detail = "needs_review", "no_text_extracted" + (" (ocr_unavailable)" if "ocr_unavailable" in ex.notes else "")
            await _event(db, doc, "structure", "skipped", doc.status_detail)
            return doc.status

        structured = parse_structure(text)
        _assign_pages(structured, ex)
        for i, s in enumerate(structured.sections):
            db.add(DocumentSection(firm_id=doc.firm_id, client_id=doc.client_id, document_id=doc.id, ord=i, number=s.number,
                                   heading=s.heading[:300], text=s.text, page=s.page, path=s.path))
        await _event(db, doc, "structure", "ok", f"{len(structured.sections)} section(s)")

        c = classify(text)
        doc.doc_type, doc.doc_type_confidence, doc.practice_area = c.doc_type, c.confidence, c.practice_area
        if c.sensitivity == "sensitive":
            doc.sensitivity = "sensitive"
        await _event(db, doc, "classify", "ok", f"{c.doc_type or 'unclassified'} ({c.confidence})")

        await _extract(db, doc, structured)

        if scope == "client" and c.doc_type:
            structured.doc_type = c.doc_type
            await _run_rules(db, doc, structured)
        else:
            await _event(db, doc, "rules", "skipped", "knowledge-base document" if scope != "client" else "unclassified")

        doc.status = "needs_review" if low else "processed"
        doc.status_detail = f"low_confidence_pages={low}" if low else ""
        return doc.status


async def _index(
    factory: async_sessionmaker[AsyncSession], ctx: TenantContext, document_id: uuid.UUID, embedder: Embedder | None
) -> None:
    """Chunk + embed in its own transaction so an embedding outage never discards the parsed document."""
    try:
        async with tenant_session(factory, ctx) as db:
            doc = await db.get(Document, document_id)
            assert doc is not None
            if embedder is None:
                await _event(db, doc, "index", "skipped", "no embedding model configured")
                return
            rows = (await db.execute(select(DocumentSection).where(DocumentSection.document_id == doc.id).order_by(DocumentSection.ord))).scalars().all()
            if not rows:
                await _event(db, doc, "index", "skipped", "no text")
                return
            n = await replace_chunks(
                db, embedder, chunk_sections(load_sections(list(rows)).sections), source_type="document", source_id=doc.id,
                title=doc.title, scope=doc.scope, firm_id=doc.firm_id, client_id=doc.client_id, matter_id=doc.matter_id,
                shared_with_clients=doc.shared_with_clients,
            )
            await _event(db, doc, "index", "ok", f"{n} chunk(s), model {embedder.model}")
    except (EmbeddingError, OSError) as exc:
        async with tenant_session(factory, ctx) as db:
            doc = await db.get(Document, document_id)
            if doc is not None:
                await _event(db, doc, "index", "error", str(exc))
                doc.status_detail = f"index_failed: {exc}"[:500]


async def _extract(db: AsyncSession, doc: Document, structured: DocumentText) -> None:
    fields, ents = extract_fields_and_entities(structured)
    for f in fields:
        db.add(DocumentField(firm_id=doc.firm_id, client_id=doc.client_id, document_id=doc.id, kind=f.kind, key=f.key[:100],
                             value=f.value[:300], normalized=f.normalized[:300], section_path=f.section_path, span_start=f.start, span_end=f.end))
    if ents:
        await db.execute(
            pg_insert(Entity).values([{"firm_id": doc.firm_id, "kind": e.kind, "name_norm": e.name_norm, "display_name": e.display} for e in ents])
            .on_conflict_do_nothing(index_elements=["firm_id", "kind", "name_norm"])
        )
        ids = {
            (k, n): i
            for i, k, n in (await db.execute(select(Entity.id, Entity.kind, Entity.name_norm).where(Entity.firm_id == doc.firm_id, Entity.name_norm.in_([e.name_norm for e in ents])))).all()
        }
        for e in ents:
            db.add(EntityMention(firm_id=doc.firm_id, client_id=doc.client_id, entity_id=ids[(e.kind, e.name_norm)], document_id=doc.id,
                                 role=e.role, section_path=e.section_path))
    await _event(db, doc, "extract", "ok", f"{len(fields)} field(s), {len(ents)} entit(ies)")


async def _run_rules(db: AsyncSession, doc: Document, structured: DocumentText) -> None:
    resolved = {
        (f.rule_id, (f.span or {}).get("section_path"))
        for f in (await db.execute(select(Flag).where(Flag.document_id == doc.id, Flag.status != "open"))).scalars()
    }
    created, skipped_packs = 0, []
    for meta in list_packs():
        if doc.doc_type not in meta.doc_types:
            continue
        try:
            pack = cached_rule_pack(meta.id)
        except PackNotAvailable as exc:
            skipped_packs.append(f"{meta.id}: {exc}")
            continue
        for fl in evaluate_pack(pack, structured):
            if (fl.rule_id, fl.span.section_path if fl.span else None) in resolved:
                continue  # a human already acknowledged/dismissed this one
            db.add(Flag(
                firm_id=doc.firm_id, client_id=doc.client_id, document_id=doc.id, rule_id=fl.rule_id, pack=fl.pack,
                pack_version=fl.pack_version, severity=fl.severity.value, title=fl.title, explanation=fl.explanation,
                span=fl.span.model_dump() if fl.span else None, extracted=fl.extracted, source_refs=fl.source_refs,
            ))
            created += 1
    await _event(db, doc, "rules", "ok" if not skipped_packs else "skipped", f"{created} flag(s)" + (f"; {skipped_packs}" if skipped_packs else ""))


def load_sections(rows: list[DocumentSection]) -> DocumentText:
    return DocumentText(sections=[Section(number=r.number, heading=r.heading, text=r.text, page=r.page, path=r.path) for r in rows])


_ = Sniffed, sniff  # re-exported for callers that validate before queueing
