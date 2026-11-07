"""typed document fields and firm entities (+ RLS)

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from app.db.rls import tenant_rls_sql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _pk() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _firm() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False)


def _doc() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("document_id", pg.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "document_fields", _pk(), _firm(), sa.Column("client_id", pg.UUID(as_uuid=True)), _doc(),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.String(300), nullable=False), sa.Column("normalized", sa.String(300), nullable=False),
        sa.Column("section_path", sa.String(200), nullable=False, server_default=""),
        sa.Column("span_start", sa.Integer), sa.Column("span_end", sa.Integer),
    )
    op.create_table(
        "entities", _pk(), _firm(),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("name_norm", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("firm_id", "kind", "name_norm"),
    )
    op.create_table(
        "entity_mentions", _pk(), _firm(), sa.Column("client_id", pg.UUID(as_uuid=True)),
        sa.Column("entity_id", pg.UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False), _doc(),
        sa.Column("role", sa.String(32), nullable=False, server_default=""),
        sa.Column("section_path", sa.String(200), nullable=False, server_default=""),
    )
    for t, cols in (("document_fields", ["firm_id", "document_id"]), ("entities", ["firm_id"]), ("entity_mentions", ["firm_id", "entity_id", "document_id"])):
        for c in cols:
            op.create_index(f"ix_{t}_{c}", t, [c])
    # Clients may see the fields of their own documents; entities/mentions span clients, so they are firm-only.
    for stmt in tenant_rls_sql("document_fields", client_column="client_id"):
        op.execute(stmt)
    for t in ("entities", "entity_mentions"):
        for stmt in tenant_rls_sql(t, firm_only=True):
            op.execute(stmt)


def downgrade() -> None:
    for t in ("entity_mentions", "entities", "document_fields"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
