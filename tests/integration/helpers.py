import uuid

import psycopg
from httpx import AsyncClient

from tests.conftest import TEST_DB, PgConfig

PASSWORD = "correct horse battery 42"


def owner_sql(pg: PgConfig, sql: str, *params):  # type: ignore[no-untyped-def]
    with psycopg.connect(pg.owner_dsn(TEST_DB), autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else None


async def make_user(api: AsyncClient, tag: str) -> tuple[uuid.UUID, dict[str, str], str]:
    email = f"{tag}-{uuid.uuid4().hex[:8]}@example.com"
    r = await api.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    t = await api.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert t.status_code == 200, t.text
    return uuid.UUID(r.json()["id"]), {"Authorization": f"Bearer {t.json()['access_token']}"}, email


def new_firm(pg: PgConfig) -> uuid.UUID:
    fid = uuid.uuid4()
    owner_sql(pg, "INSERT INTO firms (id, slug, name) VALUES (%s, %s, %s)", fid, f"f-{fid.hex[:10]}", "Test Firm")
    return fid


async def firm_member(api: AsyncClient, pg: PgConfig, firm: uuid.UUID, role: str):  # type: ignore[no-untyped-def]
    uid, headers, email = await make_user(api, role)
    owner_sql(pg, "INSERT INTO firm_memberships (firm_id, user_id, role) VALUES (%s, %s, %s)", firm, uid, role)
    return uid, headers, email


def token_from(outbox, to: str) -> str:  # type: ignore[no-untyped-def]
    msg = [m for m in outbox.sent if m.to == to][-1]
    return msg.body.rsplit(": ", 1)[1].strip()
