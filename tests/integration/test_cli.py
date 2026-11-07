"""Operator CLI: platform roles are granted from a server shell only."""
import io
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import cli
from tests.conftest import PgConfig
from tests.integration.helpers import PASSWORD, make_user, owner_sql


@pytest.fixture
def test_db(database: PgConfig, monkeypatch: pytest.MonkeyPatch) -> PgConfig:
    monkeypatch.setattr(cli, "make_sessionmaker", lambda: async_sessionmaker(create_async_engine(database.app_url, poolclass=NullPool), expire_on_commit=False))
    return database


def _email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}@example.com"


def _run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str, stdin: str = "") -> tuple[int, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def test_create_admin_with_a_new_account_and_audit(test_db: PgConfig, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    email = _email("ops")
    code, out = _run(monkeypatch, capsys, "create-platform-admin", "--email", email.upper(), "--name", "Ops", "--password-stdin", stdin=PASSWORD + "\n")
    assert code == 0 and "Created" in out and PASSWORD not in out
    (uid, role, pw_hash), = owner_sql(test_db, "SELECT id, platform_role, password_hash FROM users WHERE email=%s", email)  # email normalised
    assert role == "platform_admin" and pw_hash and PASSWORD not in pw_hash
    assert owner_sql(test_db, "SELECT meta->>'via', meta->>'role' FROM audit_events WHERE action='platform_role.grant' AND target=%s", f"user:{uid}") == [("cli", "platform_admin")]
    code, out = _run(monkeypatch, capsys, "create-platform-admin", "--email", email)
    assert code == 0 and "already" in out  # idempotent


def test_weak_password_and_bad_email_are_refused(test_db: PgConfig, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    email = _email("weak")
    code, out = _run(monkeypatch, capsys, "create-platform-admin", "--email", email, "--password-stdin", stdin="short\n")
    assert code == 2 and "at least" in out
    code, out = _run(monkeypatch, capsys, "create-platform-admin", "--email", email, "--password-stdin", stdin="aaaaaaaaaaaaaaaa\n")
    assert code == 2 and "repetitive" in out
    assert owner_sql(test_db, "SELECT count(*) FROM users WHERE email=%s", email)[0][0] == 0
    assert _run(monkeypatch, capsys, "create-platform-admin", "--email", "not-an-email")[0] == 2


def test_list_and_revoke(test_db: PgConfig, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    email = _email("sup")
    _run(monkeypatch, capsys, "create-platform-admin", "--email", email, "--role", "platform_support", "--password-stdin", stdin=PASSWORD + "\n")
    code, out = _run(monkeypatch, capsys, "list-platform-users")
    assert code == 0 and email in out and "platform_support" in out and "mfa=OFF" in out
    code, out = _run(monkeypatch, capsys, "revoke-platform-role", "--email", email)
    assert code == 0 and "Removed platform_support" in out
    assert owner_sql(test_db, "SELECT platform_role FROM users WHERE email=%s", email) == [(None,)]
    assert _run(monkeypatch, capsys, "revoke-platform-role", "--email", _email("nobody"))[0] == 2


@pytest.mark.asyncio
async def test_promoted_account_can_review_applications(api, test_db: PgConfig) -> None:  # type: ignore[no-untyped-def]
    _, h, email = await make_user(api, "promoted")
    assert (await api.get("/v1/platform/firm-applications", headers=h)).status_code == 403
    out = await cli.grant(email, "platform_admin", "", password_stdin=False)  # existing account: no password needed
    assert "now platform_admin" in out
    t = (await api.post("/v1/auth/login", json={"email": email, "password": PASSWORD})).json()
    assert (await api.get("/v1/platform/firm-applications", headers={"Authorization": f"Bearer {t['access_token']}"})).status_code == 200
