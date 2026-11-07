import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid_pk


class FirmApplication(Base):
    """A user's request to open a firm workspace. Pre-tenant (no firm yet), so like identity tables it has no tenant RLS
    (ADR 0006): every query filters by the applicant, or runs as a platform admin."""

    __tablename__ = "firm_applications"
    id: Mapped[uuid.UUID] = uuid_pk()
    applicant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    firm_name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63))
    practice_areas: Mapped[list[str]] = mapped_column(ARRAY(String), server_default=text("'{}'::varchar[]"))
    lsk_admission_no: Mapped[str | None] = mapped_column(String(40))  # Law Society of Kenya admission number of the applicant
    phone: Mapped[str | None] = mapped_column(String(20))  # E.164
    town: Mapped[str | None] = mapped_column(String(80))
    size: Mapped[str] = mapped_column(String(10))  # solo | 2-5 | 6-20 | 21+
    terms_version: Mapped[str] = mapped_column(String(40))
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | approved | rejected | withdrawn
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    firm_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("firms.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
