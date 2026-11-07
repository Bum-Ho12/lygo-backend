import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


def _fk_doc() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    matter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("matters.id", ondelete="SET NULL"))
    scope: Mapped[str] = mapped_column(String(16))  # firm_kb | client
    title: Mapped[str] = mapped_column(String(300))
    original_filename: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_destination_id: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500))
    # received -> processing -> processed | needs_review | failed (| quarantined at upload time: never stored)
    status: Mapped[str] = mapped_column(String(16), default="received")
    status_detail: Mapped[str] = mapped_column(Text, default="")
    doc_type: Mapped[str | None] = mapped_column(String(64))
    doc_type_confidence: Mapped[float | None] = mapped_column(Float)
    practice_area: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), server_default=text("'{}'::varchar[]"))
    sensitivity: Mapped[str] = mapped_column(String(16), default="normal")
    language: Mapped[str | None] = mapped_column(String(8))
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    shared_with_clients: Mapped[bool] = mapped_column(Boolean, default=False)
    version_group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), server_default=text("gen_random_uuid()"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID] = _fk_doc()
    n: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    method: Mapped[str] = mapped_column(String(8), default="text")


class DocumentSection(Base):
    __tablename__ = "document_sections"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID] = _fk_doc()
    ord: Mapped[int] = mapped_column(Integer)
    number: Mapped[str] = mapped_column(String(32), default="")
    heading: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[int | None] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(200), default="")


class Flag(Base, TimestampMixin):
    __tablename__ = "flags"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID] = _fk_doc()
    rule_id: Mapped[str] = mapped_column(String(100))
    pack: Mapped[str] = mapped_column(String(64))
    pack_version: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(300))
    explanation: Mapped[str] = mapped_column(Text)
    span: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extracted: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    source_refs: Mapped[list[str]] = mapped_column(ARRAY(String), server_default=text("'{}'::varchar[]"))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | acknowledged | dismissed
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resolution_note: Mapped[str] = mapped_column(Text, default="")


class IngestEvent(Base):
    __tablename__ = "ingest_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID] = _fk_doc()
    stage: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # ok | skipped | error
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentField(Base):
    __tablename__ = "document_fields"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID] = _fk_doc()
    kind: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(300))
    normalized: Mapped[str] = mapped_column(String(300))
    section_path: Mapped[str] = mapped_column(String(200), default="")
    span_start: Mapped[int | None] = mapped_column(Integer)
    span_end: Mapped[int | None] = mapped_column(Integer)


class Entity(Base, TimestampMixin):
    """Firm-wide named entity (party/reference). Firm-only: the basis for conflict checks across clients."""

    __tablename__ = "entities"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # person | org | reference
    name_norm: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200))


class EntityMention(Base):
    __tablename__ = "entity_mentions"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = _fk_doc()
    role: Mapped[str] = mapped_column(String(32), default="")
    section_path: Mapped[str] = mapped_column(String(200), default="")
