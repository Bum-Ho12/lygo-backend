"""Authentication hardening: lockouts, SMS OTP, TOTP, recovery codes, MFA enforcement, password reset."""
import time
import uuid

import pyotp
import pytest

from app.core.config import settings
from app.core.security import create_mfa_token, decode_access_claims
from app.integrations.sms.base import SmsTransientError
from tests.conftest import PgConfig
from tests.integration.helpers import (
    PASSWORD,
    firm_member,
    make_user,
    new_firm,
    owner_sql,
    token_from,
)

pytestmark = pytest.mark.asyncio
PHONE = "0712345678"
E164 = "+254712345678"


def bearer(tokens: dict) -> dict[str, str]:  # type: ignore[type-arg]
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def login(api, email, password=PASSWORD, ip=None):  # type: ignore[no-untyped-def]
    return await api.post("/v1/auth/login", json={"email": email, "password": password}, headers={"X-Forwarded-For": ip} if ip else {})


async def verified_phone(api, sms_box, headers):  # type: ignore[no-untyped-def]
    assert (await api.post("/v1/me/phone", json={"phone": PHONE}, headers=headers)).status_code == 202
    assert (await api.post("/v1/me/phone/verify", json={"code": sms_box["p"].last_code()}, headers=headers)).status_code == 200


def totp_codes(secret: str):  # type: ignore[no-untyped-def]
    """Codes for three consecutive steps; the server accepts t-1..t+1 and never the same step twice."""
    t, now = pyotp.TOTP(secret), time.time()
    return [t.at(now - 30), t.at(now), t.at(now + 30)]


async def enrol_totp(api, headers):  # type: ignore[no-untyped-def]
    secret = (await api.post("/v1/me/mfa/totp/enroll", headers=headers)).json()["secret"]
    codes = totp_codes(secret)
    r = await api.post("/v1/me/mfa/totp/confirm", json={"code": codes[0]}, headers=headers)
    assert r.status_code == 200, r.text
    return secret, codes, r.json()


