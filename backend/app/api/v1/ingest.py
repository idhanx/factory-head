"""The data-source seam (§2, §9, §16.3).

    "The data layer must treat 'who produced this data point' as
     interchangeable. A production count or a downtime start/stop must enter
     through one internal contract, whether typed by a supervisor today or
     emitted by a sensor later. Get this right now and the hardware phase is a
     data-source swap, not a rebuild."

Nothing publishes here at launch. It exists, tested, so that the day a CT clamp
is fitted to CNC-3, the only change is a device publishing MQTT — no schema
change, no new cost path, no second version of any formula.

EMQX's rule engine forwards ``factorymanager/+/+/telemetry`` to this endpoint
(see infra/emqx/README.md), so the API needs no MQTT client of its own.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import settings
from app.core.db import set_plant_scope
from app.models import (
    DataSource,
    DowntimeEvent,
    DowntimeStatus,
    Plant,
    ReasonCode,
    Station,
)
from app.schemas import SensorReading
from app.services import capture as capture_service
from app.services import downtime as downtime_service
from app.services import shifts as shift_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/{plant_code}/telemetry")
async def telemetry(
    plant_code: str,
    reading: SensorReading,
    x_ingest_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """One reading from one device.

    Deliberately identical in effect to the equivalent screen action: it calls
    the same service functions, with ``source=SENSOR`` instead of ``MANUAL``.
    The only difference that ever reaches the database is that one column.
    """
    if not settings.mqtt_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Sensor ingest is disabled (MQTT_ENABLED=false)",
        )
    if not settings.ingest_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Sensor ingest has no INGEST_TOKEN configured",
        )
    # Constant-time: a device secret must not leak one character per request.
    if not x_ingest_token or not hmac.compare_digest(x_ingest_token, settings.ingest_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid ingest token")

    plant = await session.scalar(select(Plant).where(Plant.code == plant_code))
    if plant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown plant")
    # No user identity here, so bind the plant scope ourselves: row-level
    # security refuses every read and write until it is set.
    await set_plant_scope(session, plant.id)

    station = await session.scalar(
        select(Station).where(
            Station.plant_id == plant.id, Station.code == reading.station_code
        )
    )
    if station is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown station {reading.station_code!r}"
        )

    shift_session = await shift_service.current_open_session(session, plant.id)
    if shift_session is None:
        # A sensor emitting outside a running shift is not an error — it is a
        # plant that hasn't started its shift yet. Drop it, loudly enough to
        # notice in logs, without failing the device's publish.
        return {"status": "ignored", "reason": "no open shift"}

    occurred_at = reading.occurred_at or datetime.now(UTC)

    if reading.kind == "production":
        row = await capture_service.record_production(
            session,
            plant=plant,
            station=station,
            shift_session=shift_session,
            count=reading.value or 0,
            recorded_at=occurred_at,
            source=DataSource.SENSOR,
            source_ref=reading.device_id,
        )
        return {"status": "recorded", "id": str(row.id)}

    if reading.kind == "downtime_start":
        reason = None
        if reading.reason_code:
            reason = await session.scalar(
                select(ReasonCode).where(
                    ReasonCode.plant_id == plant.id,
                    ReasonCode.code == reading.reason_code,
                )
            )
        event = await downtime_service.open_downtime(
            session,
            plant=plant,
            station=station,
            reason_code=reason,
            shift_session=shift_session,
            started_at=occurred_at,
            source=DataSource.SENSOR,
            source_ref=reading.device_id,
            note="Detected automatically",
        )
        return {"status": "opened", "id": str(event.id)}

    # downtime_end
    event = await session.scalar(
        select(DowntimeEvent).where(
            DowntimeEvent.plant_id == plant.id,
            DowntimeEvent.station_id == station.id,
            DowntimeEvent.status == DowntimeStatus.OPEN,
        )
    )
    if event is None:
        return {"status": "ignored", "reason": "no open downtime for that station"}

    await downtime_service.close_downtime(session, event=event, ended_at=occurred_at)
    return {"status": "closed", "id": str(event.id)}
