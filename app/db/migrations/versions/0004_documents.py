"""documents, pages, sections, flags, ingest events (+ RLS)

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from app.db.rls import tenant_rls_sql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _id() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _ts() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _firm() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False)


def _doc() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("document_id", pg.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "documents", _id(), _firm(),
        sa.Column("client_id", pg.UUID(as_uuid=True), sa.ForeignKey("clients.id", ondelete="CASCADE")),
        sa.Column("matter_id", pg.UUID(as_uuid=True), sa.ForeignKey("matters.id", ondelete="SET NULL")),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("original_filename", sa.String(300), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_destination_id", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="received"),
        sa.Column("status_detail", sa.Text, nullable=False, server_default=""),
        sa.Column("doc_type", sa.String(64)),
        sa.Column("doc_type_confidence", sa.Float),
        sa.Column("practice_area", sa.String(64)),
        sa.Column("tags", pg.ARRAY(sa.String), nullable=False, server_default=sa.text("'{}'::varchar[]")),
        sa.Column("sensitivity", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("language", sa.String(8)),
        sa.Column("page_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("shared_with_clients", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("version_group_id", pg.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("version_no", sa.Integer, nullable=False, server_default="1"),
        sa.Column("uploaded_by", pg.UUID(as_uuid=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *_ts(),
        sa.CheckConstraint("scope IN ('firm_kb','client')", name="scope_valid"),
        sa.CheckConstraint("(scope = 'client') = (client_id IS NOT NULL)", name="scope_client_consistent"),
    )
    for col in ("firm_id", "client_id", "version_group_id"):
        op.create_index(f"ix_documents_{col}", "documents", [col])
    # Deduplicate identical content per (firm, client|KB): NULL client_id is mapped to a fixed UUID.
    op.execute(
        "CREATE UNIQUE INDEX uq_documents_dedup ON documents "
        "(firm_id, COALESCE(client_id, '00000000-0000-0000-0000-000000000000'::uuid), sha256) WHERE deleted_at IS NULL"
    )

    op.create_table(
        "pages", _id(), _firm(), sa.Column("client_id", pg.UUID(as_uuid=True)), _doc(),
        sa.Column("n", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("ocr_confidence", sa.Float),
        sa.Column("needs_review", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("method", sa.String(8), nullable=False, server_default="text"),
    )
    op.create_table(
        "document_sections", _id(), _firm(), sa.Column("client_id", pg.UUID(as_uuid=True)), _doc(),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("number", sa.String(32), nullable=False, server_default=""),
        sa.Column("heading", sa.String(300), nullable=False, server_default=""),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("page", sa.Integer),
        sa.Column("path", sa.String(200), nullable=False, server_default=""),
    )
    op.create_table(
        "flags", _id(), _firm(), sa.Column("client_id", pg.UUID(as_uuid=True)), _doc(),
        sa.Column("rule_id", sa.String(100), nullable=False),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("pack_version", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("explanation", sa.Text, nullable=False),
        sa.Column("span", pg.JSONB),
        sa.Column("extracted", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_refs", pg.ARRAY(sa.String), nullable=False, server_default=sa.text("'{}'::varchar[]")),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resolved_by", pg.UUID(as_uuid=True)),
        sa.Column("resolution_note", sa.Text, nullable=False, server_default=""),
        *_ts(),
    )
    op.create_table(
        "ingest_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True), _firm(),
        sa.Column("client_id", pg.UUID(as_uuid=True)), _doc(),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for t in ("pages", "document_sections", "flags", "ingest_events"):
        op.create_index(f"ix_{t}_firm_id", t, ["firm_id"])
        op.create_index(f"ix_{t}_document_id", t, ["document_id"])

    for stmt in tenant_rls_sql("documents", client_column="client_id", shared_kb=True):
        op.execute(stmt)
    for t in ("pages", "document_sections", "flags", "ingest_events"):
        for stmt in tenant_rls_sql(t, client_column="client_id"):
            op.execute(stmt)


def downgrade() -> None:
    for t in ("ingest_events", "flags", "document_sections", "pages", "documents"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
