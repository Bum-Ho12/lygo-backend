"""self-service firm applications

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "firm_applications",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("applicant_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("firm_name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("practice_areas", pg.ARRAY(sa.String), nullable=False, server_default=sa.text("'{}'::varchar[]")),
        sa.Column("lsk_admission_no", sa.String(40)),
        sa.Column("phone", sa.String(20)),
        sa.Column("town", sa.String(80)),
        sa.Column("size", sa.String(10), nullable=False),
        sa.Column("terms_version", sa.String(40), nullable=False),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("review_note", sa.Text, nullable=False, server_default=""),
        sa.Column("reviewed_by", pg.UUID(as_uuid=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending','approved','rejected','withdrawn')", name="status_valid"),
    )
    op.create_index("ix_firm_applications_applicant_id", "firm_applications", ["applicant_id"])
    op.execute("CREATE INDEX ix_firm_applications_pending ON firm_applications (created_at) WHERE status = 'pending'")
    # one open application per person, and no two open applications for the same slug
    op.execute("CREATE UNIQUE INDEX uq_firm_applications_one_pending ON firm_applications (applicant_id) WHERE status = 'pending'")
    op.execute("CREATE UNIQUE INDEX uq_firm_applications_slug_pending ON firm_applications (slug) WHERE status = 'pending'")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS firm_applications")
