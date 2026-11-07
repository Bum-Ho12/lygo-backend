"""End-to-end API tests: auth, RBAC, tenant resolution, RLS and audit through HTTP."""
import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token
from tests.conftest import PgConfig, World
from tests.integration.helpers import PASSWORD, firm_member, owner_sql
from tests.integration.helpers import make_user as _make_user

pytestmark = pytest.mark.asyncio


async def make_user(api: AsyncClient, tag: str):  # type: ignore[no-untyped-def]
    uid, headers, _ = await _make_user(api, tag)
    return uid, headers


async def member(api, pg: PgConfig, world: World, firm: uuid.UUID, role: str):  # type: ignore[no-untyped-def]
    uid, headers, _ = await firm_member(api, pg, firm, role)
    return uid, headers


# --- authentication ----------------------------------------------------------------------------
async def test_register_login_me(api) -> None:  # type: ignore[no-untyped-def]
    uid, h = await make_user(api, "me")
    r = await api.get("/v1/me", headers=h)
    assert r.status_code == 200 and r.json()["id"] == str(uid)
    assert (await api.get("/v1/me/memberships", headers=h)).json() == {"firms": [], "clients": []}


async def test_register_validation_and_duplicates(api) -> None:  # type: ignore[no-untyped-def]
    email = f"dup-{uuid.uuid4().hex[:6]}@example.com"
    assert (await api.post("/v1/auth/register", json={"email": email, "password": "short"})).status_code == 422
    assert (await api.post("/v1/auth/register", json={"email": email, "password": "aaaaaaaaaaaaaaaa"})).status_code == 422
    assert (await api.post("/v1/auth/register", json={"email": "not-an-email", "password": PASSWORD})).status_code == 422
    assert (await api.post("/v1/auth/register", json={"email": email, "password": PASSWORD})).status_code == 201
    dup = await api.post("/v1/auth/register", json={"email": email.upper(), "password": PASSWORD})
    assert dup.status_code == 409 and dup.headers["content-type"].startswith("application/problem+json")


async def test_login_failures_are_indistinguishable(api) -> None:  # type: ignore[no-untyped-def]
    _, _ = await make_user(api, "x")
    email = f"known-{uuid.uuid4().hex[:6]}@example.com"
    await api.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    wrong = await api.post("/v1/auth/login", json={"email": email, "password": "wrong password here"})
    unknown = await api.post("/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong password here"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


async def test_refresh_rotation_and_reuse_detection(api) -> None:  # type: ignore[no-untyped-def]
    email = f"rot-{uuid.uuid4().hex[:6]}@example.com"
    await api.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    first = (await api.post("/v1/auth/login", json={"email": email, "password": PASSWORD})).json()
    second = await api.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200 and second.json()["refresh_token"] != first["refresh_token"]
    # replaying the rotated token is treated as theft: it fails AND kills the whole family
    assert (await api.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})).status_code == 401
    assert (await api.post("/v1/auth/refresh", json={"refresh_token": second.json()["refresh_token"]})).status_code == 401
    assert (await api.post("/v1/auth/refresh", json={"refresh_token": "garbage"})).status_code == 401


