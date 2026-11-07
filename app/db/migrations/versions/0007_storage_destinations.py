"""BYO storage destinations and per-firm wrapped data keys

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from app.db.rls import tenant_rls_sql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("firms", sa.Column("wrapped_dek", sa.LargeBinary))
    op.create_table(
        "storage_destinations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False, server_default="s3_compatible"),
        sa.Column("region_country", sa.String(2), nullable=False),
        sa.Column("transfer_basis", sa.String(500), nullable=False, server_default=""),
        sa.Column("config", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("credentials_enc", sa.LargeBinary, nullable=False),
        sa.Column("health", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("firm_id", "name"),
    )
    op.create_index("ix_storage_destinations_firm_id", "storage_destinations", ["firm_id"])
    op.execute("CREATE UNIQUE INDEX uq_storage_one_primary ON storage_destinations (firm_id) WHERE is_primary")
    for stmt in tenant_rls_sql("storage_destinations", firm_only=True):
        op.execute(stmt)
    # Workers (role 'worker', firm context) read destinations through the same firm_isolation policy; no extra access.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS storage_destinations CASCADE")
    op.drop_column("firms", "wrapped_dek")
