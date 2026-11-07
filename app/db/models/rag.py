import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid_pk


class CorpusDocument(Base):
    """A version of a public-law source (Act, regulation, ...). Platform-managed; readable by everyone."""

    __tablename__ = "corpus_documents"
    id: Mapped[uuid.UUID] = uuid_pk()
    manifest_id: Mapped[str] = mapped_column(String(100), index=True)  # stable id across versions, e.g. ke-insurance-act
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(32))  # constitution | act | regulation | judgment
    jurisdiction: Mapped[str] = mapped_column(String(8), default="KE")
    cap_no: Mapped[str | None] = mapped_column(String(32))
    version_date: Mapped[date] = mapped_column(Date)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(String(500), default="")
    licence: Mapped[str] = mapped_column(String(200), default="")
    licence_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    checksum: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    """Retrieval unit. The embedding/tsv columns are managed with raw SQL (see app/engine/rag/indexer.py)."""

    __tablename__ = "chunks"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    matter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    scope: Mapped[str] = mapped_column(String(16))  # public | firm_kb | client
    shared_with_clients: Mapped[bool] = mapped_column(Boolean, default=False)
    source_type: Mapped[str] = mapped_column(String(16))  # document | corpus
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    title: Mapped[str] = mapped_column(String(300))
    section_ref: Mapped[str] = mapped_column(String(100), default="")
    heading: Mapped[str] = mapped_column(String(300), default="")
    page: Mapped[int | None] = mapped_column(Integer)
    ord: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    embed_model: Mapped[str] = mapped_column(String(100))
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    version_label: Mapped[str] = mapped_column(String(32), default="")
