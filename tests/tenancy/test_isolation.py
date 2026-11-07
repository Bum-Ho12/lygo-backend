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


# --- chunks (RAG): the most sensitive table ---------------------------------------------------------------
@pytest.fixture(scope="session")
def chunk_rows(database: PgConfig, world: World):  # type: ignore[no-untyped-def]
    vec = "[" + ",".join(["0.1"] * 1024) + "]"
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        def add(label: str, firm: uuid.UUID | None, client: uuid.UUID | None, scope: str, shared: bool = False) -> None:
            c.execute(
                "INSERT INTO chunks (firm_id, client_id, scope, shared_with_clients, source_type, source_id, title, text, embedding, embed_model)"
                " VALUES (%s,%s,%s,%s,'document',%s,%s,%s,%s::vector,'t')",
                (firm, client, scope, shared, uuid.uuid4(), label, label, vec),
            )

        add("law", None, None, "public")
        add("a1", world.firm_a, world.a1, "client")
        add("a2", world.firm_a, world.a2, "client")
        add("a_kb_private", world.firm_a, None, "firm_kb")
        add("a_kb_shared", world.firm_a, None, "firm_kb", shared=True)
        add("b1", world.firm_b, world.b1, "client")
        add("b_kb", world.firm_b, None, "firm_kb", shared=True)


CHUNK_LABELS = {"law", "a1", "a2", "a_kb_private", "a_kb_shared", "b1", "b_kb"}


async def test_chunk_visibility_matrix(factory, world: World, chunk_rows) -> None:  # type: ignore[no-untyped-def]
    async def titles(ctx: TenantContext) -> set[str]:
        # other tests in the session add their own (public) corpus rows; compare only this fixture's labels
        async with tenant_session(factory, ctx) as s:
            return await ids(s, "SELECT title FROM chunks") & CHUNK_LABELS

    assert await titles(firm_ctx(world, world.firm_a, world.user_a)) == {"law", "a1", "a2", "a_kb_private", "a_kb_shared"}
    assert await titles(firm_ctx(world, world.firm_b, world.user_b)) == {"law", "b1", "b_kb"}
    client_a1 = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    assert await titles(client_a1) == {"law", "a1", "a_kb_shared"}  # own + shared KB + public law, nothing else
    assert await titles(TenantContext(firm_id=None, user_id=world.platform_user, role="platform_admin")) == {"law"}  # staff: public only
    async with factory() as s:  # no context at all: public law only, never tenant rows
        assert await ids(s, "SELECT title FROM chunks") & CHUNK_LABELS == {"law"}


async def test_chunk_writes_are_confined(factory, world: World, chunk_rows) -> None:  # type: ignore[no-untyped-def]
    vec = "[" + ",".join(["0.1"] * 1024) + "]"
    ins = text(
        "INSERT INTO chunks (firm_id, client_id, scope, source_type, source_id, title, text, embedding, embed_model)"
        " VALUES (:f, :c, :s, 'document', :src, 'x', 'x', CAST(:v AS vector), 't')"
    )
    p = {"src": uuid.uuid4(), "v": vec}
    client_a1 = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    async with tenant_session(factory, client_a1) as s:  # clients cannot edit what they can read
        assert (await s.execute(text("UPDATE chunks SET text='pwned' WHERE title='a_kb_shared'"))).rowcount == 0
        assert (await s.execute(text("DELETE FROM chunks WHERE title IN ('law','a_kb_shared')"))).rowcount == 0
    for ctx, params in (
        (firm_ctx(world, world.firm_a, world.user_a), {"f": world.firm_b, "c": None, "s": "firm_kb"}),  # another firm
        (firm_ctx(world, world.firm_a, world.user_a), {"f": None, "c": None, "s": "public"}),  # a firm forging public law
    ):
        with pytest.raises(DBAPIError):
            async with tenant_session(factory, ctx) as s:
                await s.execute(ins, {**p, **params})
    platform = TenantContext(firm_id=None, user_id=world.platform_user, role="platform_admin")
    async with tenant_session(factory, platform) as s:
        await s.execute(ins, {**p, "f": None, "c": None, "s": "public"})  # staff may load public law
    with pytest.raises(DBAPIError):
        async with tenant_session(factory, platform) as s:
            await s.execute(ins, {**p, "f": world.firm_a, "c": None, "s": "firm_kb"})  # but never write tenant content


