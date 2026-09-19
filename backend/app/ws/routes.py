"""The live channel. One socket per device, scoped to one plant."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime

import jwt
import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.db import SessionFactory, set_plant_scope
from app.core.security import decode_token
from app.models import DowntimeEvent, DowntimeStatus
from app.services.costing import cost_open_event
from app.ws.manager import TICKER, Event, manager

log = structlog.get_logger()

router = APIRouter()

TICKER_INTERVAL_SECONDS = 5

#: One ticker per plant per process, shared by every tablet watching that plant.
#: It used to be one per socket: ten tablets on the grid meant ten identical
#: queries every five seconds, and a plant that opened more screens paid for it
#: in database load rather than in anything anyone could see.
_tickers: dict[str, asyncio.Task] = {}


@router.websocket("/ws/live")
async def live(websocket: WebSocket, token: str = Query(...)):
    """Push channel for the machine grid.

    The token arrives as a query parameter because browsers cannot set headers
    on a WebSocket handshake. It is the same short-lived access token the REST
    API uses, verified the same way.
    """
    try:
        payload = decode_token(token)
        plant_id = uuid.UUID(payload["plant"])
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        await websocket.close(code=4401)
        return

    await manager.connect(websocket, plant_id)
    _start_ticker(plant_id)

    try:
        while True:
            # The client sends nothing but keepalives; reading is how we notice
            # a socket that has gone away.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket, plant_id)
        _stop_ticker_if_idle(plant_id)


def _start_ticker(plant_id: uuid.UUID) -> None:
    key = str(plant_id)
    task = _tickers.get(key)
    if task is None or task.done():
        _tickers[key] = asyncio.create_task(_ticker(plant_id))


def _stop_ticker_if_idle(plant_id: uuid.UUID) -> None:
    """The last tablet watching this plant closed its screen; stop counting."""
    if manager.socket_count(plant_id):
        return
    task = _tickers.pop(str(plant_id), None)
    if task is not None:
        task.cancel()


async def _ticker(plant_id: uuid.UUID) -> None:
    """Push the running cost of every open stop, every few seconds.

    The browser interpolates between these frames using the per-minute rate the
    server sends, so the number climbs smoothly without the client ever doing
    arithmetic the kernel didn't authorise.
    """
    while True:
        try:
            await asyncio.sleep(TICKER_INTERVAL_SECONDS)
            payload = await _open_stop_costs(plant_id)
            if payload is not None:
                await manager.broadcast_local(plant_id, Event(TICKER, payload))
        except asyncio.CancelledError:
            return
        except Exception as exc:
            # A blip on the database must not freeze the rupee counter on a
            # stopped machine until someone reloads the tablet. Say so once,
            # then carry on — the next tick re-reads the truth anyway.
            log.warning("ws.ticker_failed", plant_id=str(plant_id), error=str(exc))


async def _open_stop_costs(plant_id: uuid.UUID) -> dict | None:
    async with SessionFactory() as session:
        await set_plant_scope(session, plant_id)
        open_events = (
            await session.scalars(
                select(DowntimeEvent).where(
                    DowntimeEvent.plant_id == plant_id,
                    DowntimeEvent.status == DowntimeStatus.OPEN,
                )
            )
        ).all()

        if not open_events:
            return None

        now = datetime.now(UTC)
        events = []
        for event in open_events:
            live_cost = cost_open_event(event, as_of=now)
            events.append(
                {
                    "event_id": str(event.id),
                    "station_id": str(event.station_id),
                    "minutes": float(live_cost.duration_minutes),
                    "cost": live_cost.total,
                }
            )
        return {"as_of": now.isoformat(), "events": events}


async def stop_all_tickers() -> None:
    """Cancel every plant ticker — used when the process is shutting down."""
    tasks = list(_tickers.values())
    _tickers.clear()
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