# =========================================================================================================== rate limiting
async def test_lockout_blocks_even_the_correct_password_and_expires(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    _, _, email = await make_user(api, "victim")
    for _ in range(settings.auth_max_failures_pair):
        assert (await login(api, email, "wrong password!!")).status_code == 401
    locked = await login(api, email)  # CORRECT password, but the pair is locked
    assert locked.status_code == 429 and locked.json()["code"] == "too_many_attempts" and int(locked.headers["retry-after"]) > 0
    assert (await login(api, email, "wrong password!!")).status_code == 429  # no guessing oracle while locked
    owner_sql(database, "UPDATE auth_throttle SET locked_until = now() - interval '1 second', window_start = now() - interval '1 hour'")
    assert (await login(api, email)).status_code == 200
    assert owner_sql(database, "SELECT count(*) FROM auth_throttle WHERE key LIKE 'login-pair:%%'")[0][0] == 0  # success clears the pair counter


async def test_an_attacker_cannot_lock_a_victim_out_from_everywhere(api, database: PgConfig, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
    _, _, email = await make_user(api, "victim")
    for _ in range(settings.auth_max_failures_pair):
        await login(api, email, "wrong password!!", ip="203.0.113.7")  # the attacker's address
    assert (await login(api, email, ip="203.0.113.7")).status_code == 429
    assert (await login(api, email, ip="198.51.100.20")).status_code == 200  # the real user, elsewhere, is unaffected


async def test_distributed_guessing_hits_the_account_wide_limit(api, database: PgConfig, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
    monkeypatch.setattr(settings, "auth_max_failures_account", 4)
    _, _, email = await make_user(api, "victim")
    for i in range(4):
        await login(api, email, "wrong password!!", ip=f"198.51.100.{i + 1}")  # a botnet: one guess per address
    assert (await login(api, email, ip="198.51.100.99")).status_code == 429  # now locked for everyone, correct password included


async def test_one_address_spraying_many_accounts_gets_locked(api, database: PgConfig, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
    monkeypatch.setattr(settings, "auth_max_failures_ip", 3)
    for i in range(3):
        await login(api, f"nobody{i}@example.com", "wrong password!!", ip="203.0.113.50")  # unknown accounts count too
    _, _, email = await make_user(api, "legit")
    assert (await login(api, email, ip="203.0.113.50")).status_code == 429
    assert (await login(api, email, ip="203.0.113.51")).status_code == 200


def test_client_ip_trusts_only_the_configured_proxy_hops(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import Request

    from app.api.deps import client_ip

    def req(xff: str | None) -> Request:
        headers = [(b"x-forwarded-for", xff.encode())] if xff else []
        return Request({"type": "http", "headers": headers, "client": ("10.0.0.9", 1234), "method": "GET", "path": "/", "query_string": b""})

    monkeypatch.setattr(settings, "trusted_proxy_hops", 0)
    assert client_ip(req("6.6.6.6")) == "10.0.0.9"  # header ignored unless we run behind proxies
    monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
    assert client_ip(req("6.6.6.6, 203.0.113.9")) == "203.0.113.9"  # the proxy-appended entry, not the spoofed leftmost one
    assert client_ip(req(None)) == "10.0.0.9"
    monkeypatch.setattr(settings, "trusted_proxy_hops", 2)
    assert client_ip(req("1.1.1.1, 2.2.2.2, 3.3.3.3")) == "2.2.2.2"


# ====================================================================================================== phone verification & OTP
async def test_phone_verification_flow(api, database: PgConfig, sms_box, outbox) -> None:  # type: ignore[no-untyped-def]
    uid, h, email = await make_user(api, "phone")
    assert (await api.post("/v1/me/phone", json={"phone": "12345"}, headers=h)).status_code == 422
    r = await api.post("/v1/me/phone", json={"phone": PHONE}, headers=h)
    assert r.status_code == 202 and r.json() == {"sent_to": "+25471***78"}
    to, body = sms_box["p"].sent[-1]
    assert to == E164 and body.startswith("Your Lygo code is ") and "expires in 5 minutes" in body
    code = sms_box["p"].last_code()
    stored = owner_sql(database, "SELECT code_hash, destination FROM otp_challenges WHERE user_id=%s", uid)[0]
    assert code not in stored[0] and len(stored[0]) == 64 and stored[1] == E164  # keyed hash only, never the code
    assert (await api.post("/v1/me/phone/verify", json={"code": "000000" if code != "000000" else "111111"}, headers=h)).status_code == 422
    assert (await api.post("/v1/me/phone/verify", json={"code": code}, headers=h)).json() == {"phone": "+25471***78", "status": "verified"}
    assert (await api.post("/v1/me/phone/verify", json={"code": code}, headers=h)).status_code == 422  # single use
    s = (await api.get("/v1/me/security", headers=h)).json()
    assert s["phone_verified"] is True and s["phone"] == "+25471***78" and s["totp_enabled"] is False
    assert any("A new phone number was verified" in m.body for m in outbox.sent if m.to == email)  # the owner is told by email


async def test_otp_attempt_limit_expiry_and_supersession(api, database: PgConfig, sms_box) -> None:  # type: ignore[no-untyped-def]
    uid, h, _ = await make_user(api, "otp")
    await api.post("/v1/me/phone", json={"phone": PHONE}, headers=h)
    first = sms_box["p"].last_code()
    await api.post("/v1/me/phone", json={"phone": PHONE}, headers=h)
    second = sms_box["p"].last_code()
    if first != second:
        assert (await api.post("/v1/me/phone/verify", json={"code": first}, headers=h)).status_code == 422  # a new code supersedes the old
    wrong = "000000" if second != "000000" else "111111"
    for _ in range(settings.otp_max_attempts):
        assert (await api.post("/v1/me/phone/verify", json={"code": wrong}, headers=h)).status_code == 422
    assert (await api.post("/v1/me/phone/verify", json={"code": second}, headers=h)).status_code == 422  # burned by too many wrong guesses
    await api.post("/v1/me/phone", json={"phone": PHONE}, headers=h)
    fresh = sms_box["p"].last_code()
    owner_sql(database, "UPDATE otp_challenges SET expires_at = now() - interval '1 second' WHERE user_id=%s", uid)
    assert (await api.post("/v1/me/phone/verify", json={"code": fresh}, headers=h)).status_code == 422  # expired


async def test_sms_sends_are_rate_limited_per_user_and_ip(api, database: PgConfig, sms_box, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _, h, _ = await make_user(api, "pump")
    codes = [await api.post("/v1/me/phone", json={"phone": PHONE}, headers=h) for _ in range(settings.otp_sends_per_window)]
    assert all(c.status_code == 202 for c in codes)
    blocked = await api.post("/v1/me/phone", json={"phone": PHONE}, headers=h)
    assert blocked.status_code == 429 and len(sms_box["p"].sent) == settings.otp_sends_per_window  # no SMS is paid for once blocked
    monkeypatch.setattr(settings, "otp_sends_per_ip_hour", 2)
    owner_sql(database, "DELETE FROM auth_throttle")
    _, h2, _ = await make_user(api, "pump2")
    _, h3, _ = await make_user(api, "pump3")
    await api.post("/v1/me/phone", json={"phone": PHONE}, headers=h2)
    await api.post("/v1/me/phone", json={"phone": PHONE}, headers=h3)
    assert (await api.post("/v1/me/phone", json={"phone": "0722000111"}, headers=h2)).status_code == 429  # same IP, different users


async def test_provider_failures_surface_as_503_without_leaking(api, database: PgConfig, sms_box) -> None:  # type: ignore[no-untyped-def]
    _, h, _ = await make_user(api, "down")
    sms_box["p"].fail = SmsTransientError("provider key=SECRET busy")
    r = await api.post("/v1/me/phone", json={"phone": PHONE}, headers=h)
    assert r.status_code == 503 and r.json()["code"] == "sms_unavailable" and "SECRET" not in r.text
    sms_box["p"] = None  # no provider configured at all
    assert (await api.post("/v1/me/phone", json={"phone": PHONE}, headers=h)).status_code == 503


async def test_sms_factor_needs_a_verified_phone_and_the_password(api, database: PgConfig, sms_box) -> None:  # type: ignore[no-untyped-def]
    _, h, _ = await make_user(api, "toggle")
    assert (await api.post("/v1/me/mfa/sms", json={"enabled": True, "password": PASSWORD}, headers=h)).status_code == 409
    await verified_phone(api, sms_box, h)
    assert (await api.post("/v1/me/mfa/sms", json={"enabled": True, "password": "wrong password!!"}, headers=h)).status_code == 403
    assert (await api.post("/v1/me/mfa/sms", json={"enabled": True, "password": PASSWORD}, headers=h)).json() == {"sms_enabled": True}


# ============================================================================================================ SMS as a login factor
async def test_sms_second_factor_login(api, database: PgConfig, sms_box) -> None:  # type: ignore[no-untyped-def]
    _uid, h, email = await make_user(api, "smsmfa")
    await verified_phone(api, sms_box, h)
    await api.post("/v1/me/mfa/sms", json={"enabled": True, "password": PASSWORD}, headers=h)
    sms_box["p"].sent.clear()

    step1 = (await login(api, email)).json()
    assert step1["mfa_required"] is True and step1["methods"] == ["sms"] and step1["access_token"] is None and step1["refresh_token"] is None
    assert (await api.get("/v1/me", headers={"Authorization": f"Bearer {step1['mfa_token']}"})).status_code == 401  # the mfa token is not a session
    sent = await api.post("/v1/auth/mfa/sms/send", json={"mfa_token": step1["mfa_token"]})
    assert sent.status_code == 202 and sent.json() == {"sent_to": "+25471***78"}
    assert [to for to, _ in sms_box["p"].sent] == [E164]  # always the verified number, never one supplied by the caller
    bad = await api.post("/v1/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "method": "sms", "code": "000000"})
    assert bad.status_code == 401
    ok = await api.post("/v1/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "method": "sms", "code": sms_box["p"].last_code()})
    assert ok.status_code == 200 and decode_access_claims(ok.json()["access_token"]).mfa is True
    assert (await api.get("/v1/me", headers=bearer(ok.json()))).status_code == 200
    assert (await api.post("/v1/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "method": "totp", "code": "123456"})).status_code == 401  # method not enabled


async def test_mfa_tokens_are_validated(api, database: PgConfig, sms_box) -> None:  # type: ignore[no-untyped-def]
    _uid, h, _email = await make_user(api, "tok")
    forged = create_mfa_token(uuid.uuid4())
    assert (await api.post("/v1/auth/mfa/sms/send", json={"mfa_token": "garbage"})).status_code == 401
    assert (await api.post("/v1/auth/mfa/sms/send", json={"mfa_token": forged})).status_code == 409  # unknown user / no SMS factor
    assert (await api.post("/v1/auth/mfa/verify", json={"mfa_token": "garbage", "method": "totp", "code": "123456"})).status_code == 401
    assert (await api.post("/v1/auth/mfa/sms/send", json={"mfa_token": h["Authorization"].split()[1]})).status_code == 401  # an access token is not an mfa token


async def test_second_factor_guessing_is_throttled(api, database: PgConfig, sms_box, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _, h, email = await make_user(api, "guess")
    secret, _, _ = await enrol_totp(api, h)
    step1 = (await login(api, email)).json()
    for _ in range(settings.auth_max_failures_pair):
        assert (await api.post("/v1/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "method": "totp", "code": "000000"})).status_code == 401
    locked = await api.post("/v1/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "method": "totp", "code": totp_codes(secret)[1]})
    assert locked.status_code == 429  # even the right code is refused while locked


# ================================================================================================================== TOTP & recovery
async def test_totp_enrolment_login_replay_and_recovery(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    uid, h, email = await make_user(api, "totp")
    enrol = (await api.post("/v1/me/mfa/totp/enroll", headers=h)).json()
    assert enrol["otpauth_uri"].startswith("otpauth://totp/") and enrol["secret"] in enrol["otpauth_uri"]
    blob = bytes(owner_sql(database, "SELECT mfa_secret_enc FROM users WHERE id=%s", uid)[0][0])
    assert enrol["secret"].encode() not in blob and blob[:1] == b"\x01"  # secret is sealed at rest
    secret = enrol["secret"]
    c = totp_codes(secret)
    assert (await api.post("/v1/me/mfa/totp/confirm", json={"code": "000000"}, headers=h)).status_code == 422
    done = await api.post("/v1/me/mfa/totp/confirm", json={"code": c[0]}, headers=h)
    codes = done.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10 and decode_access_claims(done.json()["access_token"]).mfa is True
    assert (await api.post("/v1/me/mfa/totp/enroll", headers=h)).status_code == 409  # already on
    assert (await api.get("/v1/me/security", headers=bearer(done.json()))).json()["recovery_codes_remaining"] == 10
    stored = str(owner_sql(database, "SELECT code_hash FROM mfa_recovery_codes WHERE user_id=%s", uid))
    assert not any(code.replace("-", "") in stored for code in codes)

    step1 = (await login(api, email)).json()
    assert step1["methods"] == ["totp", "recovery"] and step1["access_token"] is None
    verify = lambda code, m="totp": api.post("/v1/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "method": m, "code": code})
    ok = await verify(c[1])
    assert ok.status_code == 200 and decode_access_claims(ok.json()["access_token"]).mfa is True
    assert (await verify(c[1])).status_code == 401  # replaying a used code (same time step) is refused
    assert (await verify(c[0])).status_code == 401  # and so is an earlier step than the last one accepted
    assert (await verify(c[2])).status_code == 200  # a later step is fine
    rec = await verify(codes[0].upper(), "recovery")  # case/format tolerant
    assert rec.status_code == 200
    assert (await verify(codes[0], "recovery")).status_code == 401  # recovery codes are single use
    assert (await verify(codes[1].replace("-", " "), "recovery")).status_code == 200
    assert (await api.get("/v1/me/security", headers=bearer(rec.json()))).json()["recovery_codes_remaining"] == 8


async def test_disabling_and_regenerating_need_reauthentication(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    uid, h, email = await make_user(api, "disable")
    _secret, c, done = await enrol_totp(api, h)
    hh = bearer(done)
    assert (await api.post("/v1/me/mfa/totp/disable", json={"password": "wrong password!!", "code": c[1]}, headers=hh)).status_code == 403
    assert (await api.post("/v1/me/mfa/totp/disable", json={"password": PASSWORD}, headers=hh)).status_code == 403  # password alone is not enough
    new_codes = await api.post("/v1/me/mfa/recovery-codes", json={"password": PASSWORD, "code": c[1]}, headers=hh)
    assert new_codes.status_code == 200 and set(new_codes.json()["recovery_codes"]).isdisjoint(done["recovery_codes"])
    old = (await login(api, email)).json()
    assert (await api.post("/v1/auth/mfa/verify", json={"mfa_token": old["mfa_token"], "method": "recovery", "code": done["recovery_codes"][0]})).status_code == 401  # regeneration killed the old set
    assert (await api.post("/v1/me/mfa/totp/disable", json={"password": PASSWORD, "code": c[2]}, headers=hh)).status_code == 204
    assert (await api.post("/v1/auth/refresh", json={"refresh_token": done["refresh_token"]})).status_code == 401  # sessions ended
    plain = await login(api, email)
    assert plain.json()["mfa_required"] is False and plain.json()["access_token"]  # back to password-only
    assert owner_sql(database, "SELECT mfa_secret_enc, mfa_totp_enabled_at FROM users WHERE id=%s", uid)[0] == (None, None)


async def test_refreshed_sessions_keep_their_mfa_status(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    _, h, email = await make_user(api, "refresh")
    _, c, _done = await enrol_totp(api, h)
    step1 = (await login(api, email)).json()
    tokens = (await api.post("/v1/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "method": "totp", "code": c[1]})).json()
    again = (await api.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})).json()
    assert decode_access_claims(again["access_token"]).mfa is True
    _plain_uid, _ph, pemail = await make_user(api, "nomfa")
    p = (await login(api, pemail)).json()
    assert decode_access_claims((await api.post("/v1/auth/refresh", json={"refresh_token": p["refresh_token"]})).json()["access_token"]).mfa is False


# ============================================================================================================ MFA enforcement
async def test_firm_roles_must_use_a_second_factor_when_enforced(api, database: PgConfig, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _uid, h, email = await firm_member(api, database, firm, "advocate")
    _, _ch, _cemail = await make_user(api, "clientuser")
    assert (await api.get(f"/v1/firms/{firm}", headers=h)).status_code == 200  # enforcement off (local default)
    monkeypatch.setattr(settings, "mfa_required_for_firm_roles", True)
    r = await api.get(f"/v1/firms/{firm}", headers=h)
    assert r.status_code == 403 and r.json()["code"] == "mfa_required"
    assert (await api.get("/v1/me", headers=h)).status_code == 200  # account endpoints stay reachable so they can enrol
    assert (await api.get(f"/v1/firms/{firm}/clients", headers=h)).status_code == 403
    _secret, c, done = await enrol_totp(api, h)
    assert (await api.get(f"/v1/firms/{firm}", headers=bearer(done))).status_code == 200  # the confirm step upgrades the session
    assert (await api.get(f"/v1/firms/{firm}", headers=h)).status_code == 403  # the old token is still just single-factor
    # a fresh login must pass the second factor to reach firm data
    step1 = (await login(api, email)).json()
    tokens = (await api.post("/v1/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "method": "totp", "code": c[1]})).json()
    assert (await api.get(f"/v1/firms/{firm}", headers=bearer(tokens))).status_code == 200


async def test_enforcement_does_not_apply_to_client_users_but_does_to_platform(api, database: PgConfig, outbox, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, adv, _ = await firm_member(api, database, firm, "advocate")
    cid = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "C"}, headers=adv)).json()["id"]
    _, portal, pemail = await make_user(api, "portal")
    await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": pemail}, headers=adv)
    await api.post("/v1/invitations/accept", json={"token": token_from(outbox, pemail)}, headers=portal)
    pid, ph, _ = await make_user(api, "platform")
    owner_sql(database, "UPDATE users SET platform_role='platform_admin' WHERE id=%s", pid)
    assert (await api.get("/v1/platform/firms", headers=ph)).status_code == 200
    monkeypatch.setattr(settings, "mfa_required_for_firm_roles", True)
    assert (await api.get(f"/v1/firms/{firm}", headers=portal)).status_code == 200  # clients: optional
    r = await api.get("/v1/platform/firms", headers=ph)
    assert r.status_code == 403 and r.json()["code"] == "mfa_required"  # platform staff: mandatory


# ============================================================================================================== password reset
async def test_password_reset_flow(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    uid, _h, email = await make_user(api, "reset")
    session = (await login(api, email)).json()
    same = {"status": "If an account exists for that address, a reset link has been sent."}
    unknown = await api.post("/v1/auth/password/forgot", json={"email": "nobody@example.com"})
    known = await api.post("/v1/auth/password/forgot", json={"email": email.upper()})
    assert unknown.status_code == known.status_code == 202 and unknown.json() == known.json() == same  # no account enumeration
    assert [m.to for m in outbox.sent if "reset" in m.subject.lower()] == [email]  # only the real account got mail
    token = token_from(outbox, email)
    assert "reset-password?token=" in outbox.sent[-1].body

    new = "a brand new passphrase 99"
    assert (await api.post("/v1/auth/password/reset", json={"token": token, "new_password": "short"})).status_code == 422
    assert (await api.post("/v1/auth/password/reset", json={"token": "x" * 40, "new_password": new})).status_code == 400
    assert (await api.post("/v1/auth/password/reset", json={"token": token, "new_password": new})).status_code == 204
    assert (await api.post("/v1/auth/password/reset", json={"token": token, "new_password": new + "!"})).status_code == 400  # single use
    assert (await login(api, email, PASSWORD)).status_code == 401 and (await login(api, email, new)).status_code == 200
    assert (await api.post("/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})).status_code == 401  # all sessions ended
    assert any("Your password was changed" in m.body for m in outbox.sent if m.to == email)
    assert owner_sql(database, "SELECT email_verified_at IS NOT NULL FROM users WHERE id=%s", uid)[0][0] is True


async def test_reset_links_expire_and_newer_links_replace_older(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    uid, _, email = await make_user(api, "expire")
    await api.post("/v1/auth/password/forgot", json={"email": email})
    first = token_from(outbox, email)
    await api.post("/v1/auth/password/forgot", json={"email": email})
    second = token_from(outbox, email)
    assert first != second
    assert (await api.post("/v1/auth/password/reset", json={"token": first, "new_password": "another good passphrase 1"})).status_code == 400
    owner_sql(database, "UPDATE password_reset_tokens SET expires_at = now() - interval '1 second' WHERE user_id=%s", uid)
    assert (await api.post("/v1/auth/password/reset", json={"token": second, "new_password": "another good passphrase 1"})).status_code == 400


async def test_forgot_password_is_rate_limited_silently(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    _, _, email = await make_user(api, "spam")
    statuses = [(await api.post("/v1/auth/password/forgot", json={"email": email})).status_code for _ in range(6)]
    assert statuses == [202] * 6  # the caller can't tell
    assert len([m for m in outbox.sent if m.to == email and "reset" in m.subject.lower()]) == 3  # but the mailbox is not flooded


async def test_a_password_reset_never_bypasses_the_second_factor(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    """Email compromise alone must not give account access once MFA is on."""
    _, h, email = await make_user(api, "protected")
    await enrol_totp(api, h)
    await api.post("/v1/auth/password/forgot", json={"email": email})
    new = "attacker chosen passphrase 7"
    assert (await api.post("/v1/auth/password/reset", json={"token": token_from(outbox, email), "new_password": new})).status_code == 204
    r = (await login(api, email, new)).json()
    assert r["mfa_required"] is True and r["access_token"] is None  # the attacker has the password and still no session


# ================================================================================================================ password change
async def test_change_password_signs_out_other_devices(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    _, h, email = await make_user(api, "change")
    other = (await login(api, email)).json()
    new = "changed passphrase number 5"
    assert (await api.post("/v1/me/password", json={"current_password": "wrong password!!", "new_password": new}, headers=h)).status_code == 403
    r = await api.post("/v1/me/password", json={"current_password": PASSWORD, "new_password": new}, headers=h)
    assert r.status_code == 200 and r.json()["refresh_token"]
    assert (await api.post("/v1/auth/refresh", json={"refresh_token": other["refresh_token"]})).status_code == 401  # the other device is out
    assert (await api.post("/v1/auth/refresh", json={"refresh_token": r.json()["refresh_token"]})).status_code == 200  # this one continues
    assert (await login(api, email, new)).status_code == 200
    assert any("Your password was changed" in m.body for m in outbox.sent if m.to == email)


# ================================================================================================================ token / hash units
def test_token_types_are_not_interchangeable_and_hashes_are_domain_separated() -> None:
    from app.core.security import InvalidToken, create_access_token, decode_mfa_token, keyed_hash

    uid = uuid.uuid4()
    with pytest.raises(InvalidToken):
        decode_mfa_token(create_access_token(uid))
    with pytest.raises(InvalidToken):
        decode_access_claims(create_mfa_token(uid))
    assert decode_mfa_token(create_mfa_token(uid)) == uid and decode_access_claims(create_access_token(uid)).mfa is False
    assert keyed_hash("otp", "123456") == keyed_hash("otp", "123456") != keyed_hash("recovery", "123456")
