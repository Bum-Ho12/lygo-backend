"""MFA, OTP, throttling and password reset

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _pk() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _user() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def _now() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    op.add_column("users", sa.Column("email_verified_at", ts))
    op.add_column("users", sa.Column("phone_verified_at", ts))
    op.add_column("users", sa.Column("mfa_totp_enabled_at", ts))
    op.add_column("users", sa.Column("mfa_totp_last_step", sa.BigInteger, nullable=False, server_default="0"))
    op.add_column("users", sa.Column("mfa_sms_enabled", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("password_changed_at", ts))
    op.add_column("refresh_tokens", sa.Column("mfa", sa.Boolean, nullable=False, server_default=sa.false()))

    op.create_table(
        "otp_challenges", _pk(), _user(),
        sa.Column("purpose", sa.String(16), nullable=False), sa.Column("destination", sa.String(32), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False), sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("consumed_at", sa.DateTime(timezone=True)), _now(),
    )
    op.create_index("ix_otp_challenges_user_id", "otp_challenges", ["user_id"])
    op.create_table(
        "mfa_recovery_codes", _pk(), _user(),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True), sa.Column("used_at", sa.DateTime(timezone=True)), _now(),
    )
    op.create_index("ix_mfa_recovery_codes_user_id", "mfa_recovery_codes", ["user_id"])
    op.create_table(
        "password_reset_tokens", _pk(), _user(),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)), _now(), sa.Column("ip", sa.String(64)),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    # Generic windowed counter used for login/OTP/reset throttling (durable across restarts and replicas; no Redis dependency).
    op.create_table(
        "auth_throttle",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    for t in ("auth_throttle", "password_reset_tokens", "mfa_recovery_codes", "otp_challenges"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.drop_column("refresh_tokens", "mfa")
    for c in ("password_changed_at", "mfa_sms_enabled", "mfa_totp_last_step", "mfa_totp_enabled_at", "phone_verified_at", "email_verified_at"):
        op.drop_column("users", c)
