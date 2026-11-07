"""Upload handling: limits, type sniffing, malware scan, dedup, versioning, storage, then dispatch."""
import hashlib
import tempfile
import uuid
from pathlib import Path
from typing import IO

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import NotFound, ProblemError
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.documents import Document
from app.db.models.tenancy import Client, Matter
from app.domain.audit import service as audit
from app.ingestion.scan import Scanner, ScannerUnavailable
from app.ingestion.sniff import UnsupportedFile, sniff
from app.storage.base import StorageDestination
from app.workers.ingest import Dispatcher

_CHUNK = 1 << 20


async def _read_upload(upload: UploadFile) -> tuple[IO[bytes], int, str]:
    """Spool to disk with a hard size cap while hashing; returns (file, size, sha256)."""
    limit = settings.max_upload_mb * 1024 * 1024
    buf = tempfile.SpooledTemporaryFile(max_size=8 << 20)  # noqa: SIM115 - closed by caller
    h, size = hashlib.sha256(), 0
    while chunk := await upload.read(_CHUNK):
        size += len(chunk)
        if size > limit:
            buf.close()
            raise ProblemError(413, "file_too_large", "File too large", f"Maximum upload size is {settings.max_upload_mb} MB.")
        h.update(chunk)
        buf.write(chunk)
    if size == 0:
        buf.close()
        raise ProblemError(422, "empty_file", "Empty file", "The uploaded file is empty.")
    buf.seek(0)
    return buf, size, h.hexdigest()


async def _stream(f: IO[bytes]):  # type: ignore[no-untyped-def]
    f.seek(0)
    while chunk := f.read(_CHUNK):
        yield chunk


async def _precheck(
    db: AsyncSession, ctx: TenantContext, *, scope: str, client_id: uuid.UUID | None, matter_id: uuid.UUID | None,
    previous_version_id: uuid.UUID | None, sha: str,
) -> tuple[Document | None, uuid.UUID | None, int]:
    """Returns (existing duplicate | None, version_group_id | None, version_no)."""
    if client_id is not None and await db.get(Client, client_id) is None:
        raise NotFound("client")
    if matter_id is not None:
        m = await db.get(Matter, matter_id)
        if m is None or m.client_id != client_id:
            raise ProblemError(422, "invalid_matter", "Invalid matter", "The matter does not belong to this client.")
    dup = (
        await db.execute(
            select(Document).where(
                Document.firm_id == ctx.firm_id, Document.sha256 == sha, Document.deleted_at.is_(None),
                Document.client_id.is_(None) if client_id is None else Document.client_id == client_id,
            )
        )
    ).scalars().first()
    if dup:
        return dup, None, 0
    if previous_version_id is None:
        return None, None, 1
    prev = await db.get(Document, previous_version_id)
    if prev is None or prev.deleted_at is not None or prev.scope != scope or prev.client_id != client_id:
        raise ProblemError(422, "invalid_version", "Invalid previous version", "The previous version must be in the same scope.")
    latest = (await db.execute(select(func.max(Document.version_no)).where(Document.version_group_id == prev.version_group_id))).scalar_one()
    return None, prev.version_group_id, int(latest) + 1


async def ingest_upload(
    factory: async_sessionmaker[AsyncSession], storage: StorageDestination, scanner: Scanner, dispatcher: Dispatcher,
    ctx: TenantContext, upload: UploadFile, *, scope: str, client_id: uuid.UUID | None, matter_id: uuid.UUID | None = None,
    title: str | None = None, tags: list[str] | None = None, previous_version_id: uuid.UUID | None = None,
) -> tuple[Document, bool]:
    """Returns (document, duplicate). Quarantined/invalid files are rejected and never stored."""
    assert ctx.firm_id
    filename = Path(upload.filename or "upload").name[:300]
    buf, size, sha = await _read_upload(upload)
    try:
        try:
            sniffed = sniff(buf, filename)
        except UnsupportedFile as exc:
            raise ProblemError(415, "unsupported_file", "Unsupported file", exc.reason) from exc
        try:
            result = await scanner.scan(buf)
        except ScannerUnavailable as exc:
            raise ProblemError(503, "scanner_unavailable", "Scanner unavailable", "Uploads are temporarily unavailable; try again later.") from exc
        if not result.clean:
            async with tenant_session(factory, ctx) as db:
                await audit.record(db, ctx, "document.quarantined", target=f"file:{filename}", meta={"signature": result.signature, "sha256": sha})
            raise ProblemError(422, "malware_detected", "File rejected", "The file failed the security scan and was not stored.")

        async with tenant_session(factory, ctx) as db:
            dup, group, version_no = await _precheck(
                db, ctx, scope=scope, client_id=client_id, matter_id=matter_id, previous_version_id=previous_version_id, sha=sha
            )
        if dup:
            return dup, True

        doc_id = uuid.uuid4()
        key = f"{ctx.firm_id}/{scope}/{doc_id}/original"
        stored = await storage.put(key, _stream(buf), content_type=sniffed.mime, metadata={"sha256": sha})
        try:
            async with tenant_session(factory, ctx) as db:
                doc = Document(
                    id=doc_id, firm_id=ctx.firm_id, client_id=client_id, matter_id=matter_id, scope=scope,
                    title=(title or Path(filename).stem)[:300], original_filename=filename, mime_type=sniffed.mime,
                    size_bytes=stored.size, sha256=stored.sha256, storage_destination_id=stored.destination_id,
                    storage_key=key, status="received", tags=tags or [], uploaded_by=ctx.user_id, version_no=version_no,
                    **({"version_group_id": group} if group else {}),
                )
                db.add(doc)
                await db.flush()
                await audit.record(db, ctx, "document.upload", target=f"document:{doc_id}", meta={"scope": scope, "size": size})
        except IntegrityError:  # lost a race with an identical concurrent upload
            await storage.delete(key)
            async with tenant_session(factory, ctx) as db:
                existing = (await db.execute(select(Document).where(Document.sha256 == sha, Document.firm_id == ctx.firm_id))).scalars().first()
                if existing:
                    return existing, True
            raise
        except Exception:
            await storage.delete(key)
            raise
    finally:
        buf.close()
    await dispatcher.dispatch(ctx.firm_id, doc.id)
    return doc, False
