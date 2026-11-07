import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_factory
from app.ingestion.ocr.base import get_ocr
from app.ingestion.pipeline import process_document
from app.integrations.email.base import InMemoryEmailSender, get_email_sender
from app.main import app
from app.storage.adapters.local_fs import LocalFsStorage
from app.storage.factory import get_storage
from app.workers.ingest import get_dispatcher
from tests.conftest import PgConfig
from tests.unit.ingestion.factories import FakeOcr


class AwaitingDispatcher:
    """Runs the pipeline to completion before the upload response returns (deterministic tests)."""

    def __init__(self, factory, storage, ocr) -> None:  # type: ignore[no-untyped-def]
        self.factory, self.storage, self.ocr = factory, storage, ocr

    async def dispatch(self, firm_id, document_id) -> None:  # type: ignore[no-untyped-def]
        await process_document(self.factory, self.storage, self.ocr, firm_id, document_id)


@pytest.fixture
def outbox() -> InMemoryEmailSender:
    return InMemoryEmailSender()


@pytest.fixture
def storage(tmp_path) -> LocalFsStorage:  # type: ignore[no-untyped-def]
    return LocalFsStorage(tmp_path / "objects", destination_id="test")


@pytest.fixture
def ocr() -> FakeOcr:
    return FakeOcr()


@pytest.fixture
async def api(database: PgConfig, outbox, storage, ocr):  # type: ignore[no-untyped-def]
    engine = create_async_engine(database.app_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app.dependency_overrides[get_factory] = lambda: factory
    app.dependency_overrides[get_email_sender] = lambda: outbox
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_ocr] = lambda: ocr
    app.dependency_overrides[get_dispatcher] = lambda: AwaitingDispatcher(factory, storage, ocr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
async def factory(database: PgConfig):  # type: ignore[no-untyped-def]
    engine = create_async_engine(database.app_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
