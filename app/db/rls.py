"""Row-Level Security helpers. See docs/03-multi-tenancy.md and ADR 0001.

Every tenant-owned table gets its policies from `tenant_rls_sql` *in the same migration* that
creates it. The policies read session variables set by `app.core.tenancy` via helper functions that
treat unset/empty values as NULL, so a missing context returns zero rows instead of raising.
"""

FUNCTIONS_SQL = [
    """CREATE OR REPLACE FUNCTION app_firm_id() RETURNS uuid LANGUAGE sql STABLE AS
       $$ SELECT NULLIF(current_setting('app.firm_id', true), '')::uuid $$""",
    """CREATE OR REPLACE FUNCTION app_client_id() RETURNS uuid LANGUAGE sql STABLE AS
       $$ SELECT NULLIF(current_setting('app.client_id', true), '')::uuid $$""",
    """CREATE OR REPLACE FUNCTION app_role() RETURNS text LANGUAGE sql STABLE AS
       $$ SELECT NULLIF(current_setting('app.role', true), '') $$""",
    """CREATE OR REPLACE FUNCTION app_is_platform() RETURNS boolean LANGUAGE sql STABLE AS
       $$ SELECT COALESCE(app_role() IN ('platform_admin', 'platform_support'), false) $$""",
]
DROP_FUNCTIONS_SQL = [
    "DROP FUNCTION IF EXISTS app_is_platform()",
    "DROP FUNCTION IF EXISTS app_role()",
    "DROP FUNCTION IF EXISTS app_client_id()",
    "DROP FUNCTION IF EXISTS app_firm_id()",
]


def tenant_rls_sql(
    table: str,
    *,
    client_column: str | None = None,
    firm_only: bool = False,
    public_scope: bool = False,
    shared_kb: bool = False,
) -> list[str]:
    """SQL enabling and forcing RLS on `table`.

    client_column: column compared with the caller's client id (client users only see their own
        rows). Rows with a NULL value are hidden from client users.
    firm_only: client users see nothing (e.g. comparisons spanning several clients, ADR 0005).
    public_scope: also allow everyone to read rows with scope='public' and firm_id IS NULL.
    shared_kb: client users may additionally READ (never write) rows with scope='firm_kb' AND
        shared_with_clients, i.e. firm knowledge the firm chose to publish to its clients.
    """
    stmts = [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        (
            f"CREATE POLICY firm_isolation ON {table} FOR ALL "
            f"USING (firm_id = app_firm_id()) WITH CHECK (firm_id = app_firm_id())"
        ),
    ]
    if public_scope:
        stmts.append(
            f"CREATE POLICY public_read ON {table} FOR SELECT "
            f"USING (scope = 'public' AND firm_id IS NULL)"
        )
    if firm_only:
        stmts.append(
            f"CREATE POLICY firm_only ON {table} AS RESTRICTIVE FOR ALL "
            f"USING (app_client_id() IS NULL) WITH CHECK (app_client_id() IS NULL)"
        )
    elif client_column:
        own = f"app_client_id() IS NULL OR {client_column} = app_client_id()"
        # Public-corpus rows have no client; client users must still be able to read them.
        read_extra = " OR (scope = 'public' AND firm_id IS NULL)" if public_scope else ""
        if shared_kb:
            read_extra += " OR (scope = 'firm_kb' AND shared_with_clients)"
        if read_extra:
            # Split by command so the extra visibility is read-only.
            stmts.append(f"CREATE POLICY client_isolation_select ON {table} AS RESTRICTIVE FOR SELECT USING ({own}{read_extra})")
            stmts.append(f"CREATE POLICY client_isolation_insert ON {table} AS RESTRICTIVE FOR INSERT WITH CHECK ({own})")
            stmts.append(f"CREATE POLICY client_isolation_update ON {table} AS RESTRICTIVE FOR UPDATE USING ({own}) WITH CHECK ({own})")
            stmts.append(f"CREATE POLICY client_isolation_delete ON {table} AS RESTRICTIVE FOR DELETE USING ({own})")
        else:
            stmts.append(
                f"CREATE POLICY client_isolation ON {table} AS RESTRICTIVE FOR ALL "
                f"USING ({own}) WITH CHECK ({own})"
            )
    return stmts