def test_chunk_table_constraints(database: PgConfig, world: World) -> None:
    vec = "[" + ",".join(["0.1"] * 1024) + "]"
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        for firm, client, scope in ((world.firm_a, None, "public"), (None, None, "firm_kb"), (world.firm_a, None, "client")):
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(
                    "INSERT INTO chunks (firm_id, client_id, scope, source_type, source_id, title, text, embedding, embed_model)"
                    " VALUES (%s,%s,%s,'document',%s,'x','x',%s::vector,'t')",
                    (firm, client, scope, uuid.uuid4(), vec),
                )


# --- Phase C gap tables: storage destinations, document fields, entities ----------------------------------
@pytest.fixture(scope="session")
def gap_rows(database: PgConfig, world: World):  # type: ignore[no-untyped-def]
    out: dict[str, uuid.UUID] = {}
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        for tag, firm, client in (("a1", world.firm_a, world.a1), ("a2", world.firm_a, world.a2), ("b1", world.firm_b, world.b1)):
            did = uuid.uuid4()
            out[tag] = did
            c.execute(
                "INSERT INTO documents (id, firm_id, client_id, scope, title, original_filename, mime_type, size_bytes, sha256, storage_destination_id, storage_key)"
                " VALUES (%s,%s,%s,'client',%s,'f','text/plain',1,%s,'t','k')", (did, firm, client, f"gap-{tag}", uuid.uuid4().hex * 2),
            )
            c.execute("INSERT INTO document_fields (firm_id, client_id, document_id, kind, key, value, normalized) VALUES (%s,%s,%s,'money',%s,'v','v')", (firm, client, did, f"field-{tag}"))
            eid = c.execute(
                "INSERT INTO entities (firm_id, kind, name_norm, display_name) VALUES (%s,'person',%s,%s) RETURNING id", (firm, f"person {tag}", f"Person {tag}")
            ).fetchone()[0]
            c.execute("INSERT INTO entity_mentions (firm_id, client_id, entity_id, document_id) VALUES (%s,%s,%s,%s)", (firm, client, eid, did))
        for firm, name in ((world.firm_a, "dest-a"), (world.firm_b, "dest-b")):
            c.execute("INSERT INTO storage_destinations (firm_id, name, region_country, credentials_enc) VALUES (%s,%s,'KE',%s)", (firm, name, b"\x01cipher"))
    return out


