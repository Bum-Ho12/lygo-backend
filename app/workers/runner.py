"""Job worker: claims jobs, runs handlers under a renewed lease, retries with backoff, wakes instantly via LISTEN/NOTIFY."""
import asyncio
import contextlib
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.workers import jobs
from app.workers.jobs import Job

log = structlog.get_logger()
Handler = Callable[[Job], Awaitable[object]]


class PermanentJobError(Exception):
    """Raise from a handler when retrying cannot help (bad payload, missing record): the job goes straight to 'dead'."""


@dataclass(frozen=True)
class Periodic:
    kind: str
    every_seconds: float


class Worker:
    def __init__(
        self, factory: async_sessionmaker[AsyncSession], handlers: dict[str, Handler], *, worker_id: str | None = None, concurrency: int = 2,
        poll_seconds: float = 2.0, lease_seconds: float = 900.0, job_timeout: float = 3600.0, periodic: list[Periodic] | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self.factory, self.handlers, self.concurrency = factory, handlers, concurrency
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.poll_seconds, self.lease_seconds, self.job_timeout, self.periodic = poll_seconds, lease_seconds, job_timeout, periodic or []
        self.engine = engine or factory.kw.get("bind")
        self._stop, self._wake = asyncio.Event(), asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self.processed = 0

    # -- lifecycle --------------------------------------------------------------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def run(self) -> None:
        listener = await self._listen()
        log.info("worker.started", worker=self.worker_id, kinds=sorted(self.handlers), concurrency=self.concurrency)
        try:
            while not self._stop.is_set():
                await self._housekeeping()
                filled = await self._fill()
                if not filled or len(self._tasks) >= self.concurrency:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), self.poll_seconds)
                    self._wake.clear()
        finally:
            await self._shutdown(listener)

    async def run_until_idle(self, max_jobs: int = 10_000) -> int:
        """Process everything currently runnable, then return (tests, CLI `--once`). Does not use the poll loop."""
        n = 0
        while n < max_jobs:
            await self._housekeeping()
            job = await jobs.claim(self.factory, self.worker_id, list(self.handlers), self.lease_seconds)
            if job is None:
                break
            await self._execute(job)
            n += 1
        return n

    # -- internals -----------------------------------------------------------------------------------------------------------------
    async def _housekeeping(self) -> None:
        if reaped := await jobs.reap_expired(self.factory):
            log.warning("worker.reaped_expired_leases", count=reaped)
        for p in self.periodic:
            if p.kind in self.handlers and await jobs.due_periodic(self.factory, p.kind, p.every_seconds):
                async with self.factory() as s, s.begin():
                    await jobs.enqueue(s, p.kind, {}, dedupe_key=f"periodic:{p.kind}")

    async def _fill(self) -> bool:
        claimed = False
        while len(self._tasks) < self.concurrency and not self._stop.is_set():
            job = await jobs.claim(self.factory, self.worker_id, list(self.handlers), self.lease_seconds)
            if job is None:
                break
            claimed = True
            t = asyncio.create_task(self._execute(job))
            self._tasks.add(t)
            t.add_done_callback(lambda done: (self._tasks.discard(done), self._wake.set()))
        return claimed

    async def _execute(self, job: Job) -> None:
        hb = asyncio.create_task(self._heartbeat(job))
        try:
            await asyncio.wait_for(self.handlers[job.kind](job), self.job_timeout)
            if not await jobs.complete(self.factory, job.id, self.worker_id):
                log.warning("job.lease_lost_before_completion", job=str(job.id), kind=job.kind)
            else:
                self.processed += 1
        except PermanentJobError as exc:
            await jobs.fail(self.factory, job.id, self.worker_id, f"{type(exc).__name__}: {exc}", permanent=True)
            log.error("job.dead", job=str(job.id), kind=job.kind, reason=str(exc)[:200])
        except asyncio.CancelledError:
            raise  # shutdown mid-job: the lease will expire and the job will be re-queued by another worker
        except Exception as exc:  # noqa: BLE001 - any handler failure is a retryable job failure
            status = await jobs.fail(self.factory, job.id, self.worker_id, f"{type(exc).__name__}: {exc}")
            log.warning("job.failed", job=str(job.id), kind=job.kind, attempt=job.attempts, next=status, error=f"{type(exc).__name__}: {str(exc)[:200]}")
        finally:
            hb.cancel()

    async def _heartbeat(self, job: Job) -> None:
        interval = max(0.2, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            if not await jobs.extend(self.factory, job.id, self.worker_id, self.lease_seconds):
                return  # lease lost; completion will be refused by the fencing check

    async def _listen(self) -> tuple[object, object] | None:
        """Dedicated connection that LISTENs for NOTIFY so new jobs start in milliseconds instead of waiting for the next poll."""
        if self.engine is None:
            return None
        try:
            conn = await self.engine.connect()
            raw = await conn.get_raw_connection()
            driver = raw.driver_connection
            await driver.add_listener(jobs.NOTIFY_CHANNEL, lambda *_: self._wake.set())
            return conn, driver
        except Exception as exc:  # noqa: BLE001 - polling still works without it
            log.warning("worker.listen_unavailable", error=type(exc).__name__)
            return None

    async def _shutdown(self, listener: tuple[object, object] | None) -> None:
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=30)  # let in-flight jobs finish
            for t in pending:
                t.cancel()
        if listener:
            conn, driver = listener
            with contextlib.suppress(Exception):
                await driver.remove_listener(jobs.NOTIFY_CHANNEL, None)  # type: ignore[attr-defined]
            with contextlib.suppress(Exception):
                await conn.close()  # type: ignore[attr-defined]
        log.info("worker.stopped", worker=self.worker_id, processed=self.processed)
