import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_factory
from app.engine.ai.providers.factory import get_llm
from app.engine.ai.providers.fake import ExtractiveLLM
from app.engine.rag.embeddings.base import HashingEmbedder
from app.engine.rag.embeddings.factory import get_embedder
from app.ingestion.ocr.base import get_ocr
from app.ingestion.pipeline import process_document
from app.integrations.email.base import InMemoryEmailSender, get_email_sender
from app.integrations.sms.base import SmsResult
from app.integrations.sms.factory import get_sms_provider
from app.main import app
from app.storage.adapters.local_fs import LocalFsStorage
from app.storage.factory import get_storage
from app.storage.resolver import StorageResolver, get_resolver
from app.workers.ingest import get_dispatcher
from tests.conftest import PgConfig
from tests.unit.ingestion.factories import FakeOcr


class CapturingSms:
    """Records OTP/notification SMS instead of sending. Set `.fail` to an exception to simulate provider errors."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail: Exception | None = None

    async def send(self, to, text, *, sender_id=None, callback_url=None):  # type: ignore[no-untyped-def]
        if self.fail:
            raise self.fail
        self.sent.append((to, text))
        return SmsResult("1", "queued", 1)

    def last_code(self) -> str:
        import re

        return re.search(r"\b(\d{6})\b", self.sent[-1][1]).group(1)  # type: ignore[union-attr]


class AwaitingDispatcher:
    """Runs the pipeline to completion before the upload response returns (deterministic tests)."""

    def __init__(self, factory, resolver, ocr, embedder=None) -> None:  # type: ignore[no-untyped-def]
        self.factory, self.resolver, self.ocr, self.embedder = factory, resolver, ocr, embedder

    async def dispatch(self, firm_id, document_id) -> None:  # type: ignore[no-untyped-def]
        await process_document(self.factory, self.resolver, self.ocr, firm_id, document_id, self.embedder)


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
def sms_box() -> dict:  # type: ignore[type-arg]
    return {"p": CapturingSms()}


@pytest.fixture(autouse=True)
def _clean_throttle(database: PgConfig):  # type: ignore[no-untyped-def]
    """Rate-limit counters live in the shared test database; start every test from a clean slate."""
    from tests.integration.helpers import owner_sql

    owner_sql(database, "DELETE FROM auth_throttle")


@pytest.fixture
def llm_box() -> dict:  # type: ignore[type-arg]
    """Tests swap `llm_box["llm"]` to script model behaviour."""
    return {"llm": ExtractiveLLM()}


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder()


@pytest.fixture
async def api(database: PgConfig, outbox, storage, ocr, embedder, llm_box, sms_box):  # type: ignore[no-untyped-def]
    engine = create_async_engine(database.app_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app.dependency_overrides[get_factory] = lambda: factory
    app.dependency_overrides[get_email_sender] = lambda: outbox
    resolver = StorageResolver(storage)
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_resolver] = lambda: resolver
    app.dependency_overrides[get_ocr] = lambda: ocr
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_llm] = lambda: llm_box["llm"]
    app.dependency_overrides[get_sms_provider] = lambda: sms_box["p"]
    app.dependency_overrides[get_dispatcher] = lambda: AwaitingDispatcher(factory, resolver, ocr, embedder)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
async def factory(database: PgConfig):  # type: ignore[no-untyped-def]
    engine = create_async_engine(database.app_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
