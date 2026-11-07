"""corpus documents and chunks (pgvector + full text) with RLS

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

from app.db.rls import tenant_rls_sql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

DIM = 1024  # must match settings.embedding_dim; changing the model dimension requires a re-index migration


def upgrade() -> None:
    op.create_table(
        "corpus_documents",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("manifest_id", sa.String(100), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("jurisdiction", sa.String(8), nullable=False, server_default="KE"),
        sa.Column("cap_no", sa.String(32)),
        sa.Column("version_date", sa.Date, nullable=False),
        sa.Column("effective_from", sa.Date),
        sa.Column("effective_to", sa.Date),
        sa.Column("source_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("licence", sa.String(200), nullable=False, server_default=""),
        sa.Column("licence_confirmed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("checksum", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("manifest_id", "version_date"),
    )
    op.create_index("ix_corpus_documents_manifest_id", "corpus_documents", ["manifest_id"])
    # Public law: everyone may read; only platform roles may write.
    op.execute("ALTER TABLE corpus_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE corpus_documents FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY corpus_read ON corpus_documents FOR SELECT USING (true)")
    op.execute("CREATE POLICY corpus_write ON corpus_documents FOR ALL USING (app_is_platform()) WITH CHECK (app_is_platform())")

    op.create_table(
        "chunks",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="CASCADE")),
        sa.Column("client_id", pg.UUID(as_uuid=True)),
        sa.Column("matter_id", pg.UUID(as_uuid=True)),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("shared_with_clients", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("section_ref", sa.String(100), nullable=False, server_default=""),
        sa.Column("heading", sa.String(300), nullable=False, server_default=""),
        sa.Column("page", sa.Integer),
        sa.Column("ord", sa.Integer, nullable=False, server_default="0"),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(DIM), nullable=False),
        sa.Column("embed_model", sa.String(100), nullable=False),
        sa.Column("tsv", pg.TSVECTOR, sa.Computed("to_tsvector('english', text)", persisted=True)),
        sa.Column("effective_from", sa.Date),
        sa.Column("effective_to", sa.Date),
        sa.Column("version_label", sa.String(32), nullable=False, server_default=""),
        sa.CheckConstraint("scope IN ('public','firm_kb','client')", name="scope_valid"),
        sa.CheckConstraint("(scope = 'public') = (firm_id IS NULL)", name="public_has_no_firm"),
        sa.CheckConstraint("(scope = 'client') = (client_id IS NOT NULL)", name="client_scope_has_client"),
    )
    op.create_index("ix_chunks_firm_id", "chunks", ["firm_id"])
    op.create_index("ix_chunks_source_id", "chunks", ["source_id"])
    op.execute("CREATE INDEX ix_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)")

    for stmt in tenant_rls_sql("chunks", client_column="client_id", public_scope=True, shared_kb=True):
        op.execute(stmt)
    # Corpus loading: platform staff write public chunks (firm_id IS NULL), which firm_isolation cannot allow.
    op.execute(
        "CREATE POLICY platform_public_write ON chunks FOR ALL "
        "USING (app_is_platform() AND scope = 'public' AND firm_id IS NULL) "
        "WITH CHECK (app_is_platform() AND scope = 'public' AND firm_id IS NULL)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chunks CASCADE")
    op.execute("DROP TABLE IF EXISTS corpus_documents CASCADE")
