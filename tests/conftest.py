"""Shared fixtures. DB fixtures need a Postgres with pgvector (docker compose `db`, or CI service).

Environment (defaults match docker-compose): LYGO_TEST_PG_HOST/PORT, LYGO_TEST_OWNER_USER/PASSWORD,
LYGO_TEST_APP_USER/PASSWORD. Set REQUIRE_DB=1 (CI does) to fail instead of skip when unreachable.
"""
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]

_CREDENTIALS = ("anthropic_api_key", "voyage_api_key", "thrivewave_api_key", "thrivewave_base_url", "smtp_host", "smtp_username", "smtp_password", "llm_api_key", "llm_base_url")


@pytest.fixture(autouse=True)
def _hermetic_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never use real third-party credentials from a developer's .env (they would spend money and make results
    non-deterministic). The AI evaluation can opt in explicitly: `EVAL_REAL_MODEL=1 make eval`."""
    from app.core.config import settings

    if os.environ.get("EVAL_REAL_MODEL"):
        return
    for name in _CREDENTIALS:
        monkeypatch.setattr(settings, name, "")
TEST_DB = "lygo_test"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class PgConfig:
    host: str
    port: str
    owner: str
    owner_pw: str
    app: str
    app_pw: str

    def owner_dsn(self, db: str) -> str:
        return f"postgresql://{self.owner}:{self.owner_pw}@{self.host}:{self.port}/{db}"

    @property
    def migrator_url(self) -> str:
        return f"postgresql+psycopg://{self.owner}:{self.owner_pw}@{self.host}:{self.port}/{TEST_DB}"

    @property
    def app_url(self) -> str:
        return f"postgresql+asyncpg://{self.app}:{self.app_pw}@{self.host}:{self.port}/{TEST_DB}"


@dataclass(frozen=True)
class World:
    """Two firms x two clients, each with two matters."""

    firm_a: uuid.UUID
    firm_b: uuid.UUID
    a1: uuid.UUID
    a2: uuid.UUID
    b1: uuid.UUID
    b2: uuid.UUID
    user_a: uuid.UUID
    user_b: uuid.UUID
    user_a1: uuid.UUID  # client portal user of A1
    platform_user: uuid.UUID


def _uid() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture(scope="session")
def pg() -> PgConfig:
    import psycopg

    cfg = PgConfig(
        _env("LYGO_TEST_PG_HOST", "localhost"), _env("LYGO_TEST_PG_PORT", "5432"),
        _env("LYGO_TEST_OWNER_USER", "lygo_owner"), _env("LYGO_TEST_OWNER_PASSWORD", "lygo_owner"),
        _env("LYGO_TEST_APP_USER", "lygo_app"), _env("LYGO_TEST_APP_PASSWORD", "lygo_app"),
    )
    try:
        with psycopg.connect(cfg.owner_dsn("postgres"), connect_timeout=3):
            pass
    except Exception as exc:  # noqa: BLE001
        if os.environ.get("REQUIRE_DB"):
            pytest.fail(f"Postgres required but unreachable: {exc}")
        pytest.skip(f"Postgres not reachable ({exc.__class__.__name__}); start `docker compose up -d db`")
    return cfg


@pytest.fixture(scope="session")
def database(pg: PgConfig):  # type: ignore[no-untyped-def]
    """Fresh database with init-db.sql applied and all migrations run as the owner role."""
    import psycopg
    from alembic import command
    from alembic.config import Config

    with psycopg.connect(pg.owner_dsn("postgres"), autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {TEST_DB}")
    init_sql = (ROOT / "scripts" / "init-db.sql").read_text()
    init_sql = re.sub(r"DATABASE lygo\b", f"DATABASE {TEST_DB}", init_sql)
    with psycopg.connect(pg.owner_dsn(TEST_DB), autocommit=True) as c:
        c.execute(init_sql)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", pg.migrator_url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    yield pg
    with psycopg.connect(pg.owner_dsn("postgres"), autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


@pytest.fixture(scope="session")
def world(database: PgConfig) -> World:
    """Seed as the (superuser) owner so seeding itself does not depend on the policies under test."""
    import psycopg

    w = World(*(_uid() for _ in range(10)))
    with psycopg.connect(database.owner_dsn(TEST_DB), autocommit=True) as c:
        for fid, slug in ((w.firm_a, "firm-a"), (w.firm_b, "firm-b")):
            c.execute("INSERT INTO firms (id, slug, name) VALUES (%s, %s, %s)", (fid, slug, slug.upper()))
        for uid, mail in ((w.user_a, "a"), (w.user_b, "b"), (w.user_a1, "a1"), (w.platform_user, "p")):
            c.execute("INSERT INTO users (id, email) VALUES (%s, %s)", (uid, f"{mail}@test.local"))
        c.execute("INSERT INTO firm_memberships (firm_id, user_id, role) VALUES (%s, %s, 'advocate')", (w.firm_a, w.user_a))
        c.execute("INSERT INTO firm_memberships (firm_id, user_id, role) VALUES (%s, %s, 'advocate')", (w.firm_b, w.user_b))
        for cid, fid, name in ((w.a1, w.firm_a, "A1"), (w.a2, w.firm_a, "A2"), (w.b1, w.firm_b, "B1"), (w.b2, w.firm_b, "B2")):
            c.execute("INSERT INTO clients (id, firm_id, name) VALUES (%s, %s, %s)", (cid, fid, name))
            for n in (1, 2):
                c.execute(
                    "INSERT INTO matters (firm_id, client_id, title) VALUES (%s, %s, %s)", (fid, cid, f"{name}-matter-{n}")
                )
        c.execute(
            "INSERT INTO client_memberships (client_id, firm_id, user_id, role) VALUES (%s, %s, %s, 'client_user')",
            (w.a1, w.firm_a, w.user_a1),
        )
    return w
