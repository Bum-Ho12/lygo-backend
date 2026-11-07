import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class Chat(Base, TimestampMixin):
    __tablename__ = "chats"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))  # NULL => advocate chat (firm-only)
    matter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("matters.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    audience: Mapped[str] = mapped_column(String(10))  # client | advocate
    title: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text, default="")  # rolling conversation memory (docs/04 §4)
    summarised_upto: Mapped[int] = mapped_column(Integer, default=0)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    task: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(12), default="ok")  # ok | abstained | deflected
    approval_status: Mapped[str] = mapped_column(String(14), default="not_required")  # not_required | pending | approved | rejected
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(100))
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # usage, guardrail results (AI Bill record-keeping)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class MatterFact(Base, TimestampMixin):
    """Advocate-confirmed facts used as memory for the AI. AI-proposed facts stay 'unconfirmed' (docs/04 §4)."""

    __tablename__ = "matter_facts"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    matter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matters.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="unconfirmed")  # unconfirmed | confirmed
    source: Mapped[str] = mapped_column(String(16), default="advocate")  # advocate | ai
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
