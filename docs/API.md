# API reference

Base: `/api/v1` · Auth: `Authorization: Bearer <access_token>` · Docs: `/docs`

## Money on the wire

Every monetary field appears **twice**:

```json
{ "lost_yesterday_paise": 670200, "lost_yesterday_display": "₹6,702" }
```

The integer is for sorting and arithmetic; the string is what screens render.
The client never picks a rounding rule or a digit-grouping convention — there is
one money formatter in the system and it lives in the kernel.

## Auth

| Method | Path | Notes |
|---|---|---|
| `POST` | `/auth/login` | `{identifier, password, plant_code?}`. `identifier` is a phone number **or** an email — supervisors sign in with a phone and a PIN. |
| `POST` | `/auth/refresh` | Also the plant-switch endpoint: a different plant means a different token. |
| `GET` | `/auth/me` | Current identity and all plant memberships. |

Tokens are **plant-scoped**: the plant is a claim inside the token, not a query
parameter, so a request cannot quietly address another plant's data. Roles are
re-read from the database on every request — a role revoked this morning must
not stay valid until the token expires tonight.

## Capture (Screens 2–5)

| Method | Path | Role | Notes |
|---|---|---|---|
| `GET` | `/grid` | supervisor, owner | Machine grid with live ₹ ticker per open stop |
| `POST` | `/downtime/start` | supervisor, maintenance | Idempotent per station: a second tap returns the open event |
| `POST` | `/downtime/{id}/close` | supervisor, maintenance | Freezes the final cost; cancels unfired escalations |
| `GET` | `/downtime/open` | any | |
| `POST` | `/production` | supervisor | Count **since the last entry**, not a running total |
| `POST` | `/quality` | **quality only** | Restricted deliberately — see below |
| `POST` | `/change-job` | supervisor | §14.3: pauses job A, starts the changeover clock, starts job B |

`POST /quality` is limited to the quality role on purpose. The cross-check in §2
is only worth anything if one person cannot enter both sides of it: *"Two people
would have to collude to fake the numbers."* The response carries
`reconciliation_flagged` and a plain-language `reconciliation_message` when the
inspector's count and the supervisor's disagree beyond tolerance.

## Shift (Screens 1, 6)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/shifts/current` | |
| `POST` | `/shifts/start` | Assigns job orders to stations — the moment events start inheriting their tags |
| `GET` | `/shifts/{id}/closure-check` | The blocker checklist |
| `POST` | `/shifts/{id}/close` | Locks the shift, then evaluates Action triggers |

Closing returns **409** with the blocker list if anything is unresolved. An
owner may pass `override_reason` — the override is written to the audit log and
surfaces in the monthly report. Overriding is allowed; overriding silently is
not.

## Reports (Screens 7, 8)

| Method | Path | Role |
|---|---|---|
| `GET` | `/reports/owner-home` | owner, consultant |
| `GET` | `/reports/losses?start=&end=` | any |
| `GET` | `/reports/meeting?business_date=` | any |
| `GET` | `/reports/kpis/{shift_session_id}` | any |
| `GET` | `/reports/shift-report/{shift_session_id}` | any — the 90-second WhatsApp text |

Loss reports always return `recoverable` and `structural` as **separate arrays
with separate totals**. They are never summed: telling an owner they lost ₹9,380
when ₹2,380 of it was planned maintenance is how a number stops being trusted.

## Actions & Memory (Screens 9, 10)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/actions?state=open\|verified\|monitoring` | Ranked by money |
| `POST` | `/actions` | Anyone can raise one — §5.2's third trigger, and where Kaizen ideas enter |
| `PATCH` | `/actions/{id}` | Also takes `meeting_status` (`done` \| `delayed` + reason) |
| `POST` | `/actions/{id}/close` | Requires the four Factory Memory answers |
| `GET`/`PATCH` | `/actions/{id}/rca` | 5-Why, pre-filled with the event history |
| `GET` | `/actions/memory?q=` | Factory Memory search |

Closing moves an Action to **`monitoring`**, never straight to done. The system
then watches the linked reason code: two sustained weeks promote it to
`verified` and credit the savings; recurrence reopens it.

## AI

| Method | Path | Notes |
|---|---|---|
| `GET` | `/ai/coach/today` | The 5:45 AM briefing — exactly three priorities, or an honest silence |
| `POST` | `/ai/coach/generate` | Force a regeneration |
| `POST` | `/ai/copilot/ask` | `{question, language: "en"\|"ta"}` |

Both responses carry their **tier** and **data basis**. A Copilot answer
includes `tier_available: false` when the question needs more history than the
plant has — and says so in words rather than guessing.

## Layer 5 — plugins

