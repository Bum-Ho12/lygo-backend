"""Job handlers. A handler receives only ids from the job payload and loads everything else under the right tenant context."""
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.tenancy import TenantContext, tenant_session
from app.db.models.documents import Document
from app.domain.notifications import service as notifications
from app.engine.rag.embeddings.base import Embedder
from app.ingestion.ocr.base import OcrEngine
from app.ingestion.pipeline import process_document
from app.integrations.sms.base import SmsProvider
from app.storage.resolver import StorageResolver
from app.workers import jobs
from app.workers.jobs import Job
from app.workers.runner import Handler, Periodic, PermanentJobError

STUCK_AFTER = timedelta(minutes=30)

PERIODIC = [
    Periodic("sms.sweep", 60),  # safety net: queued SMS whose own job was lost
    Periodic("documents.reap_stuck", 300),  # documents left half-processed
    Periodic("jobs.purge", 86_400),  # forget finished jobs after a week
]


@dataclass
class WorkerDeps:
    factory: async_sessionmaker[AsyncSession]
    resolver: StorageResolver
    ocr: OcrEngine
    embedder: Embedder | None
    sms: SmsProvider | None


def _ids(job: Job, *names: str) -> list[uuid.UUID]:
    try:
        return [uuid.UUID(str(job.payload[n])) for n in names]
    except (KeyError, ValueError) as exc:
        raise PermanentJobError(f"malformed payload for {job.kind}") from exc


def build_handlers(d: WorkerDeps) -> dict[str, Handler]:
    async def ingest(job: Job) -> None:
        firm_id, doc_id = _ids(job, "firm_id", "document_id")
        # process_document records its own outcome on the document (processed / needs_review / failed); a 'failed' document is a
        # visible, user-retryable state, not a reason to loop the job forever.
        await process_document(d.factory, d.resolver, d.ocr, firm_id, doc_id, d.embedder)

    async def sms_deliver(job: Job) -> None:
        firm_id, nid = _ids(job, "firm_id", "notification_id")
        if d.sms is None:
            raise RuntimeError("no SMS provider configured")  # retried with backoff until configuration is fixed or attempts run out
        await notifications.deliver(d.factory, d.sms, firm_id, nid)

    async def sms_sweep(job: Job) -> None:
        if d.sms is not None:
            await notifications.send_due(d.factory, d.sms)

    async def reap_stuck(job: Job) -> None:
        """Defence in depth: re-enqueue documents stuck in 'received'/'processing' with no live job (jobs are enqueued atomically,
        so this should find nothing; it exists for bugs, manual DB edits and old data)."""
        cutoff = datetime.now(UTC) - STUCK_AFTER
        sweeper = TenantContext(firm_id=None, user_id=uuid.UUID(int=0), role="system_sweeper")
        async with tenant_session(d.factory, sweeper) as db:  # documents are RLS-protected: the sweeper role has no read policy on them,
            _ = db  # so stuck documents are found per firm below
        async with d.factory() as s, s.begin():
            firms = [r[0] for r in (await s.execute(_FIRMS_SQL)).all()]
        for firm_id in firms:
            ctx = TenantContext(firm_id=firm_id, user_id=uuid.UUID(int=0), role="worker")
            async with tenant_session(d.factory, ctx) as db:
                stuck = (await db.execute(select(Document.id).where(Document.status.in_(("received", "processing")), Document.updated_at < cutoff, Document.deleted_at.is_(None)).limit(100))).scalars().all()
                for doc_id in stuck:
                    await jobs.enqueue(db, "ingest.process_document", {"firm_id": str(firm_id), "document_id": str(doc_id)}, firm_id=firm_id, dedupe_key=f"ingest:{doc_id}")

    async def purge(job: Job) -> None:
        await jobs.purge(d.factory)

    return {"ingest.process_document": ingest, "sms.deliver": sms_deliver, "sms.sweep": sms_sweep, "documents.reap_stuck": reap_stuck, "jobs.purge": purge}


from sqlalchemy import text

_FIRMS_SQL = text("SELECT DISTINCT firm_id FROM jobs WHERE false UNION SELECT id FROM firms WHERE deleted_at IS NULL")
