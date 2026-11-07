"""fact checks (+ RLS, firm-only)

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from app.db.rls import tenant_rls_sql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fact_checks",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("matter_id", pg.UUID(as_uuid=True), sa.ForeignKey("matters.id", ondelete="SET NULL")),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("input_text", sa.Text, nullable=False),
        sa.Column("as_at", sa.String(10), nullable=False),
        sa.Column("summary", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("model", sa.String(100)),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_fact_checks_firm_id", "fact_checks", ["firm_id"])
    op.create_table(
        "fact_check_items",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_check_id", pg.UUID(as_uuid=True), sa.ForeignKey("fact_checks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("detail", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_fact_check_items_firm_id", "fact_check_items", ["firm_id"])
    op.create_index("ix_fact_check_items_fact_check_id", "fact_check_items", ["fact_check_id"])
    for t in ("fact_checks", "fact_check_items"):
        for stmt in tenant_rls_sql(t, firm_only=True):  # privileged work product: never visible to client users
            op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fact_check_items CASCADE")
    op.execute("DROP TABLE IF EXISTS fact_checks CASCADE")