async def test_fields_follow_client_scope_while_entities_and_destinations_are_firm_only(factory, world: World, gap_rows) -> None:  # type: ignore[no-untyped-def]
    firm_a = firm_ctx(world, world.firm_a, world.user_a)
    client_a1 = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    async with tenant_session(factory, firm_a) as s:
        assert await ids(s, "SELECT key FROM document_fields WHERE key LIKE 'field-%'") == {"field-a1", "field-a2"}
        assert await ids(s, "SELECT display_name FROM entities WHERE display_name LIKE 'Person %'") == {"Person a1", "Person a2"}
        assert await ids(s, "SELECT name FROM storage_destinations") == {"dest-a"}
        assert (await s.execute(text("SELECT count(*) FROM entity_mentions"))).scalar_one() >= 2
    async with tenant_session(factory, client_a1) as s:
        assert await ids(s, "SELECT key FROM document_fields WHERE key LIKE 'field-%'") == {"field-a1"}  # own document only
        assert await ids(s, "SELECT id FROM entities") == set()  # entities span clients => never visible to them
        assert await ids(s, "SELECT id FROM entity_mentions") == set()
        assert await ids(s, "SELECT id FROM storage_destinations") == set()  # credentials never exposed to portal users
    async with tenant_session(factory, firm_ctx(world, world.firm_b, world.user_b)) as s:
        assert await ids(s, "SELECT name FROM storage_destinations") == {"dest-b"}
        assert (await s.execute(text("UPDATE storage_destinations SET name='x' WHERE name='dest-a'"))).rowcount == 0
        assert await ids(s, "SELECT display_name FROM entities WHERE display_name LIKE 'Person %'") == {"Person b1"}
    async with factory() as s:
        for table in ("document_fields", "entities", "entity_mentions", "storage_destinations"):
            assert (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0  # no context => nothing


def test_only_one_primary_destination_per_firm(database: PgConfig, world: World) -> None:
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        for n in ("p1", "p2"):
            c.execute("INSERT INTO storage_destinations (firm_id, name, region_country, credentials_enc, is_primary) VALUES (%s,%s,'KE',%s,%s)",
                      (world.firm_b, n, b"\x01x", n == "p1"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("UPDATE storage_destinations SET is_primary = true WHERE firm_id=%s AND name='p2'", (world.firm_b,))


# --- chats / messages / matter facts: approval gating is enforced by the database ------------------------------------
@pytest.fixture(scope="session")
def chat_rows(database: PgConfig, world: World):  # type: ignore[no-untyped-def]
    out: dict[str, uuid.UUID] = {}
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        def chat(label: str, firm: uuid.UUID, client: uuid.UUID | None, user: uuid.UUID) -> uuid.UUID:
            cid = c.execute(
                "INSERT INTO chats (firm_id, client_id, user_id, audience, title) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (firm, client, user, "client" if client else "advocate", label),
            ).fetchone()[0]
            out[label] = cid
            return cid

        a1 = chat("chat-a1", world.firm_a, world.a1, world.user_a1)
        a2 = chat("chat-a2", world.firm_a, world.a2, world.user_a)
        adv = chat("chat-adv", world.firm_a, None, world.user_a)
        b1 = chat("chat-b1", world.firm_b, world.b1, world.user_b)
        for cid, firm, client in ((a1, world.firm_a, world.a1), (a2, world.firm_a, world.a2), (adv, world.firm_a, None), (b1, world.firm_b, world.b1)):
            c.execute("INSERT INTO messages (firm_id, client_id, chat_id, role, content) VALUES (%s,%s,%s,'user',%s)", (firm, client, cid, f"q-{cid}"))
        for label, status in (("pending", "pending"), ("approved", "approved"), ("rejected", "rejected"), ("plain", "not_required")):
            c.execute(
                "INSERT INTO messages (firm_id, client_id, chat_id, role, content, approval_status) VALUES (%s,%s,%s,'assistant',%s,%s)",
                (world.firm_a, world.a1, a1, f"reply-{label}", status),
            )
    return out


async def test_clients_never_see_unreleased_replies_or_other_chats(factory, world: World, chat_rows) -> None:  # type: ignore[no-untyped-def]
    client_a1 = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    async with tenant_session(factory, client_a1) as s:
        assert await ids(s, "SELECT title FROM chats WHERE title LIKE 'chat-%'") == {"chat-a1"}  # not a2, not the advocate chat, not firm B
        seen = await ids(s, "SELECT content FROM messages WHERE chat_id = :c", c=chat_rows["chat-a1"])
        assert seen == {f"q-{chat_rows['chat-a1']}", "reply-approved", "reply-plain"}  # pending and rejected replies are invisible
        assert (await s.execute(text("UPDATE messages SET approval_status='approved' WHERE content='reply-pending'"))).rowcount == 0  # cannot self-approve
        assert (await s.execute(text("DELETE FROM messages WHERE content='reply-approved'"))).rowcount == 0  # replies are the audit record
        assert (await s.execute(text("UPDATE messages SET content='tampered' WHERE content='reply-approved'"))).rowcount == 0
        assert (await s.execute(text("DELETE FROM chats WHERE title='chat-a1'"))).rowcount == 0
    async with tenant_session(factory, firm_ctx(world, world.firm_a, world.user_a)) as s:
        every = await ids(s, "SELECT content FROM messages WHERE chat_id = :c", c=chat_rows["chat-a1"])
        assert {"reply-pending", "reply-rejected", "reply-approved", "reply-plain"} <= every  # the firm keeps the full record
        assert await ids(s, "SELECT title FROM chats WHERE title LIKE 'chat-%'") == {"chat-a1", "chat-a2", "chat-adv"}
    async with tenant_session(factory, firm_ctx(world, world.firm_b, world.user_b)) as s:
        assert await ids(s, "SELECT title FROM chats WHERE title LIKE 'chat-%'") == {"chat-b1"}


async def test_message_constraints_and_client_write_limits(factory, database: PgConfig, world: World, chat_rows) -> None:  # type: ignore[no-untyped-def]
    client_a1 = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    with pytest.raises(DBAPIError, match="row-level security"):  # a client cannot write into a sibling's chat
        async with tenant_session(factory, client_a1) as s:
            await s.execute(text("INSERT INTO messages (firm_id, client_id, chat_id, role, content) VALUES (:f,:c,:chat,'user','x')"),
                            {"f": world.firm_a, "c": world.a2, "chat": chat_rows["chat-a2"]})
    for role, approval in (("assistant", "approved"), ("user", "approved")):  # a client may not forge a released AI reply
        with pytest.raises(DBAPIError, match="row-level security"):
            async with tenant_session(factory, client_a1) as s:
                await s.execute(
                    text("INSERT INTO messages (firm_id, client_id, chat_id, role, content, approval_status) VALUES (:f,:c,:chat,:r,'forged',:a)"),
                    {"f": world.firm_a, "c": world.a1, "chat": chat_rows["chat-a1"], "r": role, "a": approval},
                )
    async with tenant_session(factory, client_a1) as s:  # but asking a plain question in their own chat works
        await s.execute(text("INSERT INTO messages (firm_id, client_id, chat_id, role, content) VALUES (:f,:c,:chat,'user','my question')"),
                        {"f": world.firm_a, "c": world.a1, "chat": chat_rows["chat-a1"]})
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO messages (firm_id, chat_id, role, content, approval_status) VALUES (%s,%s,'assistant','x','whatever')", (world.firm_a, chat_rows["chat-adv"]))
        with pytest.raises(psycopg.errors.CheckViolation):  # a 'client' chat must belong to a client
            c.execute("INSERT INTO chats (firm_id, user_id, audience) VALUES (%s,%s,'client')", (world.firm_a, world.user_a))


async def test_matter_facts_are_firm_only(factory, database: PgConfig, world: World) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        mid = c.execute("SELECT id FROM matters WHERE client_id=%s LIMIT 1", (world.a1,)).fetchone()[0]
        c.execute("INSERT INTO matter_facts (firm_id, client_id, matter_id, key, value) VALUES (%s,%s,%s,'tenancy-fact','v')", (world.firm_a, world.a1, mid))
    client_a1 = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    async with tenant_session(factory, client_a1) as s:
        assert await ids(s, "SELECT key FROM matter_facts") == set()
    async with tenant_session(factory, firm_ctx(world, world.firm_a, world.user_a)) as s:
        assert "tenancy-fact" in await ids(s, "SELECT key FROM matter_facts")
    async with tenant_session(factory, firm_ctx(world, world.firm_b, world.user_b)) as s:
        assert "tenancy-fact" not in await ids(s, "SELECT key FROM matter_facts")


# --- fact checks: privileged work product, firm-only ------------------------------------------------------------------------
async def test_fact_checks_are_firm_only_and_isolated(factory, database: PgConfig, world: World) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        for firm, user in ((world.firm_a, world.user_a), (world.firm_b, world.user_b)):
            fid = c.execute("INSERT INTO fact_checks (firm_id, created_by, input_text, as_at) VALUES (%s,%s,%s,'2026-01-01') RETURNING id", (firm, user, f"draft-{firm.hex[:6]}")).fetchone()[0]
            c.execute("INSERT INTO fact_check_items (firm_id, fact_check_id, ord, kind, content, verdict) VALUES (%s,%s,0,'claim',%s,'supported')", (firm, fid, f"item-{firm.hex[:6]}"))
    async with tenant_session(factory, firm_ctx(world, world.firm_a, world.user_a)) as s:
        assert await ids(s, "SELECT input_text FROM fact_checks WHERE input_text LIKE 'draft-%'") == {f"draft-{world.firm_a.hex[:6]}"}
        assert await ids(s, "SELECT content FROM fact_check_items WHERE content LIKE 'item-%'") == {f"item-{world.firm_a.hex[:6]}"}
    client_a1 = TenantContext(firm_id=world.firm_a, user_id=world.user_a1, role="client_user", client_id=world.a1)
    async with tenant_session(factory, client_a1) as s:  # a client must never see a firm's privileged analysis of their own file
        assert await ids(s, "SELECT id FROM fact_checks") == set() and await ids(s, "SELECT id FROM fact_check_items") == set()
    async with factory() as s:
        assert (await s.execute(text("SELECT count(*) FROM fact_checks"))).scalar_one() == 0
