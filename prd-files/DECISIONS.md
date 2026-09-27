# Decisions made during the build

Every choice here was forced by something the specification said, or by
something it left open. Where the spec is ambiguous this document says so
rather than silently picking a side.

---

## 1. The ₹3,290 discrepancy — unresolved, and deliberately not "fixed"

**Status: needs the spec owner.** This is the only genuinely blocking item.

§3 Layer 2 states the CNC-3 tool breakage cost **₹3,290**: ₹329 machine + ₹141
labour + ₹165 overhead + **₹2,655 lost contribution**, describing that last
figure as *"(47 ÷ 2.4 min cycle) = 19 parts × ₹38 margin."*

**19 × ₹38 = ₹722, not ₹2,655.** Applying §8.1's own formula with the spec's own
inputs (D=47, MCR=420, LCR=180, OHR=210, CT=2.4, CM=38, BF=1.0) gives
**₹1,356.50**. For ₹2,655 to hold, CM would have to be ₹139.74 — but ₹38 is
stated independently in §4.1 (`₹110 − ₹72 = ₹38`) and again in the T2 example.

₹3,290 is not an isolated typo: it recurs in the ranked loss table, the AI Coach
example, and the day-in-the-life narrative, and it anchors the ₹9,380 shift
total. It is also the number in the sales demo.

**What we did:** the kernel implements the §8.1 formula, because the formula is
the contract. `tests/test_kernel_cost.py::test_spec_total_discrepancy_is_recorded_not_papered_over`
pins ₹1,356.50 and will fail loudly if anyone "corrects" the kernel to reproduce
₹3,290. **Ask the spec owner which is authoritative — the ₹38 margin, or the
₹2,655 figure — before the first demo.**

A related, smaller note: the spec prints overhead as ₹165 where 47/60 × ₹210 is
exactly ₹164.50. That one *is* just display rounding to whole rupees, and the
ledger keeps the half-rupee so the four components sum to the total.

---

## 2. Reason codes carry four attributes, not one

T6 (§15) gives each downtime reason a single R/S tag. Three different formulas
read this row and they need different answers:

| Attribute | Drives | Why it cannot be the R/S tag |
|---|---|---|
| `recoverability` | Money report, Action triggers | — |
| `oee_treatment` | Availability | **Power Cut is Structural but unplanned.** Mapping S → planned silently *improves* OEE on every power cut, and a plant manager will notice. |
| `is_breakdown` | MTBF / MTTR | Otherwise these string-match the label `'Machine Breakdown'`, which breaks the moment a plant renames it. |
| `escalation_route` | Who gets the 15-minute WhatsApp | §3 Layer 2 says the system picks the recipient from the reason code. |

**This adds one column to T6** and is seeded with working defaults, so no plant
has to think about it at onboarding.

---

## 3. The job order spans shifts, days and machines

§14.3 is explicit: *"300 pcs Monday on CNC-3, 200 pcs Tuesday on CNC-5 — under
one job order, one lot lineage."*

Attaching a job order to a shift session would make split jobs, the Change Job
event and multi-day throughput time unrepresentable. So the shift-scoped entity
is a **`job_session`** — this station ran this job from t1 to t2 — and events
attach to that.

This has a consequence worth knowing: **an event on a station with no active job
has no part, therefore no cycle time and no contribution margin.** It is costed
on machine, labour and overhead only. That is correct — the product must not
claim lost contribution on parts it cannot name — and it is pinned by
`test_an_event_with_no_job_running_costs_only_time`.

---

## 4. Every event freezes its cost inputs

If the owner corrects a machine rate in week 6 and yesterday's ₹9,380 silently
becomes ₹11,200, trust dies — and every Recovered Profit baseline shifts
underneath its own Action.

So at the moment an event is costed, the kernel's *resolved inputs* (MCR, LCR,
OHR, CT, CM, BF, costing mode, formula version) are written onto the row beside
the outputs. A locked shift is arithmetically frozen. Rates are effective-dated
and apply forward only.

Verified by `test_a_later_rate_change_does_not_restate_a_past_event`.

---

## 5. Money is integer paise, everywhere

`Recovered Profit` is an accounting ledger an owner uses to decide whether to
renew. Floating-point drift in a renewal argument is unacceptable.

All monetary values are Python `int` paise and `BIGINT` in Postgres. Formatting
to ₹ happens only at the presentation edge — the API sends both a `*_paise`
integer and a pre-formatted `*_display` string, so the browser never picks a
rounding rule. Components are each rounded once, half-up, and the total is the
sum of the rounded components, so a displayed breakdown always adds up.

---

## 6. The AI never computes a number

The Coach and Copilot are grounded generators, not calculators. Facts are
assembled by deterministic SQL and the kernel; the model receives a structured,
pre-computed fact bundle and turns it into prose.

Two enforcement points, neither of them a prompt instruction:

- **Tier gating is a data-sufficiency check in code** (`kernel/rules.py::coach_tier`).
  A Copilot with three days of history that confidently answers pattern
  questions will be confidently wrong, and one wrong answer to an owner kills
  trust in every number.
