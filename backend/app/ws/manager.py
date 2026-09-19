"""WebSocket fan-out for the live shopfloor screens.

What actually needs to be live is narrower than it first appears (§3):

* **Downtime is event-driven** — a stop is logged the moment it happens, so the
  rupee ticker on a stopped machine is genuinely live.
* **Production counts are hourly** — the grid refreshes when someone saves.

So this is a broadcast bus, not a streaming engine. Redis pub/sub carries
events between API processes; each process fans out to its own sockets.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import redis.asyncio as redis
import structlog
from fastapi import WebSocket

from app.core.config import settings

log = structlog.get_logger()

CHANNEL_PREFIX = "fm:plant:"

#: How long the fan-out waits before retrying a dropped Redis subscription.
#: Short, because while it is down no stop reaches any grid in the plant.
RESUBSCRIBE_DELAY_SECONDS = 2


def _encode(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"Not JSON serialisable: {type(value)}")


@dataclass(slots=True)
class Event:
    """One thing that happened, addressed to one plant."""

    type: str
    payload: dict

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "payload": self.payload}, default=_encode)


class ConnectionManager:
    def __init__(self) -> None:
        self._sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self._redis: redis.Redis | None = None
        self._listener: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def startup(self) -> None:
        self._redis = redis.from_url(str(settings.redis_url), decode_responses=True)
        self._listener = asyncio.create_task(self._listen())

    async def shutdown(self) -> None:
        if self._listener:
            self._listener.cancel()
        if self._redis:
            await self._redis.aclose()

    async def connect(self, websocket: WebSocket, plant_id: uuid.UUID) -> None:
        await websocket.accept()
        async with self._lock:
            self._sockets[str(plant_id)].add(websocket)

    async def disconnect(self, websocket: WebSocket, plant_id: uuid.UUID) -> None:
        async with self._lock:
            key = str(plant_id)
            self._sockets[key].discard(websocket)
            if not self._sockets[key]:
                del self._sockets[key]

    async def broadcast_local(self, plant_id: uuid.UUID, event: Event) -> None:
        """Send to this process's sockets only.

        For what this process generated for its own sockets — the ticker — so
        the frame does not make a pointless round trip through Redis and out to
        every other process, which would then have nobody to give it to.
        """
        await self._fan_out(str(plant_id), event.to_json())

    def socket_count(self, plant_id: uuid.UUID) -> int:
        return len(self._sockets.get(str(plant_id), ()))

    async def publish(self, plant_id: uuid.UUID, event: Event) -> None:
        """Publish through Redis so every API process sees it, not just this one."""
        if self._redis is None:
            await self._fan_out(str(plant_id), event.to_json())
            return
        await self._redis.publish(f"{CHANNEL_PREFIX}{plant_id}", event.to_json())

    async def _listen(self) -> None:
        """Carry other processes' events to this process's sockets, forever.

        Redis restarts, and so does the network between here and it. Without
        the outer loop, one dropped subscription ends the live channel for
        every tablet this process is serving — silently, because nothing ever
        awaits this task — and the grid sits on stale tiles until each is
        reloaded by hand. Reconnecting is the whole job.
        """
        assert self._redis is not None
        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
                async for message in pubsub.listen():
                    if message.get("type") != "pmessage":
                        continue
                    plant_id = message["channel"].removeprefix(CHANNEL_PREFIX)
                    await self._fan_out(plant_id, message["data"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ws.subscription_lost", error=str(exc))
            finally:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()
            await asyncio.sleep(RESUBSCRIBE_DELAY_SECONDS)

    async def _fan_out(self, plant_id: str, data: str) -> None:
        async with self._lock:
            sockets = list(self._sockets.get(plant_id, ()))

        dead: list[WebSocket] = []
        for socket in sockets:
            try:
                await socket.send_text(data)
            except Exception:
                # A tablet that walked out of Wi-Fi range. Drop it quietly; the
                # client reconnects and re-fetches the grid.
                dead.append(socket)

        if dead:
            async with self._lock:
                bucket = self._sockets.get(plant_id)
                if bucket is not None:
                    bucket.difference_update(dead)
                    if not bucket:
                        del self._sockets[plant_id]


manager = ConnectionManager()


# Event type constants — the client switches on these.
MACHINE_STOPPED = "machine.stopped"
MACHINE_STARTED = "machine.started"
PRODUCTION_RECORDED = "production.recorded"
QUALITY_RECORDED = "quality.recorded"
ESCALATION_FIRED = "escalation.fired"
ACTION_CREATED = "action.created"
SHIFT_CLOSED = "shift.closed"
TICKER = "ticker.tick"
