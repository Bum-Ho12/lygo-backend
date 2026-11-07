"""Malware scanning. Production uses ClamAV (clamd); local dev uses a scanner that only knows EICAR.

Fail closed: if no scanner is available outside local env, uploads are refused.
"""
import asyncio
import struct
from dataclasses import dataclass
from typing import IO, Protocol

from app.core.config import settings

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class ScannerUnavailable(Exception): ...


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    signature: str = ""


class Scanner(Protocol):
    async def scan(self, f: IO[bytes]) -> ScanResult: ...


class EicarOnlyScanner:
    """Dev/test only: flags the EICAR test string. NOT an antivirus."""

    async def scan(self, f: IO[bytes]) -> ScanResult:
        f.seek(0)
        tail = b""
        while chunk := f.read(1 << 20):
            if EICAR in tail + chunk:
                f.seek(0)
                return ScanResult(False, "Eicar-Test-Signature")
            tail = chunk[-len(EICAR):]
        f.seek(0)
        return ScanResult(True)


class ClamdScanner:
    """Minimal clamd INSTREAM client."""

    def __init__(self, host: str, port: int = 3310, timeout: float = 30.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    async def scan(self, f: IO[bytes]) -> ScanResult:
        f.seek(0)
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), self.timeout)
            try:
                writer.write(b"zINSTREAM\0")
                while chunk := f.read(64 * 1024):
                    writer.write(struct.pack("!I", len(chunk)) + chunk)
                    await writer.drain()
                writer.write(struct.pack("!I", 0))
                await writer.drain()
                reply = (await asyncio.wait_for(reader.readuntil(b"\0"), self.timeout)).rstrip(b"\0").decode(errors="replace")
            finally:
                writer.close()
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            raise ScannerUnavailable(str(exc)) from exc
        finally:
            f.seek(0)
        if reply.endswith("OK"):
            return ScanResult(True)
        if reply.endswith("FOUND"):
            return ScanResult(False, reply.removeprefix("stream: ").removesuffix(" FOUND"))
        raise ScannerUnavailable(f"unexpected clamd reply: {reply!r}")


class _Unavailable:
    async def scan(self, f: IO[bytes]) -> ScanResult:
        raise ScannerUnavailable("no virus scanner configured (set CLAMD_HOST)")


def get_scanner() -> Scanner:
    """FastAPI dependency; override in tests."""
    if settings.clamd_host:
        return ClamdScanner(settings.clamd_host, settings.clamd_port)
    return EicarOnlyScanner() if settings.is_local else _Unavailable()
