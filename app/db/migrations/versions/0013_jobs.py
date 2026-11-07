"""Postgres-backed job queue (replaces Celery/Redis)

Revision ID: 0013
Revises: 0012
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("firm_id", pg.UUID(as_uuid=True)),
        sa.Column("status", sa.String(10), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("locked_by", sa.String(64)),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("dedupe_key", sa.String(200)),
        sa.Column("last_error", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('queued','running','done','dead')", name="status_valid"),
    )
    # the hot path: "next runnable job" is an index scan over queued rows only
    op.execute("CREATE INDEX ix_jobs_runnable ON jobs (priority DESC, run_at, id) WHERE status = 'queued'")
    op.execute("CREATE INDEX ix_jobs_leases ON jobs (locked_until) WHERE status = 'running'")
    op.execute("CREATE INDEX ix_jobs_finished ON jobs (finished_at) WHERE status IN ('done','dead')")
    # idempotent enqueue: at most one live (queued/running) job per dedupe key
    op.execute("CREATE UNIQUE INDEX uq_jobs_dedupe_live ON jobs (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued','running')")
    op.create_table(
        "job_schedule",
        sa.Column("kind", sa.String(64), primary_key=True),
        sa.Column("last_run", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS job_schedule")
    op.execute("DROP TABLE IF EXISTS jobs")
