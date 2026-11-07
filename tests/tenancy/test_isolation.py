"""Cross-tenant isolation. See docs/03-multi-tenancy.md and docs/13-testing-strategy.md.

Two firms x two clients, exercised through the real non-owner application role. These tests are a
required CI check: a failure here means tenant data could leak.
"""
import uuid

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.tenancy import TenantContext, tenant_session
from app.db.rls import tenant_rls_sql
from app.domain.audit.service import record
from tests.conftest import TEST_DB, PgConfig, World

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def factory(database: PgConfig):  # type: ignore[no-untyped-def]
    engine = create_async_engine(database.app_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def firm_ctx(w: World, firm: uuid.UUID, user: uuid.UUID, role: str = "advocate") -> TenantContext:
    return TenantContext(firm_id=firm, user_id=user, role=role)


async def ids(session, sql: str, **params) -> set:  # type: ignore[no-untyped-def]
    return {r[0] for r in (await session.execute(text(sql), params)).all()}


# --- the application role itself -------------------------------------------------------------
async def test_app_role_cannot_bypass_rls(factory) -> None:  # type: ignore[no-untyped-def]
    async with factory() as s:
        row = (await s.execute(text(
            "SELECT r.rolsuper, r.rolbypassrls, (SELECT tableowner = current_user FROM pg_tables WHERE tablename='clients')"
            " FROM pg_roles r WHERE r.rolname = current_user"
        ))).one()
    assert row == (False, False, False)


# --- firm isolation --------------------------------------------------------------------------
async def test_firm_sees_only_own_clients_and_matters(factory, world: World) -> None:  # type: ignore[no-untyped-def]
    async with tenant_session(factory, firm_ctx(world, world.firm_a, world.user_a)) as s:
        assert await ids(s, "SELECT id FROM clients") == {world.a1, world.a2}
        assert len(await ids(s, "SELECT id FROM matters")) == 4
        assert await ids(s, "SELECT DISTINCT firm_id FROM matters") == {world.firm_a}
    async with tenant_session(factory, firm_ctx(world, world.firm_b, world.user_b)) as s:
        assert await ids(s, "SELECT id FROM clients") == {world.b1, world.b2}


async def test_foreign_row_not_readable_by_id(factory, world: World) -> None:  # type: ignore[no-untyped-def]
    async with tenant_session(factory, firm_ctx(world, world.firm_a, world.user_a)) as s:
        assert await ids(s, "SELECT id FROM clients WHERE id = :i", i=world.b1) == set()


async def test_cannot_insert_for_another_firm(factory, world: World) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(factory, firm_ctx(world, world.firm_a, world.user_a)) as s:
            await s.execute(
                text("INSERT INTO clients (firm_id, name) VALUES (:f, 'evil')"), {"f": world.firm_b}
            )


async def test_cannot_update_or_delete_foreign_rows(factory, world: World) -> None:  # type: ignore[no-untyped-def]
    async with tenant_session(factory, firm_ctx(world, world.firm_a, world.user_a)) as s:
        upd = await s.execute(text("UPDATE clients SET name = 'x' WHERE id = :i"), {"i": world.b1})
        dele = await s.execute(text("DELETE FROM matters WHERE client_id = :i"), {"i": world.b1})
        assert upd.rowcount == 0 and dele.rowcount == 0


async def test_cannot_move_own_row_to_another_firm(factory, world: World) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(factory, firm_ctx(world, world.firm_a, world.user_a)) as s:
            await s.execute(text("UPDATE clients SET firm_id = :f WHERE id = :i"), {"f": world.firm_b, "i": world.a1})


async def test_firm_row_visibility_and_update(factory, world: World) -> None:  # type: ignore[no-untyped-def]
    async with tenant_session(factory, firm_ctx(world, world.firm_a, world.user_a)) as s:
        assert await ids(s, "SELECT id FROM firms") == {world.firm_a}
        assert (await s.execute(text("UPDATE firms SET name = 'Firm A Ltd' WHERE id = :i"), {"i": world.firm_a})).rowcount == 1
        assert (await s.execute(text("UPDATE firms SET name = 'hacked' WHERE id = :i"), {"i": world.firm_b})).rowcount == 0


# --- client (sub-tenant) isolation -----------------------------------------------------------
async def test_client_user_sees_only_own_client(factory, world: World) -> None:  # type: ignore[no-untyped-def]
    ctx = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    async with tenant_session(factory, ctx) as s:
        assert await ids(s, "SELECT id FROM clients") == {world.a1}
        assert await ids(s, "SELECT DISTINCT client_id FROM matters") == {world.a1}
        assert len(await ids(s, "SELECT id FROM matters")) == 2


async def test_client_user_cannot_write_for_sibling_client_or_edit_firm(factory, world: World) -> None:  # type: ignore[no-untyped-def]
    ctx = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(factory, ctx) as s:
            await s.execute(
                text("INSERT INTO matters (firm_id, client_id, title) VALUES (:f, :c, 'x')"),
                {"f": world.firm_a, "c": world.a2},
            )
    async with tenant_session(factory, ctx) as s:
        assert (await s.execute(text("UPDATE firms SET name='x' WHERE id = :i"), {"i": world.firm_a})).rowcount == 0


# --- missing / leaked context ----------------------------------------------------------------
async def test_no_context_returns_nothing_not_everything(factory, world: World) -> None:  # type: ignore[no-untyped-def]
    async with factory() as s:
        for table in ("clients", "matters", "firms", "audit_events"):
            assert (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0


async def test_context_does_not_leak_across_pooled_connections(database: PgConfig, world: World) -> None:
    engine = create_async_engine(database.app_url, pool_size=1, max_overflow=0)
    f = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with tenant_session(f, firm_ctx(world, world.firm_a, world.user_a)) as s:
            assert len(await ids(s, "SELECT id FROM clients")) == 2
        async with f() as s:  # same single pooled connection
            assert (await s.execute(text("SELECT app_firm_id()"))).scalar_one() is None
            assert (await s.execute(text("SELECT count(*) FROM clients"))).scalar_one() == 0
    finally:
        await engine.dispose()


# --- platform --------------------------------------------------------------------------------
async def test_platform_manages_firms_but_sees_no_firm_content(factory, world: World) -> None:  # type: ignore[no-untyped-def]
    ctx = TenantContext(firm_id=None, user_id=world.platform_user, role="platform_admin")
    async with tenant_session(factory, ctx) as s:
        assert {world.firm_a, world.firm_b} <= await ids(s, "SELECT id FROM firms")
        assert (await s.execute(text("SELECT count(*) FROM clients"))).scalar_one() == 0
        assert (await s.execute(text("SELECT count(*) FROM matters"))).scalar_one() == 0
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(factory, ctx) as s:
            await s.execute(text("INSERT INTO clients (firm_id, name) VALUES (:f, 'x')"), {"f": world.firm_a})


def test_tenant_context_validation() -> None:
    u = uuid.uuid4()
    with pytest.raises(ValueError):
        TenantContext(firm_id=None, user_id=u, role="advocate")
    with pytest.raises(ValueError):
        TenantContext(firm_id=None, user_id=u, role="platform_admin", client_id=u)
    TenantContext(firm_id=None, user_id=u, role="platform_support")


# --- audit log -------------------------------------------------------------------------------
async def test_audit_is_firm_scoped_and_append_only(factory, database: PgConfig, world: World) -> None:  # type: ignore[no-untyped-def]
    ctx_a = firm_ctx(world, world.firm_a, world.user_a)
    async with tenant_session(factory, ctx_a) as s:
        await record(s, ctx_a, "test.audit.a", target="client:1", meta={"k": "v"})
    async with tenant_session(factory, ctx_a) as s:
        assert await ids(s, "SELECT action FROM audit_events WHERE action = 'test.audit.a'") == {"test.audit.a"}
    async with tenant_session(factory, firm_ctx(world, world.firm_b, world.user_b)) as s:
        assert await ids(s, "SELECT action FROM audit_events WHERE action = 'test.audit.a'") == set()
    with pytest.raises(DBAPIError):  # no UPDATE privilege for the app role
        async with tenant_session(factory, ctx_a) as s:
            await s.execute(text("UPDATE audit_events SET action = 'tampered'"))
    with pytest.raises(DBAPIError):
        async with tenant_session(factory, ctx_a) as s:
            await s.execute(text("DELETE FROM audit_events"))
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c, pytest.raises(psycopg.Error, match="append-only"):
        c.execute("UPDATE audit_events SET action = 'tampered'")  # even the owner is blocked by the trigger


async def test_cannot_write_audit_for_another_firm(factory, world: World) -> None:  # type: ignore[no-untyped-def]
    ctx_a = firm_ctx(world, world.firm_a, world.user_a)
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(factory, ctx_a) as s:
            await s.execute(text("INSERT INTO audit_events (firm_id, action) VALUES (:f, 'forged')"), {"f": world.firm_b})


# --- RLS helper variants (firm_only for cross-client artifacts, public scope for the corpus) ---
async def test_firm_only_and_public_scope_policies(factory, database: PgConfig, world: World) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        c.execute("CREATE TABLE scratch_cmp (id serial primary key, firm_id uuid, client_id uuid, v text)")
        for stmt in tenant_rls_sql("scratch_cmp", firm_only=True):
            c.execute(stmt)
        c.execute("CREATE TABLE scratch_chunks (id serial primary key, firm_id uuid, client_id uuid, scope text, v text)")
        for stmt in tenant_rls_sql("scratch_chunks", client_column="client_id", public_scope=True):
            c.execute(stmt)
        c.execute("INSERT INTO scratch_cmp (firm_id, v) VALUES (%s, 'a'), (%s, 'b')", (world.firm_a, world.firm_b))
        c.execute(
            "INSERT INTO scratch_chunks (firm_id, client_id, scope, v) VALUES "
            "(NULL, NULL, 'public', 'law'), (%s, %s, 'client', 'a1doc'), (%s, %s, 'client', 'b1doc')",
            (world.firm_a, world.a1, world.firm_b, world.b1),
        )
    firm = firm_ctx(world, world.firm_a, world.user_a)
    client = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    async with tenant_session(factory, firm) as s:
        assert await ids(s, "SELECT v FROM scratch_cmp") == {"a"}
        assert await ids(s, "SELECT v FROM scratch_chunks") == {"law", "a1doc"}
    async with tenant_session(factory, client) as s:
        assert await ids(s, "SELECT v FROM scratch_cmp") == set()  # firm-only: invisible to client users
        assert await ids(s, "SELECT v FROM scratch_chunks") == {"law", "a1doc"}  # public + own client only
    async with tenant_session(factory, firm_ctx(world, world.firm_b, world.user_b)) as s:
        assert await ids(s, "SELECT v FROM scratch_chunks") == {"law", "b1doc"}


# --- Phase B tables: deadlines, tasks, invitations -----------------------------------------------
@pytest.fixture(scope="session")
def phase_b_rows(database: PgConfig, world: World):  # type: ignore[no-untyped-def]
    """One matter-bound deadline, task and invitation per firm, seeded as owner."""
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        for firm, client in ((world.firm_a, world.a1), (world.firm_b, world.b1)):
            mid = c.execute("SELECT id FROM matters WHERE client_id=%s LIMIT 1", (client,)).fetchone()[0]
            c.execute(
                "INSERT INTO deadlines (firm_id, client_id, matter_id, title, due_at) VALUES (%s,%s,%s,%s, now())",
                (firm, client, mid, f"dl-{firm.hex[:6]}"),
            )
            c.execute("INSERT INTO tasks (firm_id, client_id, title) VALUES (%s,%s,%s)", (firm, client, f"task-{firm.hex[:6]}"))
            c.execute(
                "INSERT INTO invitations (firm_id, email, role, token_hash, expires_at) VALUES (%s,%s,'advocate',%s, now()+interval '1 day')",
                (firm, f"inv-{firm.hex[:6]}@example.com", f"hash-{firm.hex}"),
            )


async def test_deadlines_follow_client_isolation_tasks_and_invitations_are_firm_only(factory, world: World, phase_b_rows) -> None:  # type: ignore[no-untyped-def]
    firm_a = firm_ctx(world, world.firm_a, world.user_a)
    client_a1 = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    async with tenant_session(factory, firm_a) as s:
        assert len(await ids(s, "SELECT id FROM deadlines")) >= 1
        assert await ids(s, "SELECT DISTINCT firm_id FROM tasks") == {world.firm_a}
        assert await ids(s, "SELECT DISTINCT firm_id FROM invitations") == {world.firm_a}
    async with tenant_session(factory, client_a1) as s:
        assert await ids(s, "SELECT DISTINCT client_id FROM deadlines") == {world.a1}  # own client's only
        assert await ids(s, "SELECT id FROM tasks") == set()
        assert await ids(s, "SELECT id FROM invitations") == set()


async def test_invitation_token_policy_exposes_only_the_presented_hash(factory, world: World, phase_b_rows) -> None:  # type: ignore[no-untyped-def]
    async with factory() as s, s.begin():  # invitee: no tenant context at all
        assert await ids(s, "SELECT email FROM invitations") == set()
        await s.execute(text("SELECT set_config('app.invite_token_hash', :h, true)"), {"h": f"hash-{world.firm_b.hex}"})
        assert await ids(s, "SELECT firm_id FROM invitations") == {world.firm_b}  # exactly one row
    async with factory() as s, s.begin():
        assert await ids(s, "SELECT email FROM invitations") == set()  # setting was transaction-local


async def test_platform_may_insert_but_not_read_invitations(factory, world: World) -> None:  # type: ignore[no-untyped-def]
    ctx = TenantContext(firm_id=None, user_id=world.platform_user, role="platform_admin")
    async with tenant_session(factory, ctx) as s:
        await s.execute(
            text("INSERT INTO invitations (firm_id, email, role, token_hash, expires_at) VALUES (:f,'o@example.com','firm_owner',:h, now()+interval '1 day')"),
            {"f": world.firm_a, "h": f"plat-{uuid.uuid4().hex}"},
        )
        assert (await s.execute(text("SELECT count(*) FROM invitations"))).scalar_one() == 0
    with pytest.raises(DBAPIError, match="row-level security"):  # a firm member cannot invite into another firm
        async with tenant_session(factory, firm_ctx(world, world.firm_a, world.user_a)) as s:
            await s.execute(
                text("INSERT INTO invitations (firm_id, email, role, token_hash, expires_at) VALUES (:f,'o@example.com','x',:h, now())"),
                {"f": world.firm_b, "h": f"forged-{uuid.uuid4().hex}"},
            )


# --- Phase C tables: documents (shared knowledge base is read-only for clients) -----------------------
@pytest.fixture(scope="session")
def doc_rows(database: PgConfig, world: World):  # type: ignore[no-untyped-def]
    ids_: dict[str, uuid.UUID] = {}
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        def add(label: str, firm: uuid.UUID, client: uuid.UUID | None, scope: str, shared: bool = False) -> None:
            did = uuid.uuid4()
            ids_[label] = did
            c.execute(
                "INSERT INTO documents (id, firm_id, client_id, scope, title, original_filename, mime_type, size_bytes, sha256,"
                " storage_destination_id, storage_key, shared_with_clients) VALUES (%s,%s,%s,%s,%s,'f.txt','text/plain',1,%s,'t',%s,%s)",
                (did, firm, client, scope, label, uuid.uuid4().hex * 2, f"k/{did}", shared),
            )
            c.execute("INSERT INTO pages (firm_id, client_id, document_id, n, text) VALUES (%s,%s,%s,1,%s)", (firm, client, did, label))

        add("a1_doc", world.firm_a, world.a1, "client")
        add("a2_doc", world.firm_a, world.a2, "client")
        add("a_kb_private", world.firm_a, None, "firm_kb")
        add("a_kb_shared", world.firm_a, None, "firm_kb", shared=True)
        add("b_kb_shared", world.firm_b, None, "firm_kb", shared=True)
    return ids_


async def test_client_reads_own_and_shared_kb_documents_only(factory, world: World, doc_rows) -> None:  # type: ignore[no-untyped-def]
    ctx = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    async with tenant_session(factory, ctx) as s:
        assert await ids(s, "SELECT title FROM documents") == {"a1_doc", "a_kb_shared"}
        # derived rows are NOT shared: a client sees pages of its own documents only, never the KB's text
        assert await ids(s, "SELECT text FROM pages") == {"a1_doc"}


async def test_client_cannot_modify_or_create_kb_documents(factory, world: World, doc_rows) -> None:  # type: ignore[no-untyped-def]
    ctx = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    async with tenant_session(factory, ctx) as s:
        assert (await s.execute(text("UPDATE documents SET title='pwned' WHERE title='a_kb_shared'"))).rowcount == 0
        assert (await s.execute(text("DELETE FROM documents WHERE title='a_kb_shared'"))).rowcount == 0
        assert (await s.execute(text("UPDATE documents SET shared_with_clients=false WHERE title='a_kb_shared'"))).rowcount == 0
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(factory, ctx) as s:
            await s.execute(text(
                "INSERT INTO documents (firm_id, scope, title, original_filename, mime_type, size_bytes, sha256, storage_destination_id, storage_key)"
                " VALUES (:f,'firm_kb','evil','f','text/plain',1,:h,'t','k')"), {"f": world.firm_a, "h": uuid.uuid4().hex * 2})
    with pytest.raises(DBAPIError, match="row-level security"):  # and cannot file a document under a sibling client
        async with tenant_session(factory, ctx) as s:
            await s.execute(text(
                "INSERT INTO documents (firm_id, client_id, scope, title, original_filename, mime_type, size_bytes, sha256, storage_destination_id, storage_key)"
                " VALUES (:f,:c,'client','evil','f','text/plain',1,:h,'t','k')"), {"f": world.firm_a, "c": world.a2, "h": uuid.uuid4().hex * 2})


async def test_firm_sees_all_own_documents_and_none_of_others(factory, world: World, doc_rows) -> None:  # type: ignore[no-untyped-def]
    async with tenant_session(factory, firm_ctx(world, world.firm_a, world.user_a)) as s:
        assert await ids(s, "SELECT title FROM documents") == {"a1_doc", "a2_doc", "a_kb_private", "a_kb_shared"}
    async with tenant_session(factory, firm_ctx(world, world.firm_b, world.user_b)) as s:
        assert await ids(s, "SELECT title FROM documents") == {"b_kb_shared"}
        assert (await s.execute(text("UPDATE documents SET title='x' WHERE title='a_kb_shared'"))).rowcount == 0


# --- notifications: firm-only, token-scoped delivery-report access, read-only sweeper ------------------
@pytest.fixture(scope="session")
def notif_rows(database: PgConfig, world: World):  # type: ignore[no-untyped-def]
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        for firm in (world.firm_a, world.firm_b):
            c.execute(
                "INSERT INTO notifications (firm_id, template, template_version, to_masked, token_hash, deliver_after, status)"
                " VALUES (%s,'otp',1,'+25471***78',%s, now() - interval '1 hour', 'queued')",
                (firm, f"tok-{firm.hex}"),
            )


async def test_notifications_are_firm_only_and_isolated(factory, world: World, notif_rows) -> None:  # type: ignore[no-untyped-def]
    async with tenant_session(factory, firm_ctx(world, world.firm_a, world.user_a)) as s:
        assert await ids(s, "SELECT DISTINCT firm_id FROM notifications") == {world.firm_a}
    client = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    async with tenant_session(factory, client) as s:
        assert await ids(s, "SELECT id FROM notifications") == set()
    async with factory() as s:
        assert (await s.execute(text("SELECT count(*) FROM notifications"))).scalar_one() == 0  # no context -> nothing


async def test_dlr_token_policy_and_sweeper_are_narrow(factory, world: World, notif_rows) -> None:  # type: ignore[no-untyped-def]
    async with factory() as s, s.begin():  # provider callback: no tenant, one token
        await s.execute(text("SELECT set_config('app.dlr_token_hash', :h, true)"), {"h": f"tok-{world.firm_b.hex}"})
        assert await ids(s, "SELECT firm_id FROM notifications") == {world.firm_b}
        assert (await s.execute(text("UPDATE notifications SET status='delivered' WHERE token_hash = :h"), {"h": f"tok-{world.firm_a.hex}"})).rowcount == 0
    sweeper = TenantContext(firm_id=None, user_id=world.platform_user, role="system_sweeper")
    async with tenant_session(factory, sweeper) as s:
        assert {world.firm_a, world.firm_b} <= await ids(s, "SELECT firm_id FROM notifications WHERE status='queued'")
        assert (await s.execute(text("UPDATE notifications SET status='failed'"))).rowcount == 0  # read-only
        assert (await s.execute(text("SELECT count(*) FROM clients"))).scalar_one() == 0  # and sees nothing else
    platform = TenantContext(firm_id=None, user_id=world.platform_user, role="platform_admin")
    async with tenant_session(factory, platform) as s:
        assert (await s.execute(text("SELECT count(*) FROM notifications"))).scalar_one() == 0  # platform staff get no content
