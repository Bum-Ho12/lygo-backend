"""tenancy core: identity, firms, clients, matters, audit log + RLS

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from app.db.rls import DROP_FUNCTIONS_SQL, FUNCTIONS_SQL, tenant_rls_sql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _ts() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _id() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for stmt in FUNCTIONS_SQL:
        op.execute(stmt)

    op.create_table(
        "users", _id(),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("phone", sa.String(32)),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("password_hash", sa.Text),
        sa.Column("mfa_secret_enc", sa.LargeBinary),
        sa.Column("locale", sa.String(8), nullable=False, server_default="en"),
        *_ts(),
    )
    op.create_table(
        "firms", _id(),
        sa.Column("slug", sa.String(63), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False, server_default="standard"),
        sa.Column("region_pref", sa.String(32)),
        sa.Column("branding", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ai_settings", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("kms_key_ref", sa.String(200)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *_ts(),
    )
    op.create_table(
        "firm_memberships", _id(),
        sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        *_ts(),
        sa.UniqueConstraint("firm_id", "user_id"),
    )
    op.create_table(
        "clients", _id(),
        sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(16), nullable=False, server_default="individual"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("sensitivity", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("contacts_enc", sa.LargeBinary),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *_ts(),
    )
    op.create_index("ix_clients_firm_id", "clients", ["firm_id"])
    op.create_table(
        "client_memberships", _id(),
        sa.Column("client_id", pg.UUID(as_uuid=True), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        *_ts(),
        sa.UniqueConstraint("client_id", "user_id"),
    )
    op.create_table(
        "matters", _id(),
        sa.Column("firm_id", pg.UUID(as_uuid=True), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", pg.UUID(as_uuid=True), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("practice_area", sa.String(64), nullable=False, server_default="general"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("assigned_user_ids", pg.ARRAY(pg.UUID(as_uuid=True)), nullable=False, server_default=sa.text("'{}'::uuid[]")),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        *_ts(),
    )
    op.create_index("ix_matters_firm_id", "matters", ["firm_id"])
    op.create_index("ix_matters_client_id", "matters", ["client_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("firm_id", pg.UUID(as_uuid=True)),
        sa.Column("actor_id", pg.UUID(as_uuid=True)),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target", sa.String(300)),
        sa.Column("ip", sa.String(64)),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("meta", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_audit_events_firm_id", "audit_events", ["firm_id"])

    # --- RLS (same migration as the tables: CONTRIBUTING rule) ---
    for stmt in tenant_rls_sql("clients", client_column="id"):
        op.execute(stmt)
    for stmt in tenant_rls_sql("matters", client_column="client_id"):
        op.execute(stmt)

    # firms: members see their own firm; only firm roles (not clients) update; platform manages all.
    op.execute("ALTER TABLE firms ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE firms FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY firm_self_select ON firms FOR SELECT USING (id = app_firm_id())")
    op.execute(
        "CREATE POLICY firm_self_update ON firms FOR UPDATE "
        "USING (id = app_firm_id() AND app_client_id() IS NULL) "
        "WITH CHECK (id = app_firm_id() AND app_client_id() IS NULL)"
    )
    op.execute("CREATE POLICY platform_all ON firms FOR ALL USING (app_is_platform()) WITH CHECK (app_is_platform())")

    # audit_events: append-only. Firm sees its own; platform sees/writes firm-less events.
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY audit_select ON audit_events FOR SELECT USING "
        "(app_client_id() IS NULL AND (firm_id = app_firm_id() OR (firm_id IS NULL AND app_is_platform())))"
    )
    op.execute(
        "CREATE POLICY audit_insert ON audit_events FOR INSERT WITH CHECK "
        "(firm_id = app_firm_id() OR (firm_id IS NULL AND app_is_platform()))"
    )
    op.execute(
        """CREATE FUNCTION audit_events_immutable() RETURNS trigger LANGUAGE plpgsql AS
           $$ BEGIN RAISE EXCEPTION 'audit_events is append-only'; END $$"""
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_change BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_immutable()"
    )
    op.execute(
        """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lygo_app') THEN
               REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM lygo_app;
             END IF;
           END $$"""
    )


def downgrade() -> None:
    for t in ("audit_events", "matters", "client_memberships", "clients", "firm_memberships", "firms", "users"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS audit_events_immutable()")
    for stmt in DROP_FUNCTIONS_SQL:
        op.execute(stmt)