- **Every generation falls back to deterministic text** if the LLM is
  unavailable. A supervisor at 5:45 AM needs their three priorities; "the model
  is down" is not one of them.

Two bugs found and fixed while testing this, both worth knowing about:

- The Coach was surfacing *structural* losses (planned maintenance) as
  coachable priorities. Coaching someone about money the plant cannot recover
  wastes the one briefing they read. Patterns are now filtered to recoverable
  reasons only.
- The Copilot answered "Why does **Drill-2** keep failing?" with **CNC-3**'s
  pattern — right data, wrong machine. It now extracts the station named in the
  question, scopes the facts to it, and says plainly when that machine has no
  pattern.

---

## 7. Actions are evaluated at shift close, not continuously

An Action born mid-shift interrupts the person who should be running the shift,
and the recurrence rule needs the whole window to judge a pattern anyway.

The rolling window is enforced in the kernel, not left to the caller: three
material waits spread over two months are not a pattern, they are Tuesdays. Only
the events actually inside the window are attached, so the ₹ total describes the
pattern being claimed.

**A note on demo data.** The first seed produced 396 Actions over six weeks —
exactly the action fatigue §12 (risk 10) warns kills products like this. The
rules were correct and deduplication was working; the *seed* was generating a
third of all stops longer than 30 minutes, which no real plant sees. Durations
were rebalanced to ~12% over threshold. It is a useful reminder that the
thresholds in `plants` are per-plant for a reason.

---

## 8. Substitutions from the specified stack

### psycopg3 instead of asyncpg

One driver serves both the async application and Alembic's synchronous
migrations — no second connection stack to keep configured, no second set of
type adapters. Same wire protocol, same performance envelope at this scale.

### A small Redis scheduler instead of Celery / arq

The only durable-delay requirement in the product is the 5/15/30-minute
escalation ladder, and its due times already live in Postgres (`escalations`)
precisely because *"every escalation is timestamped; nobody can later say they
didn't know."* The worker therefore needs to be a durable **tick**, not a queue:
run a few periodic jobs, never two at once across replicas, never lose one to a
restart. That is a Redis `SET NX EX` and about a hundred lines.

One bug worth recording from building it: the first version let the lock expire
on its TTL rather than releasing it on completion, which silently made the lock
duration the real interval — a 30-second escalation sweep holding a 60-second
lock runs every 60 seconds, and the 5-minute rung starts arriving at six. The
lock is now released when the job finishes; the TTL is only a crash net.

### EMQX rule engine instead of an in-process MQTT client

Devices publish to EMQX; EMQX forwards to `POST /api/v1/ingest/{plant}/telemetry`.
The broker does what brokers are good at — QoS, offline buffering when a Tamil
Nadu industrial estate loses Wi-Fi, per-device auth — and the API keeps one
ingest path that is identical to the manual one apart from the `source` column.

### Postgres FTS before LlamaIndex/pgvector

Factory Memory with three weeks of history is worthless (§3 Layer 4); its value
compounds over years. Postgres full-text search carries retrieval far past 100
plants. `pgvector` and LlamaIndex are in `requirements-ai.txt` and join when a
plant has the depth to justify a second index.

---

## 9. §16.5's open decisions — the defaults we shipped

| Decision | Default shipped | Where |
|---|---|---|
| IoT device per machine class | Deferred; data-source seam built now | `infra/emqx/` |
| Action trigger thresholds | >₹2,500 or >30 min, 3× in 7 shifts | `plants` columns, per plant |
| Severity → PDCA/RCA routing | Above threshold, OR safety/quality flag, OR 3+ recurrence | `kernel/rules.py` |
| Basic vs Standard costing at go-live | Basic; upgrade is additive, never a migration | `plants.costing_mode` |
| Tablets per plant | Device follows the person, not the machine | §14.5, no code impact |
| Cross-verification tolerance | Flag above `max(5 parts, 2%)` | `plants` columns, per plant |
| Night-shift business date | Belongs to the shift's **start** date | `plants.business_date_from_shift_start` |
| `plant_fees` ownership | Internal field, owner-visible; feeds running ROI | `plants.monthly_fee` |

---

## 10. What is deliberately not built

§9's NOT-building list is as binding as the feature list, and was respected:

- No hardware, firmware or device code — only the data contract for it
- No benchmarking or multi-plant dashboards (needs 40–50 plants)
- No ERP/Tally/SAP integration, no Activity-Based Costing — launch takes file uploads
- No material-batch / tool / supplier traceability (Phase 2, needs Stores discipline)
- No Tamil voice logging (deferred until the typed flows are proven)
- No consultant portal — but every record is plant-scoped from day one, so a
  100-plant rollup is a screen later, not a migration

The three Layer-5 plugins (Compliance/Audit, Kaizen, Skill Matrix) have their
data model in place — they feed the same Actions engine and the same rupee
ledger — but no UI. They are a parallel work stream, not a later stage.
