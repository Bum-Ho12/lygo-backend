"""SSRF guard for firm-supplied storage endpoints."""
import pytest

from app.core.config import settings
from app.storage import endpoint_guard
from app.storage.endpoint_guard import EndpointNotAllowed, check_endpoint

pytestmark = pytest.mark.asyncio


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")


def resolves_to(monkeypatch: pytest.MonkeyPatch, *ips: str) -> None:
    monkeypatch.setattr(endpoint_guard, "_addresses", lambda host, port: list(ips))


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.10", "169.254.169.254",  # metadata service
    "100.64.0.1", "0.0.0.0", "::1", "fd00::1", "fe80::1", "224.0.0.1",
])
async def test_internal_addresses_are_refused(production, monkeypatch, ip: str) -> None:  # type: ignore[no-untyped-def]
    resolves_to(monkeypatch, ip)
    with pytest.raises(EndpointNotAllowed, match="private or internal"):
        await check_endpoint("https://s3.innocent-looking.example")


async def test_one_internal_address_among_public_ones_is_refused(production, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    resolves_to(monkeypatch, "52.95.110.1", "10.1.2.3")
    with pytest.raises(EndpointNotAllowed):
        await check_endpoint("https://s3.example.com")


async def test_public_https_endpoint_is_allowed(production, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    resolves_to(monkeypatch, "52.95.110.1")
    await check_endpoint("https://s3.af-south-1.amazonaws.com")
    await check_endpoint(None)  # provider default
    await check_endpoint("")


@pytest.mark.parametrize(("url", "reason"), [
    ("http://s3.example.com", "https"),
    ("ftp://s3.example.com", "https"),
    ("https://user:pw@s3.example.com", "credentials"),
    ("https://s3.example.com/bucket/path", "no path"),
    ("https:///nohost", "host name"),
])
async def test_malformed_endpoints_are_refused(production, monkeypatch, url: str, reason: str) -> None:  # type: ignore[no-untyped-def]
    resolves_to(monkeypatch, "52.95.110.1")
    with pytest.raises(EndpointNotAllowed, match=reason):
        await check_endpoint(url)


async def test_unknown_host_is_refused(production, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import socket

    def fail(host, port):  # type: ignore[no-untyped-def]
        raise socket.gaierror("nope")

    monkeypatch.setattr(endpoint_guard, "_addresses", fail)
    with pytest.raises(EndpointNotAllowed, match="could not be found"):
        await check_endpoint("https://no-such-host.invalid")


async def test_local_development_may_use_localhost(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "app_env", "local")
    await check_endpoint("http://127.0.0.1:9000")  # MinIO / moto
