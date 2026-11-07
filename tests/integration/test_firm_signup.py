"""Self-service firm sign-up: apply -> platform review -> firm created with the applicant as owner."""
import pytest

from app.core.config import settings
from tests.conftest import PgConfig
from tests.integration.helpers import make_user, owner_sql

pytestmark = pytest.mark.asyncio


def _body(slug: str, **kw):  # type: ignore[no-untyped-def]
    return {"firm_name": "Muthoni & Co. Advocates", "slug": slug, "practice_areas": ["insurance", "conveyancing"],
            "lsk_admission_no": "P.105/1234/15", "phone": "0712 345 678", "town": "Nairobi", "size": "2-5", "accept_terms": True} | kw


async def _admin(api, database: PgConfig):  # type: ignore[no-untyped-def]
    uid, h, _ = await make_user(api, "padmin")
    owner_sql(database, "UPDATE users SET platform_role='platform_admin' WHERE id=%s", uid)
    return uid, h


def _slug(tag: str) -> str:
    import uuid

    return f"{tag}-{uuid.uuid4().hex[:8]}"


async def test_apply_review_approve_makes_the_applicant_owner(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    assert (await api.get("/v1/firm-signup")).json()["mode"] == "review"
    _, h, email = await make_user(api, "applicant")
    slug = _slug("muthoni")
    assert (await api.get("/v1/firm-signup/slug-availability", params={"slug": slug}, headers=h)).json()["available"] is True
    r = await api.post("/v1/me/firm-applications", json=_body(slug), headers=h)
    assert r.status_code == 201 and r.json()["status"] == "pending" and r.json()["firm_id"] is None
    app_id = r.json()["id"]
    assert (await api.get("/v1/firm-signup/slug-availability", params={"slug": slug}, headers=h)).json()["available"] is False  # reserved while pending
    assert (await api.get("/v1/me/memberships", headers=h)).json()["firms"] == []  # nothing granted before review

    _, ah = await _admin(api, database)
    queue = (await api.get("/v1/platform/firm-applications", headers=ah)).json()
    item = next(q for q in queue if q["id"] == app_id)
    assert item["applicant_email"] == email and item["phone"] == "+254712345678" and item["terms_version"] == settings.terms_version
    ok = await api.post(f"/v1/platform/firm-applications/{app_id}:approve", json={"note": "LSK roll checked"}, headers=ah)
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    firm_id = ok.json()["firm_id"]
    assert (await api.get("/v1/me/memberships", headers=h)).json()["firms"] == [{"firm_id": firm_id, "role": "firm_owner"}]
    firm = (await api.get(f"/v1/firms/{firm_id}", headers=h)).json()
    assert firm["slug"] == slug and firm["name"] == "Muthoni & Co. Advocates"
    assert any(m.to == email and "ready" in m.subject for m in outbox.sent)
    assert owner_sql(database, "SELECT count(*) FROM audit_events WHERE action='firm_application.approve' AND target=%s", f"application:{app_id}")[0][0] == 1
    again = await api.post(f"/v1/platform/firm-applications/{app_id}:approve", json={}, headers=ah)
    assert again.status_code == 409  # decisions are final


async def test_rejection_reason_reaches_the_applicant_who_can_reapply(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    _, h, email = await make_user(api, "rejected")
    app_id = (await api.post("/v1/me/firm-applications", json=_body(_slug("nofirm")), headers=h)).json()["id"]
    _, ah = await _admin(api, database)
    assert (await api.post(f"/v1/platform/firm-applications/{app_id}:reject", json={"reason": "x"}, headers=ah)).status_code == 422
    r = await api.post(f"/v1/platform/firm-applications/{app_id}:reject", json={"reason": "We could not find you on the LSK roll."}, headers=ah)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    mine = (await api.get("/v1/me/firm-applications", headers=h)).json()
    assert mine[0]["review_note"] == "We could not find you on the LSK roll."
    msg = [m for m in outbox.sent if m.to == email][-1]
    assert "LSK" not in msg.body  # the reason is shown in the app, never emailed
    assert (await api.post("/v1/me/firm-applications", json=_body(_slug("retry")), headers=h)).status_code == 201


async def test_application_rules(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    _, h, _ = await make_user(api, "rules")
    assert (await api.post("/v1/me/firm-applications", json=_body(_slug("a"), accept_terms=False), headers=h)).json()["code"] == "terms_required"
    for bad in ("admin", "lygo-official", "Bad_Slug", "a--b", "ab"):
        assert (await api.post("/v1/me/firm-applications", json=_body(bad), headers=h)).status_code == 422, bad
    assert (await api.post("/v1/me/firm-applications", json=_body(_slug("p"), phone="12345"), headers=h)).status_code == 422
    assert (await api.post("/v1/me/firm-applications", json=_body(_slug("first")), headers=h)).status_code == 201
    second = await api.post("/v1/me/firm-applications", json=_body(_slug("second")), headers=h)
    assert second.status_code == 409 and second.json()["code"] == "application_pending"  # one open application per person
    # a name that's taken by an existing firm or another pending application
    _, h2, _ = await make_user(api, "rules2")
    taken = owner_sql(database, "SELECT slug FROM firm_applications WHERE status='pending' ORDER BY created_at DESC LIMIT 1")[0][0]
    assert (await api.post("/v1/me/firm-applications", json=_body(taken), headers=h2)).json()["code"] == "slug_taken"
    assert (await api.post("/v1/me/firm-applications", json=_body(_slug("x")))).status_code == 401  # must be signed in


async def test_withdraw_and_privacy(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    _, h, _ = await make_user(api, "withdrawer")
    _, other, _ = await make_user(api, "nosy")
    app_id = (await api.post("/v1/me/firm-applications", json=_body(_slug("w")), headers=h)).json()["id"]
    assert (await api.post(f"/v1/me/firm-applications/{app_id}:withdraw", headers=other)).status_code == 404  # not yours = not found
    assert (await api.get("/v1/me/firm-applications", headers=other)).json() == []
    assert (await api.get("/v1/platform/firm-applications", headers=h)).status_code == 403  # applicants can't see the queue
    assert (await api.post(f"/v1/me/firm-applications/{app_id}:withdraw", headers=h)).json()["status"] == "withdrawn"
    _, ah = await _admin(api, database)
    assert (await api.post(f"/v1/platform/firm-applications/{app_id}:approve", json={}, headers=ah)).status_code == 409


async def test_approve_with_a_different_slug_when_the_requested_one_was_taken(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    _, h, _ = await make_user(api, "clash")
    slug = _slug("clash")
    app_id = (await api.post("/v1/me/firm-applications", json=_body(slug), headers=h)).json()["id"]
    owner_sql(database, "INSERT INTO firms (slug, name) VALUES (%s, 'Squatter')", slug)  # created by other means meanwhile
    _, ah = await _admin(api, database)
    assert (await api.post(f"/v1/platform/firm-applications/{app_id}:approve", json={}, headers=ah)).json()["code"] == "slug_taken"
    r = await api.post(f"/v1/platform/firm-applications/{app_id}:approve", json={"slug": slug + "-ke"}, headers=ah)
    assert r.status_code == 200 and r.json()["slug"] == slug + "-ke"


async def test_platform_support_can_view_but_not_decide(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    _, h, _ = await make_user(api, "sup-applicant")
    app_id = (await api.post("/v1/me/firm-applications", json=_body(_slug("s")), headers=h)).json()["id"]
    sid, sh, _ = await make_user(api, "support")
    owner_sql(database, "UPDATE users SET platform_role='platform_support' WHERE id=%s", sid)
    assert (await api.get("/v1/platform/firm-applications", headers=sh)).status_code == 200
    assert (await api.post(f"/v1/platform/firm-applications/{app_id}:approve", json={}, headers=sh)).status_code == 403


async def test_auto_mode_creates_the_firm_immediately(api, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "firm_signup_mode", "auto")
    _, h, _ = await make_user(api, "auto")
    r = await api.post("/v1/me/firm-applications", json=_body(_slug("auto")), headers=h)
    assert r.status_code == 201 and r.json()["status"] == "approved" and r.json()["firm_id"]
    assert (await api.get("/v1/me/memberships", headers=h)).json()["firms"][0]["role"] == "firm_owner"


async def test_closed_mode_refuses(api, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "firm_signup_mode", "closed")
    _, h, _ = await make_user(api, "closed")
    assert (await api.post("/v1/me/firm-applications", json=_body(_slug("c")), headers=h)).status_code == 403


async def test_applications_are_rate_limited(api) -> None:  # type: ignore[no-untyped-def]
    _, h, _ = await make_user(api, "spammy")
    codes = []
    for i in range(4):
        r = await api.post("/v1/me/firm-applications", json=_body(_slug(f"s{i}")), headers=h)
        codes.append(r.status_code)
        if r.status_code == 201:
            await api.post(f"/v1/me/firm-applications/{r.json()['id']}:withdraw", headers=h)
    assert codes == [201, 201, 201, 429]
