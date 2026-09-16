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
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import redis.asyncio as redis
from fastapi import WebSocket

from app.core.config import settings

CHANNEL_PREFIX = "fm:plant:"


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
            self._sockets[str(plant_id)].discard(websocket)

    async def publish(self, plant_id: uuid.UUID, event: Event) -> None:
        """Publish through Redis so every API process sees it, not just this one."""
        if self._redis is None:
            await self._fan_out(str(plant_id), event.to_json())
            return
        await self._redis.publish(f"{CHANNEL_PREFIX}{plant_id}", event.to_json())

    async def _listen(self) -> None:
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
        async for message in pubsub.listen():
            if message.get("type") != "pmessage":
                continue
            channel = message["channel"]
            plant_id = channel.removeprefix(CHANNEL_PREFIX)
            await self._fan_out(plant_id, message["data"])

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
                for socket in dead:
                    self._sockets[plant_id].discard(socket)


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
