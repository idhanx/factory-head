"""A small durable scheduler on Redis.

The escalation ladder (§3 Layer 2) is the reason this exists:

    "at 5 minutes the supervisor gets a push notification; at 15 minutes a
     WhatsApp goes automatically to Maintenance or Stores; at 30 minutes the
     Plant Head gets an alert with the running cost. Every escalation is
     timestamped. Nobody can later say they didn't know."

That last sentence rules out `asyncio.sleep`: a process restart between minute 2
and minute 15 must not silently swallow an escalation. The due times live in
Postgres (`escalations`), and this scheduler is the durable *tick* that wakes a
worker to claim them.

It is deliberately ~100 lines rather than a queue framework. The whole job is:
run these few periodic tasks, never run two at once across replicas, and never
lose one to a restart. A Redis lock with a TTL does exactly that.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import redis.asyncio as redis
import structlog

log = structlog.get_logger()

Task = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class PeriodicJob:
    name: str
    interval_seconds: float
    handler: Task
    #: Crash-safety net only: how long another replica waits before assuming
    #: this run died. The lock is released as soon as the job finishes, so this
    #: must exceed the worst-case runtime — but it never delays the next tick.
    lock_seconds: int = 120


class Scheduler:
    def __init__(self, redis_url: str, *, namespace: str = "fm:sched") -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._namespace = namespace
        self._jobs: list[PeriodicJob] = []
        self._stopping = asyncio.Event()

    def every(
        self, seconds: float, *, name: str, lock_seconds: int = 120
    ) -> Callable[[Task], Task]:
        def decorate(handler: Task) -> Task:
            self._jobs.append(PeriodicJob(name, seconds, handler, lock_seconds))
            return handler

        return decorate

    async def run(self) -> None:
        log.info("worker.start", jobs=[job.name for job in self._jobs])
        await asyncio.gather(*(self._loop(job) for job in self._jobs))

    def stop(self) -> None:
        self._stopping.set()

    async def _loop(self, job: PeriodicJob) -> None:
        # Stagger startup so a cold boot doesn't fire every job in the same
        # second and stampede the database.
        await self._sleep(min(job.interval_seconds, 5) * self._jitter(job.name))

        while not self._stopping.is_set():
            started = time.monotonic()
            claimed = False
            try:
                claimed = await self._claim(job)
                if claimed:
                    await job.handler()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A failing job must not kill the loop: the next tick retries.
                # An escalation worker that dies on one bad row stops escalating
                # for every plant.
                log.error("worker.job_failed", job=job.name, error=str(exc))
            finally:
                # Release on completion. Letting the TTL expire instead would
                # make the lock the real interval — a 30-second escalation
                # sweep holding a 60-second lock runs every 60 seconds, and the
                # 5-minute rung starts arriving at six.
                if claimed:
                    await self._release(job)

            elapsed = time.monotonic() - started
            await self._sleep(max(1.0, job.interval_seconds - elapsed))

    async def _claim(self, job: PeriodicJob) -> bool:
        """Only one replica runs a given tick."""
        return bool(
            await self._redis.set(self._key(job), "1", nx=True, ex=job.lock_seconds)
        )

    async def _release(self, job: PeriodicJob) -> None:
        # The TTL is the fallback; a failed release costs one skipped tick,
        # never a stuck scheduler.
        with contextlib.suppress(Exception):
            await self._redis.delete(self._key(job))

    def _key(self, job: PeriodicJob) -> str:
        return f"{self._namespace}:lock:{job.name}"

    async def _sleep(self, seconds: float) -> None:
        """Sleep, but wake immediately on shutdown."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)

    @staticmethod
    def _jitter(name: str) -> float:
        return ((hash(name) % 100) / 100) + 0.1

    async def aclose(self) -> None:
        await self._redis.aclose()
