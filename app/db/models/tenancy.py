import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class User(Base, TimestampMixin):
    """Global identity (no RLS; see ADR 0006)."""

    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str | None] = mapped_column(Text)
    mfa_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    locale: Mapped[str] = mapped_column(String(8), default="en")
    platform_role: Mapped[str | None] = mapped_column(String(32))  # platform_admin | platform_support
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # secret in mfa_secret_enc is pending until set
    mfa_totp_last_step: Mapped[int] = mapped_column(BigInteger, default=0)  # RFC 6238 §5.2: a time step can be used only once
    mfa_sms_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Firm(Base, TimestampMixin):
    __tablename__ = "firms"
    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(32), default="standard")
    region_pref: Mapped[str | None] = mapped_column(String(32))
    branding: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    ai_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    kms_key_ref: Mapped[str | None] = mapped_column(String(200))
    wrapped_dek: Mapped[bytes | None] = mapped_column(LargeBinary)  # per-firm data key, wrapped by the master key
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FirmMembership(Base, TimestampMixin):
    __tablename__ = "firm_memberships"
    __table_args__ = (UniqueConstraint("firm_id", "user_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="active")


class Client(Base, TimestampMixin):
    __tablename__ = "clients"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16), default="individual")  # individual | business
    name: Mapped[str] = mapped_column(String(200))
    sensitivity: Mapped[str] = mapped_column(String(16), default="normal")
    contacts_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClientMembership(Base, TimestampMixin):
    __tablename__ = "client_memberships"
    __table_args__ = (UniqueConstraint("client_id", "user_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32))  # client_admin | client_user


class Matter(Base, TimestampMixin):
    __tablename__ = "matters"
    id: Mapped[uuid.UUID] = uuid_pk()
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    practice_area: Mapped[str] = mapped_column(String(64), default="general")
    status: Mapped[str] = mapped_column(String(16), default="open")
    assigned_user_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default=text("'{}'::uuid[]")
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
