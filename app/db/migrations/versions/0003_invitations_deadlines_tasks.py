"""invitations, deadlines, tasks (+ RLS)

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from app.db.rls import tenant_rls_sql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _id() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _ts() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _fk(name: str, table: str, *, nullable: bool = False, ondelete: str = "CASCADE") -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, pg.UUID(as_uuid=True), sa.ForeignKey(f"{table}.id", ondelete=ondelete), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "invitations", _id(),
        _fk("firm_id", "firms"), _fk("client_id", "clients", nullable=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("invited_by", pg.UUID(as_uuid=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        *_ts(),
    )
    op.create_index("ix_invitations_firm_id", "invitations", ["firm_id"])
    op.create_table(
        "deadlines", _id(),
        _fk("firm_id", "firms"), _fk("client_id", "clients"), _fk("matter_id", "matters"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="general"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("source_flag_id", pg.UUID(as_uuid=True)),
        sa.Column("reminded_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", pg.UUID(as_uuid=True)),
        *_ts(),
    )
    for col in ("firm_id", "client_id", "matter_id"):
        op.create_index(f"ix_deadlines_{col}", "deadlines", [col])
    op.create_table(
        "tasks", _id(),
        _fk("firm_id", "firms"), _fk("client_id", "clients", nullable=True, ondelete="SET NULL"),
        _fk("matter_id", "matters", nullable=True, ondelete="SET NULL"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("kind", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("assignee_id", pg.UUID(as_uuid=True)),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", pg.UUID(as_uuid=True)),
        *_ts(),
    )
    op.create_index("ix_tasks_firm_id", "tasks", ["firm_id"])

    for stmt in tenant_rls_sql("deadlines", client_column="client_id"):
        op.execute(stmt)
    for stmt in tenant_rls_sql("tasks", firm_only=True):  # internal work items: invisible to client users
        op.execute(stmt)

    # invitations: firm-only, plus two narrow extra paths that keep RLS forced:
    #  - platform staff may create the first owner invitation for a new firm (insert only);
    #  - an invitee (no tenant yet) may read/accept exactly the row whose token hash they present.
    for stmt in tenant_rls_sql("invitations", firm_only=True):
        op.execute(stmt)
    op.execute(
        "CREATE POLICY platform_insert ON invitations FOR INSERT WITH CHECK (app_is_platform())"
    )
    op.execute(
        "CREATE POLICY invitee_token ON invitations FOR ALL "
        "USING (token_hash = NULLIF(current_setting('app.invite_token_hash', true), '')) "
        "WITH CHECK (token_hash = NULLIF(current_setting('app.invite_token_hash', true), ''))"
    )


def downgrade() -> None:
    for t in ("tasks", "deadlines", "invitations"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
