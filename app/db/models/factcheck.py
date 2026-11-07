import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid_pk


class FactCheck(Base):
    """An advocate's fact/cite check of a draft. Firm-only (privileged work product)."""

    __tablename__ = "fact_checks"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    matter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("matters.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    input_text: Mapped[str] = mapped_column(Text)
    as_at: Mapped[str] = mapped_column(String(10))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FactCheckItem(Base):
    __tablename__ = "fact_check_items"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    fact_check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fact_checks.id", ondelete="CASCADE"), index=True)
    ord: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))  # citation | claim
    content: Mapped[str] = mapped_column(Text)  # the reference or claim, as it appears in the draft
    verdict: Mapped[str] = mapped_column(String(24))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # evidence quotes, matched text, notes
