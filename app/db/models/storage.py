import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class StorageDestination(Base, TimestampMixin):
    """A firm-owned (BYO) object storage destination. Credentials are encrypted and never returned by the API."""

    __tablename__ = "storage_destinations"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(24), default="s3_compatible")
    region_country: Mapped[str] = mapped_column(String(2))  # ISO-3166 alpha-2: where the data physically lives
    transfer_basis: Mapped[str] = mapped_column(String(500), default="")  # legal basis when outside Kenya (DPA s.48-49)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # bucket, endpoint, region, prefix
    credentials_enc: Mapped[bytes] = mapped_column(LargeBinary)
    health: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
