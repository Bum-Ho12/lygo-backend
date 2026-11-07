"""notifications (+ RLS with a token-scoped policy for provider delivery reports)

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from app.db.rls import tenant_rls_sql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True)),
        sa.Column("channel", sa.String(8), nullable=False, server_default="sms"),
        sa.Column("template", sa.String(64), nullable=False),
        sa.Column("template_version", sa.Integer, nullable=False),
        sa.Column("urgent", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("recipient", sa.String(32)),
        sa.Column("to_masked", sa.String(32), nullable=False),
        sa.Column("rendered_text", sa.Text),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=False, server_default=""),
        sa.Column("provider", sa.String(24), nullable=False, server_default="thrivewave"),
        sa.Column("provider_txn_id", sa.String(64)),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("deliver_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("meta", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notifications_firm_id", "notifications", ["firm_id"])
    op.create_index("ix_notifications_due", "notifications", ["status", "deliver_after"])
    for stmt in tenant_rls_sql("notifications", firm_only=True):
        op.execute(stmt)
    # The provider's delivery report has no tenant context; it may touch exactly the row whose token hash it presents.
    op.execute(
        "CREATE POLICY dlr_token ON notifications FOR ALL "
        "USING (token_hash = NULLIF(current_setting('app.dlr_token_hash', true), '')) "
        "WITH CHECK (token_hash = NULLIF(current_setting('app.dlr_token_hash', true), ''))"
    )
    # A system sweeper (role 'system_sweeper', no firm) may find due notifications across firms, read-only.
    op.execute(
        "CREATE POLICY sweeper_select ON notifications FOR SELECT USING (app_role() = 'system_sweeper')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notifications CASCADE")
