"""invitations: revoke, and at most one open invitation per email per firm/client

Revision ID: 0015
Revises: 0014
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_NIL = "'00000000-0000-0000-0000-000000000000'::uuid"


def upgrade() -> None:
    op.execute("ALTER TABLE invitations ADD COLUMN revoked_at timestamptz, ADD COLUMN revoked_by uuid")
    # Existing duplicates (same email, same firm/client, still open): keep the newest, revoke the rest.
    op.execute(f"""
        UPDATE invitations i SET revoked_at = now()
        FROM (
            SELECT id, row_number() OVER (PARTITION BY firm_id, coalesce(client_id, {_NIL}), lower(email) ORDER BY created_at DESC) AS rn
            FROM invitations WHERE accepted_at IS NULL
        ) d
        WHERE i.id = d.id AND d.rn > 1
    """)
    op.execute(f"""
        CREATE UNIQUE INDEX uq_invitations_open ON invitations (firm_id, coalesce(client_id, {_NIL}), lower(email))
        WHERE accepted_at IS NULL AND revoked_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_invitations_open")
    op.execute("ALTER TABLE invitations DROP COLUMN IF EXISTS revoked_by, DROP COLUMN IF EXISTS revoked_at")