async def test_logout_revokes_refresh_token(api) -> None:  # type: ignore[no-untyped-def]
    email = f"out-{uuid.uuid4().hex[:6]}@example.com"
    await api.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    t = (await api.post("/v1/auth/login", json={"email": email, "password": PASSWORD})).json()
    assert (await api.post("/v1/auth/logout", json={"refresh_token": t["refresh_token"]})).status_code == 204
    assert (await api.post("/v1/auth/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401


async def test_bad_access_tokens_rejected(api) -> None:  # type: ignore[no-untyped-def]
    uid, h = await make_user(api, "tok")
    expired = create_access_token(uid, ttl=timedelta(seconds=-5))
    assert (await api.get("/v1/me")).status_code == 401
    assert (await api.get("/v1/me", headers={"Authorization": f"Bearer {expired}"})).status_code == 401
    assert (await api.get("/v1/me", headers={"Authorization": h["Authorization"] + "x"})).status_code == 401
    assert (await api.get("/v1/me", headers={"Authorization": "Bearer not.a.jwt"})).status_code == 401


# --- tenant resolution, RBAC, RLS --------------------------------------------------------------
async def test_firm_access_requires_membership(api, database: PgConfig, world: World) -> None:  # type: ignore[no-untyped-def]
    _, ha = await member(api, database, world, world.firm_a, "advocate")
    assert (await api.get(f"/v1/firms/{world.firm_a}")).status_code == 401
    r = await api.get(f"/v1/firms/{world.firm_a}", headers=ha)
    assert r.status_code == 200 and r.json()["slug"] == "firm-a"
    assert (await api.get(f"/v1/firms/{world.firm_b}", headers=ha)).status_code == 404  # not a member
    assert (await api.get(f"/v1/firms/{uuid.uuid4()}", headers=ha)).status_code == 404  # same answer: no probing
    mem = (await api.get("/v1/me/memberships", headers=ha)).json()
    assert mem["firms"] == [{"firm_id": str(world.firm_a), "role": "advocate"}]


async def test_clients_list_is_isolated_and_paginated(api, database: PgConfig, world: World) -> None:  # type: ignore[no-untyped-def]
    _, ha = await member(api, database, world, world.firm_a, "advocate")
    page1 = (await api.get(f"/v1/firms/{world.firm_a}/clients?limit=1", headers=ha)).json()
    assert len(page1["items"]) == 1 and page1["next_cursor"]
    page2 = (await api.get(f"/v1/firms/{world.firm_a}/clients?limit=1&cursor={page1['next_cursor']}", headers=ha)).json()
    assert page2["next_cursor"] is None
    names = {page1["items"][0]["name"], page2["items"][0]["name"]}
    assert names == {"A1", "A2"}  # never B1/B2
    assert (await api.get(f"/v1/firms/{world.firm_a}/clients?cursor=%%%", headers=ha)).status_code == 400


async def test_client_portal_user_sees_only_own_client_and_cannot_manage(api, database: PgConfig, world: World) -> None:  # type: ignore[no-untyped-def]
    uid, h = await make_user(api, "portal")
    owner_sql(database, "INSERT INTO client_memberships (client_id, firm_id, user_id, role) VALUES (%s, %s, %s, 'client_user')", world.a1, world.firm_a, uid)
    items = (await api.get(f"/v1/firms/{world.firm_a}/clients", headers=h)).json()["items"]
    assert [c["name"] for c in items] == ["A1"]
    assert (await api.patch(f"/v1/firms/{world.firm_a}", json={"name": "pwned"}, headers=h)).status_code == 403
    assert (await api.get(f"/v1/firms/{world.firm_b}/clients", headers=h)).status_code == 404


async def test_permissions_and_audit_on_firm_update(api, database: PgConfig, world: World) -> None:  # type: ignore[no-untyped-def]
    _, hp = await member(api, database, world, world.firm_a, "paralegal")
    admin_id, hadm = await member(api, database, world, world.firm_a, "firm_admin")
    assert (await api.patch(f"/v1/firms/{world.firm_a}", json={"name": "x"}, headers=hp)).status_code == 403
    ok = await api.patch(f"/v1/firms/{world.firm_a}", json={"branding": {"color": "#123456"}}, headers=hadm)
    assert ok.status_code == 200 and ok.json()["branding"] == {"color": "#123456"}
    rows = owner_sql(database, "SELECT actor_id, meta FROM audit_events WHERE firm_id = %s AND action = 'firm.update'", world.firm_a)
    assert any(r[0] == admin_id and r[1] == {"fields": ["branding"]} for r in rows)
    assert (await api.patch(f"/v1/firms/{world.firm_a}", json={"name": ""}, headers=hadm)).status_code == 422


async def test_platform_users_get_no_implicit_tenant_access(api, database: PgConfig, world: World) -> None:  # type: ignore[no-untyped-def]
    uid, h = await make_user(api, "plat")
    owner_sql(database, "UPDATE users SET platform_role = 'platform_admin' WHERE id = %s", uid)
    assert (await api.get(f"/v1/firms/{world.firm_a}", headers=h)).status_code == 404