| Method | Path | Notes |
|---|---|---|
| `GET` | `/plugins/compliance?standard=` | Live readiness, re-scored from Core data on read |
| `GET` | `/plugins/compliance/{clause_id}/evidence` | The exact rows that scored the clause — a gap never lists evidence |
| `POST` | `/plugins/compliance/pack?standard=&period_start=&period_end=` | Freeze an evidence pack (owner, quality, consultant) |
| `GET`/`POST` | `/plugins/kaizen` | Submit → scored at once against the plant's own losses, with the reasoning |
| `POST` | `/plugins/kaizen/{id}/decide` | Accept → a real Action with a frozen baseline; decline **requires** a note |
| `GET` | `/plugins/kaizen/leaderboard` | Recognition for contributors |
| `GET` | `/plugins/skills/matrix` | Operators × stations, levels 0–3, coverage per station |
| `PUT` | `/plugins/skills` | Set one cell |
| `GET` | `/plugins/skills/needs` | `gap` (measured ₹/yr from 'No Operator' stops) and `coverage` (₹ per idle shift, `annual_loss_paise` always 0) |
| `POST` | `/plugins/skills/needs/{id}/action` | Raise a training Action |

## Lots — traceability (§3) and stages (§14.2)

| Method | Path | Role |
|---|---|---|
| `GET` | `/trace/search?q=` | any — lot code, job, part or customer; exact lot code first |
| `GET` | `/trace/lots/{lot_code}` | any — the whole story, with a one-sentence `summary` |
| `GET` | `/trace/wip` | any — lots still moving, and where each waits |
| `POST` | `/trace/stages` | supervisor, quality, owner |
| `POST` | `/trace/dispatch` | supervisor, stores, owner — writes a dispatch record and the dispatch stage, and closes the lot |

A lot's `flow` comes from `kernel.lot_flow`: parts between stages are WIP and
never flagged; more parts reaching a stage than left the previous one is always
flagged; parts that passed QC but never left the gate are flagged once despatch is
recorded. A batch lot holding several parts is walked per part (`flow.parts`) —
operation codes mean different stages for different parts.

## KPIs (§8) and the monthly report (§4.4)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/reports/roles/{executive\|supervisor\|quality\|maintenance}?days=7` | Period ends yesterday; 1–90 days |
| `GET` | `/reports/monthly?month=YYYY-MM` | Defaults to last month |

Every OEE above station level is `kernel.aggregate_oee` over per-station runs, so
a line, a shift and a week cannot disagree. MTBF/MTTR are `null`, not `0`, when
there were no breakdowns.

## Onboarding

| Method | Path | Auth |
|---|---|---|
| `GET` | `/onboarding/readiness/questions` | public |
| `POST` | `/onboarding/readiness` | public — answered before we ever visit |
| `GET` | `/onboarding/templates` | any |
| `GET` | `/onboarding/templates/{code}/blank` | any — downloads the .xlsx, with a worked example row |
| `POST` | `/onboarding/upload/{code}` | consultant, owner — parses and **previews**; writes nothing |
| `POST` | `/onboarding/confirm` | consultant, owner — writes the verified upload to the masters |

Upload and confirm are separate steps because the confirm screen *is* the
30–60 minute verification that replaces days of typing: *"Found: 132 part
numbers, 19 machines, 41 operators, 3 shifts. Confirm?"*

## Sensor ingest — the data-source seam

`POST /ingest/{plant_code}/telemetry`

Returns **503** unless `MQTT_ENABLED=true`, so a half-configured broker cannot
quietly inject data. It calls the same service functions the screens do, with
`source=SENSOR` instead of `MANUAL`. **That column is the only difference that
reaches the database** — which is the whole point of building it before any
device exists. See [`infra/emqx/README.md`](../infra/emqx/README.md).

## WebSocket

`ws://host/ws/live?token=<access_token>`

The token is a query parameter because browsers cannot set headers on a
WebSocket handshake; it is the same short-lived access token, verified the same
way.

| Event | Payload |
|---|---|
| `ticker.tick` | Running cost of every open stop, every 5s |
| `machine.stopped` / `machine.started` | The downtime event |
| `production.recorded` / `quality.recorded` | Station and counts |
| `escalation.fired` | Level, station, message, running cost |
| `action.created` | The new Action |
| `shift.closed` | Business date, Actions created |

`ticker.tick` carries `cost` and `minutes` per open event. The client
interpolates between frames using `ticker_rate_per_minute` from `/grid` — and
only the time-based terms, because lost contribution steps one whole part at a
time and drawing a smooth curve through it would show a number the kernel never
produced.

## Errors

| Status | Meaning |
|---|---|
| 400 | Domain rule violated (`ValueError` from a service) |
| 401 | Missing, expired or invalid token |
| 403 | Authenticated, but wrong role or wrong plant |
| 404 | Not found, or not in your plant — deliberately indistinguishable |
| 409 | State conflict: shift already closed, shift locked, event already closed |
