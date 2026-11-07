"""chats, messages (with approval gating in RLS) and matter facts

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from app.db.rls import tenant_rls_sql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _pk() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _firm() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False)


def _ts() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "chats", _pk(), _firm(),
        sa.Column("client_id", pg.UUID(as_uuid=True), sa.ForeignKey("clients.id", ondelete="CASCADE")),
        sa.Column("matter_id", pg.UUID(as_uuid=True), sa.ForeignKey("matters.id", ondelete="SET NULL")),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("audience", sa.String(10), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("summarised_upto", sa.Integer, nullable=False, server_default="0"),
        *_ts(),
        sa.CheckConstraint("audience IN ('client','advocate')", name="audience_valid"),
        sa.CheckConstraint("(audience = 'client') = (client_id IS NOT NULL)", name="client_chat_has_client"),
    )
    op.create_table(
        "messages", _pk(), _firm(), sa.Column("client_id", pg.UUID(as_uuid=True)),
        sa.Column("chat_id", pg.UUID(as_uuid=True), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("task", sa.String(20)),
        sa.Column("status", sa.String(12), nullable=False, server_default="ok"),
        sa.Column("approval_status", sa.String(14), nullable=False, server_default="not_required"),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("model", sa.String(100)),
        sa.Column("citations", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("meta", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("approved_by", pg.UUID(as_uuid=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("reviewer_note", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('user','assistant')", name="role_valid"),
        sa.CheckConstraint("approval_status IN ('not_required','pending','approved','rejected')", name="approval_valid"),
    )
    op.create_table(
        "matter_facts", _pk(), _firm(), sa.Column("client_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("matter_id", pg.UUID(as_uuid=True), sa.ForeignKey("matters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False), sa.Column("value", sa.Text, nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="unconfirmed"),
        sa.Column("source", sa.String(16), nullable=False, server_default="advocate"),
        sa.Column("created_by", pg.UUID(as_uuid=True)), sa.Column("confirmed_by", pg.UUID(as_uuid=True)),
        *_ts(),
    )
    for t in ("chats", "messages", "matter_facts"):
        op.create_index(f"ix_{t}_firm_id", t, ["firm_id"])
    op.create_index("ix_messages_chat_id", "messages", ["chat_id"])
    op.create_index("ix_matter_facts_matter_id", "matter_facts", ["matter_id"])

    for stmt in tenant_rls_sql("chats", client_column="client_id"):
        op.execute(stmt)
    for stmt in tenant_rls_sql("messages", client_column="client_id"):
        op.execute(stmt)
    # Approval mode (docs/04 guardrail 3), enforced in the database: clients only ever see their own questions and
    # assistant replies that are not awaiting review / were not rejected. Firm users see everything.
    op.execute(
        "CREATE POLICY client_sees_released_replies ON messages AS RESTRICTIVE FOR SELECT "
        "USING (app_client_id() IS NULL OR role = 'user' OR approval_status IN ('not_required', 'approved'))"
    )
    # Client users are read/ask-only: they may add their own plain questions, and can never forge, edit or delete AI replies
    # (the replies are the audit record for AI oversight). Staff and the system worker are unaffected (app_client_id() IS NULL).
    op.execute(
        "CREATE POLICY client_insert_questions_only ON messages AS RESTRICTIVE FOR INSERT "
        "WITH CHECK (app_client_id() IS NULL OR (role = 'user' AND approval_status = 'not_required'))"
    )
    op.execute("CREATE POLICY client_no_update ON messages AS RESTRICTIVE FOR UPDATE USING (app_client_id() IS NULL) WITH CHECK (app_client_id() IS NULL)")
    op.execute("CREATE POLICY client_no_delete ON messages AS RESTRICTIVE FOR DELETE USING (app_client_id() IS NULL)")
    op.execute("CREATE POLICY client_chats_no_update ON chats AS RESTRICTIVE FOR UPDATE USING (app_client_id() IS NULL) WITH CHECK (app_client_id() IS NULL)")
    op.execute("CREATE POLICY client_chats_no_delete ON chats AS RESTRICTIVE FOR DELETE USING (app_client_id() IS NULL)")
    for stmt in tenant_rls_sql("matter_facts", firm_only=True):
        op.execute(stmt)


def downgrade() -> None:
    for t in ("matter_facts", "messages", "chats"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
