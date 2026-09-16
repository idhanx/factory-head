"""The live channel. One socket per device, scoped to one plant."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.db import SessionFactory, set_plant_scope
from app.core.security import decode_token
from app.models import DowntimeEvent, DowntimeStatus
from app.services.costing import cost_open_event
from app.ws.manager import TICKER, Event, manager

router = APIRouter()

TICKER_INTERVAL_SECONDS = 5


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
    ticker = asyncio.create_task(_ticker(websocket, plant_id))

    try:
        while True:
            # The client sends nothing but keepalives; reading is how we notice
            # a socket that has gone away.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ticker.cancel()
        await manager.disconnect(websocket, plant_id)


async def _ticker(websocket: WebSocket, plant_id: uuid.UUID) -> None:
    """Push the running cost of every open stop, every few seconds.

    The browser interpolates between these frames using the per-minute rate the
    server sends, so the number climbs smoothly without the client ever doing
    arithmetic the kernel didn't authorise.
    """
    try:
        while True:
            await asyncio.sleep(TICKER_INTERVAL_SECONDS)

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
                    continue

                now = datetime.now(UTC)
                payload = {
                    "as_of": now.isoformat(),
                    "events": [
                        {
                            "event_id": str(event.id),
                            "station_id": str(event.station_id),
                            "minutes": float(
                                cost_open_event(event, as_of=now).duration_minutes
                            ),
                            "cost": cost_open_event(event, as_of=now).total,
                        }
                        for event in open_events
                    ],
                }

            await websocket.send_text(Event(TICKER, payload).to_json())
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    except Exception:
        return
