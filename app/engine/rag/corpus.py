"""Public Law Corpus loader. See corpus/README.md and docs/05 (licensing must be confirmed before production)."""
import hashlib
import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.rag import CorpusDocument
from app.engine.rag.chunking.base import RawChunk, split_text
from app.engine.rag.chunking.statute import chunk_statute
from app.engine.rag.embeddings.base import Embedder
from app.engine.rag.indexer import replace_chunks


class LicenceNotConfirmed(Exception): ...


class CorpusManifest(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,98}$")
    title: str = Field(min_length=1, max_length=300)
    kind: Literal["constitution", "act", "regulation", "judgment"]
    jurisdiction: str = Field(default="KE", max_length=8)
    cap_no: str | None = None
    version_date: date
    effective_from: date | None = None
    effective_to: date | None = None
    source: str = Field(default="", max_length=500)
    licence: str = Field(default="", max_length=200)
    licence_confirmed: bool = False  # set only after written permission / an open licence is on file
    chunking: Literal["statute", "judgment"] = "statute"


def _chunk(manifest: CorpusManifest, body: str) -> list[RawChunk]:
    if manifest.chunking == "statute":
        chunks = chunk_statute(body)
        if chunks:
            return chunks
    return [RawChunk(text=p, section_ref=f"part {i + 1}", ord=i) for i, p in enumerate(split_text(body))]


async def load_corpus_document(
    factory: async_sessionmaker[AsyncSession], embedder: Embedder, manifest: CorpusManifest, body: str, *, actor: uuid.UUID | None = None,
) -> tuple[CorpusDocument, int]:
    """Load (or reload) one version of a source; returns (document, chunks written)."""
    if not manifest.licence_confirmed and settings.is_production:
        raise LicenceNotConfirmed(f"licence for {manifest.id!r} is not confirmed; it cannot be loaded in production")
    checksum = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
    ctx = TenantContext(firm_id=None, user_id=actor or uuid.UUID(int=0), role="platform_admin")
    eff_from = manifest.effective_from or manifest.version_date
    async with tenant_session(factory, ctx) as db:
        doc = (
            await db.execute(select(CorpusDocument).where(CorpusDocument.manifest_id == manifest.id, CorpusDocument.version_date == manifest.version_date))
        ).scalar_one_or_none()
        if doc is not None and doc.checksum == checksum:
            return doc, 0  # unchanged
        if doc is None:
            doc = CorpusDocument(manifest_id=manifest.id, version_date=manifest.version_date, title=manifest.title, kind=manifest.kind, checksum=checksum)
            db.add(doc)
        doc.title, doc.kind, doc.jurisdiction, doc.cap_no = manifest.title, manifest.kind, manifest.jurisdiction, manifest.cap_no
        doc.effective_from, doc.effective_to = eff_from, manifest.effective_to
        doc.source_url, doc.licence, doc.licence_confirmed, doc.checksum = manifest.source, manifest.licence, manifest.licence_confirmed, checksum
        await db.flush()
        await _supersede_previous(db, doc, eff_from)
        n = await replace_chunks(
            db, embedder, _chunk(manifest, body), source_type="corpus", source_id=doc.id, title=manifest.title, scope="public",
            firm_id=None, effective_from=eff_from, effective_to=manifest.effective_to, version_label=manifest.version_date.isoformat(),
        )
        return doc, n


async def _supersede_previous(db: AsyncSession, new: CorpusDocument, eff_from: date) -> None:
    """A newer version ends the validity of the version in force before it ('as at date' retrieval, docs/04)."""
    prev = (
        await db.execute(
            select(CorpusDocument)
            .where(CorpusDocument.manifest_id == new.manifest_id, CorpusDocument.id != new.id, CorpusDocument.effective_from < eff_from,
                   CorpusDocument.effective_to.is_(None))
        )
    ).scalars().all()
    for p in prev:
        p.effective_to = eff_from
        await db.execute(text("UPDATE chunks SET effective_to = :d WHERE source_id = :s AND effective_to IS NULL"), {"d": eff_from, "s": p.id})
