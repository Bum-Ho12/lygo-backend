import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_factory
from app.integrations.email.base import InMemoryEmailSender, get_email_sender
from app.main import app
from tests.conftest import PgConfig


@pytest.fixture
def outbox() -> InMemoryEmailSender:
    return InMemoryEmailSender()


@pytest.fixture
async def api(database: PgConfig, outbox: InMemoryEmailSender):  # type: ignore[no-untyped-def]
    engine = create_async_engine(database.app_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app.dependency_overrides[get_factory] = lambda: factory
    app.dependency_overrides[get_email_sender] = lambda: outbox
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()
