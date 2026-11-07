import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ProblemError
from app.core.logging import mask_pii, pii_processor
from app.core.pagination import clamp_limit, decode_cursor, encode_cursor
from app.main import app

client = TestClient(app)


def test_mask_pii() -> None:
    out = mask_pii("call +254712345678 or 0722000111, mail a.b@firm.co.ke, PIN A123456789Z, id 12345678")
    assert "254712345678" not in out and "0722000111" not in out
    assert "a.b@firm.co.ke" not in out and "A123456789Z" not in out and "12345678" not in out


def test_pii_processor_redacts_sensitive_keys() -> None:
    ev = pii_processor(None, "info", {"event": "x", "prompt": "secret text", "meta": {"token": "t", "n": "0712345678"}})
    assert ev["prompt"] == "[redacted]"
    assert ev["meta"]["token"] == "[redacted]" and "0712345678" not in ev["meta"]["n"]


def test_cursor_roundtrip_and_bad_cursor() -> None:
    assert decode_cursor(encode_cursor({"id": "abc", "n": 3})) == {"id": "abc", "n": 3}
    assert decode_cursor(None) is None
    with pytest.raises(ProblemError):
        decode_cursor("not-a-cursor!!")
    assert clamp_limit(None) == 50 and clamp_limit(10_000) == 200 and clamp_limit(-5) == 1


def test_env_file_from_example_parses(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Regression: `cp .env.example .env` must produce a loadable configuration."""
    from pathlib import Path

    example = (Path(__file__).parents[3] / ".env.example").read_text()
    env = tmp_path / ".env"
    env.write_text(example)
    for key in ("FRONTEND_ORIGINS", "APP_ENV"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=env)
    assert s.frontend_origins == ["http://localhost:3000"] and s.is_local
    env.write_text("FRONTEND_ORIGINS=https://a.example, https://b.example\n")
    assert Settings(_env_file=env).frontend_origins == ["https://a.example", "https://b.example"]


def test_insecure_secret_rejected_outside_local() -> None:
    with pytest.raises(ValueError):
        Settings(app_env="production", jwt_secret="change-me")
    with pytest.raises(ValueError):
        Settings(app_env="staging", jwt_secret="too-short")
    with pytest.raises(ValueError, match="MFA_REQUIRED_FOR_FIRM_ROLES"):
        Settings(app_env="production", jwt_secret="x" * 32)  # production must enforce MFA for staff
    assert Settings(app_env="production", jwt_secret="x" * 32, mfa_required_for_firm_roles=True).is_production
    assert Settings(app_env="local").is_local


def test_health_and_request_id() -> None:
    r = client.get("/v1/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"} and r.headers["X-Request-ID"]
    assert client.get("/v1/health", headers={"X-Request-ID": "abc"}).headers["X-Request-ID"] == "abc"


def test_not_found_is_problem_json() -> None:
    r = client.get("/v1/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "not_found" and body["status"] == 404 and body["instance"] == "/v1/nope"


def test_ready_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.routers import health
    from app.core.config import settings

    async def down() -> bool:
        raise ConnectionError

    async def up() -> bool:
        return True

    monkeypatch.setattr(health, "check_db", down)
    monkeypatch.setattr(health, "check_redis", down)
    r = client.get("/v1/health/ready")
    assert r.status_code == 503 and r.json()["checks"] == {"db": False}  # Redis is not a dependency by default
    monkeypatch.setattr(health, "check_db", up)
    assert client.get("/v1/health/ready").json() == {"status": "ok", "checks": {"db": True}}
    monkeypatch.setattr(settings, "ingest_mode", "celery")
    r = client.get("/v1/health/ready")
    assert r.status_code == 503 and r.json()["checks"] == {"db": True, "redis": False}  # only required in Celery mode


def test_database_outage_is_a_clean_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: with Postgres down, /auth/register returned a bare 500 with a stack trace."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.deps import get_factory

    dead = async_sessionmaker(create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/none"), expire_on_commit=False)
    app.dependency_overrides[get_factory] = lambda: dead
    try:
        r = TestClient(app, raise_server_exceptions=False).post(
            "/v1/auth/register", json={"email": "a@example.com", "password": "correct horse battery 42"}
        )
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503 and r.json()["code"] == "database_unavailable" and r.headers["retry-after"] == "5"
    assert "Traceback" not in r.text and "127.0.0.1" not in r.text  # no internals leak to the caller
