import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class Notification(Base, TimestampMixin):
    """One outbound message to one recipient. PII (`recipient`, `rendered_text`) is erased once terminal."""

    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    channel: Mapped[str] = mapped_column(String(8), default="sms")
    template: Mapped[str] = mapped_column(String(64))
    template_version: Mapped[int] = mapped_column(Integer)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    recipient: Mapped[str | None] = mapped_column(String(32))  # E.164 until terminal
    to_masked: Mapped[str] = mapped_column(String(32))
    rendered_text: Mapped[str | None] = mapped_column(Text)
    # queued -> sending -> sent -> delivered | failed ; uncertain = may or may not have been sent (never auto-retried)
    status: Mapped[str] = mapped_column(String(12), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(24), default="thrivewave")
    provider_txn_id: Mapped[str | None] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    deliver_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
