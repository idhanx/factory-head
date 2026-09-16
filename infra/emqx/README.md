# EMQX — the sensor seam

Nothing publishes here at launch. This exists so the hardware phase (§13) is a
**data-source swap, not a rebuild**:

> "The data layer must treat 'who produced this data point' as interchangeable.
>  A production count or a downtime start/stop must enter through one internal
>  contract, whether typed by a supervisor today or emitted by a sensor later."
> — spec §2

## How a device reaches the platform

Devices publish to EMQX; EMQX's **rule engine** forwards matching messages to
the API's ingest endpoint over HTTP. The API therefore needs no MQTT client of
its own, and the broker handles what brokers are good at — QoS, offline
buffering when a Tamil Nadu industrial estate loses Wi-Fi, and per-device auth.

```
CT clamp / pulse counter / controller tap
        │  MQTT (QoS 1)
        ▼
      EMQX  ──rule engine──►  POST /api/v1/ingest/{plant_code}/telemetry
```

## Topic design

```
factorymanager/{plant_code}/{station_code}/telemetry
```

Plant and station are in the topic, not the payload, so an ACL can restrict a
device to exactly its own station:

```
{allow, {user, "device-cnc3"}, publish, ["factorymanager/KUMARAN/CNC-3/telemetry"]}.
```

## Payload

Exactly the shape `app/schemas/SensorReading` accepts:

```json
{
  "station_code": "CNC-3",
  "kind": "production",
  "value": 24,
  "occurred_at": "2026-09-12T10:40:00+05:30",
  "device_id": "ct-clamp-003"
}
```

`kind` is one of `production`, `downtime_start`, `downtime_end`. A
`downtime_start` may carry a `reason_code`; most sensors cannot know the reason,
so the supervisor still supplies it — and the Shift Closure Lock still refuses
to let the shift close until they do.

## Rule engine SQL

Create in the EMQX dashboard (`http://localhost:18083`, default login
`admin` / the password in `docker-compose.yml`), or via the REST API:

```sql
SELECT
  payload.station_code   as station_code,
  payload.kind           as kind,
  payload.value          as value,
  payload.reason_code    as reason_code,
  payload.occurred_at    as occurred_at,
  payload.device_id      as device_id,
  split(topic, '/')[2]   as plant_code
FROM
  "factorymanager/+/+/telemetry"
```

Action: **HTTP Server**, `POST http://api:8080/api/v1/ingest/${plant_code}/telemetry`,
body `${payload}`, header `X-Ingest-Token: <shared secret>`.

## Before any device ships

1. Set `MQTT_ENABLED=true` on the API. The ingest endpoint returns 503 otherwise
   — deliberately, so a half-configured broker cannot quietly inject data.
2. Replace EMQX's default dashboard password and enable per-device
   username/password or client-certificate auth.
3. Decide which IoT device serves which machine class (§16.5, open decision 1).
   The data contract above does not change whichever way that goes, which is the
   entire point of building it now.

## What does not change when sensors arrive

Everything above the data layer: the cost engine, escalations, morning meetings,
the AI Coach, Copilot, Memory and the plugins. None of them ever cared where the
data came from — they read `downtime_events` and `production_counts`, and the
only difference a sensor makes is the value in the `source` column.
