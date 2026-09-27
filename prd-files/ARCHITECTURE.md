# FactoryManager AI — Technical Architecture

**Version** 2.0 · **Date** 2026-09-10 · **Status** For technical review
**Source of truth** `FactoryManager_AI_Product_Specification_v5 - 08.09.2026.pdf` (42pp, Sections 1–16)
**Supersedes** `TECH-STACK-ANALYSIS.md` v1.0 — see [Appendix B](#appendix-b--what-changed-from-v10-and-why) for the diff and rationale.

This document covers what the product spec deliberately leaves out: data model, calculation architecture,
offline/sync protocol, tenancy, AI grounding, and the technology choices with their reasoning. It is written
to be built against. Where the spec is ambiguous or self-contradicting, this document says so explicitly
rather than silently picking a side — see [Part 12](#part-12--open-decisions).

---

## Part 0 — Five commitments that shape everything else

Every other decision in this document falls out of these five. If one is overturned, re-read the document.

### C1. The calculation kernel is one pure module, and it runs in the browser too

The spec's strongest engineering instruction (§8.1): *"every formula must be implemented once, in one place,
and every screen/report/Copilot answer must draw from that single implementation. Two screens showing two
different OEE numbers for the same shift is a credibility-ending bug."*

There is a second, harder constraint that forces the same conclusion: **the live cost ticker on a stopped
machine must run offline.** A tool snaps, the tablet has no Wi-Fi, and the rupee counter must still climb
(§3 Layer 1, §7 Screen 2). So the cost engine cannot be a server-side service — it must execute on the device.

Therefore: `@fm/kernel` is a **pure TypeScript package with zero I/O** — no database handle, no clock, no
network, no config lookup. Inputs in, numbers out. It is imported unchanged by the API server, the background
job runner, and the PWA. There is exactly one implementation of every formula in §8, and it is the same
compiled code in all three places.

This single constraint eliminates a polyglot backend. A Python cost service alongside a Node API means either
two implementations of the OEE formula or a network round-trip per ticker frame — and it cannot run offline
at all. **The whole system is TypeScript.**

### C2. Money is integer paise. Never a float, never a JS `number` in a ledger.

`Recovered Profit` is an accounting ledger the owner uses to decide whether to renew (§5.6, §8.1). Floating
point drift in a renewal argument is unacceptable. All monetary values are `bigint` paise in application code
and `BIGINT` in Postgres. Formatting to `₹` happens only at the presentation edge. Rounding is stated per
formula and applied once, at the end of each event's calculation, half-up.

### C3. Every event carries a frozen cost snapshot. History never restates.

If the owner corrects a machine rate in week 6 and yesterday's ₹9,380 silently becomes ₹11,200, trust dies —
and every `Recovered Profit` baseline shifts underneath its own Action.

So: at the moment an event is costed, the kernel's *resolved inputs* (MCR, LCR, OHR, CT, CM, BF, formula
version, costing mode) are written onto the event row alongside the outputs. A locked shift is arithmetically
frozen. Rate changes apply forward from their `effective_from` date only. Recomputation of a locked period is
an explicit, audited, owner-approved operation — never a side effect of editing a master record.

### C4. The job order spans shifts, days and machines. It is not shift-scoped.

§14.3 is explicit: *"300 pcs Monday on CNC-3, 200 pcs Tuesday on CNC-5 — under one job order, one lot lineage."*
Attaching a job order to a shift session makes split jobs, the Change Job event, and multi-day throughput time
unrepresentable. The shift-scoped entity is a **job session** (this station ran this job from t1 to t2).

### C5. The AI never computes a number. It only phrases numbers the kernel produced.

The Coach and Copilot are grounded generators, not calculators. Facts are assembled by deterministic SQL and
the kernel; the LLM receives a structured, pre-computed fact bundle and turns it into Tamil/English prose. An
LLM that arithmetically hallucinates a rupee figure into an owner's WhatsApp report destroys the credibility of
every number in the product. Tier gating (§14.6, Tiers 0–3) is enforced **server-side by data-sufficiency
checks before the model is called** — never by an instruction in a prompt.

---

## Part 1 — Stack

### 1.1 Chosen stack

| Layer | Choice | Why |
|---|---|---|
| Language | **TypeScript, everywhere** | C1. One kernel, shared server + browser. One hiring profile. |
| Client | **React 18 PWA** + Vite | Installable, no app store, BYOD Android (§14.5). Runs on the supervisor's own phone. |
| Offline store | **IndexedDB via Dexie** + outbox table | Structured queries, mature, no WASM/OPFS risk on cheap Android tablets. |
| Server | **Node 22 + Fastify**, modular monolith | One deployable. Microservices at 5 pilot plants is operational cost with no benefit. |
| Database | **PostgreSQL 16** | ACID audit trail, `RLS` for tenancy, `tstzrange` + `GiST` for downtime overlap constraints, JSONB, FTS, `pgvector` when needed. |
| Queue / scheduler | **BullMQ on Redis 7** | Durable *delayed* jobs — the 5/15/30-min escalation ladder cannot be `setTimeout`. |
| LLM | **Claude (`claude-sonnet-5`)**, `claude-haiku-4-5-20251001` for high-volume classification | Strong instruction-following for grounded generation; good Tamil. Provider is behind an interface (`LlmProvider`) — the grounding architecture matters more than the vendor. |
| WhatsApp | **WATI** (+ SMS fallback) | §16.4. Regulatory reality in India; 3–5 week approval. |
| Files | **S3 (ap-south-1)** or MinIO | Evidence photos, master uploads. |
| Hosting | **Single AWS ap-south-1 region**, ECS Fargate or a plain EC2 + Docker Compose | India data residency (§7.4 of v1.0). Kubernetes is deferred until it is a problem. |
| CI | GitHub Actions | Test + typecheck + migration check on every PR. |

### 1.2 Deliberately NOT in the launch stack

The spec's discipline — *"the NOT-building list is as binding as the feature list"* (§9) — applies to
infrastructure too. Each of these was in v1.0 and is removed:

- **Kubernetes** → ECS/Compose. Five pilot plants generate a few thousand events/day. Total.
- **Python services** → violates C1 outright.
- **Elasticsearch** → Postgres FTS is sufficient far past 100 plants for Factory Memory.
- **Dedicated vector DB (Weaviate/Pinecone)** → `pgvector` extension when semantic recall is actually needed
  (Memory is worthless before ~6 months anyway, §3 Layer 4). One database, not two.
- **Drools / a rules engine** → the rule set is ~15 conditions from §5.2 and §14.6. It is a typed,
  unit-tested TypeScript module in the kernel. A rules engine here is a dependency, an operational burden, and
  a second place for business logic to live.
- **LangChain** → the LLM call is a single structured prompt with a pre-built fact bundle (C5). An agent
  framework adds indirection over a call we want to keep boringly auditable.
- **Firebase Auth** → contradicts the India-residency commitment. Self-hosted JWT + Argon2id.
- **Redux Toolkit** → TanStack Query + Zustand over the local Dexie store. In an offline-first app, IndexedDB
  *is* the state store; a second normalized cache in memory is a synchronisation bug generator.

---

## Part 2 — The calculation kernel (`@fm/kernel`)

The most important package in the repository. Pure functions, no I/O, 100% unit-test coverage required,
every function exercised against real pilot-plant data (§16.1).

### 2.1 Shape

```ts
// Zero imports from db, http, fs, or Date.now(). Everything is passed in.
export const FORMULA_VERSION = 1;

export type Paise = bigint;

/** Resolved inputs — snapshotted onto every costed row (C3). */
export interface CostBasis {
  formulaVersion: number;
  costingMode: 'basic' | 'standard';
  machineRatePaisePerHour: Paise;   // MCR
  labourRatePaisePerHour: Paise;    // LCR
  overheadRatePaisePerHour: Paise;  // OHR
  cycleTimeMilliseconds: number;    // CT — integer ms, not float minutes
  contributionPaisePerPart: Paise;  // CM (resolved from ₹ or L/M/H band)
  bottleneckFactorBp: number;       // BF in basis points: 10000 = 1.0
  rateSourceIds: { machine: string; labour: string; overhead: string; contribution: string };
}

export interface DowntimeCost {
  machinePaise: Paise; labourPaise: Paise; overheadPaise: Paise;
  lostContributionPaise: Paise; totalPaise: Paise;
  partsNotMade: number;
}

export function costDowntimeEvent(durationMs: number, basis: CostBasis): DowntimeCost;
```

**BF as basis points, CT as integer milliseconds, money as paise** — the kernel contains no floating-point
arithmetic at all in the money path. `BF = 0.35` becomes `bottleneckFactorBp = 3500`.

### 2.2 The formulas (§8.1, §8.2), with the ambiguities resolved

| Quantity | Implementation | Resolution applied |
|---|---|---|
| Downtime event cost | `(D/60)·(MCR+LCR+OHR) + ⌊D/CT⌋·CM·BF` | Time terms computed in paise·ms then divided once. `⌊D/CT⌋` is integer floor — spec is explicit (`⌊ ⌋ = round down`). |
| Rounding | Half-up to whole paise, **once**, per component | Components must sum exactly to the total. Round each component, then total = sum of rounded components (not a separately-rounded total). |
| Scrap cost | `N · (material + Σ processing + CM·BF)` | Spec states it per-part; multiplied by scrapped quantity. `processing_op = (CT_op/60)·(MCR+LCR+OHR)` summed over **completed** ops only. |
| Rework cost | `(rework_min/60)·(MCR+LCR+OHR)` of the *rework station* | Enforced by constraint: a part in a rework record cannot appear in a scrap record for the same quality record (§8.1). |
| Availability | `(planned − unplanned_downtime) / planned` | **See 2.3 — requires a reason-code attribute the spec omits.** |
| Performance | `(CT · total_count) / actual_run_time` | Capped at `1.0`; values `>1.0` are stored raw, surfaced capped, and raise a `PERFORMANCE_OVER_100` data-quality flag (bad CT or padded count). |
| Quality | `good / total` | Rework-then-good counts as good here, but **not** in FPY. |
| OEE | `A · P · Q` | Computed per machine/line/shift/day from the same three functions. Never stored denormalised without the basis. |
| FPY | `good_first_time / started` | Reworked-then-good excluded (§8.1). |
| MTBF | `total_run_time / breakdowns` | `run_time` = planned time − all downtime. Breakdown = events whose reason code has `is_breakdown = true` (a code attribute, not a string match on `'Machine Breakdown'`). |
| MTTR | `Σ breakdown_duration / breakdowns` | Same code attribute. |
| Throughput time | `last_op_completion − first_op_start` per lot | From routing timestamps; excludes nothing (wall-clock, as the plant experiences it). |
| Recoverable loss | `Σ cost WHERE recoverability = 'R'` | Structural reported in a separate field, **never summed into the same total** (§8.1). |

### 2.3 A gap in the spec: `Recoverable/Structural` ≠ `Planned/Unplanned`

T6 (§15) gives each downtime reason code one tag: **R** or **S**. Two different formulas consume it, and they
need different answers:

- **The money report** needs R/S: *can the plant get this back?*
- **Availability** (§8.2) needs planned/unplanned: `planned_time = shift − planned downtime (breaks, planned
  maintenance, no-order)` and `A = (planned − unplanned) / planned`.

These are not the same axis. **Power Cut** is tagged Structural (the plant can't recover it) but it is
absolutely *unplanned* — it must reduce Availability. If the build team maps `S → planned`, every power cut
silently improves the plant's OEE, and a plant manager will notice.

**Resolution:** reason codes carry two independent attributes.

```
recoverability: 'R' | 'S'        -- drives the money report and Action triggers
oee_treatment:  'planned' | 'unplanned'  -- drives Availability
is_breakdown:   boolean          -- drives MTBF/MTTR
```

Defaults for the seeded T6 library: `Planned Maintenance (S, planned)`, `Meeting/Training (S, planned)`,
`Power Cut (S, unplanned)`, `No Order (S, planned)`, `Tool Breakage (R, unplanned, breakdown=false)`,
`Machine Breakdown (R, unplanned, breakdown=true)`, `Material Wait (R, unplanned)`,
`Setup/Changeover (R, unplanned)`. **This adds one column to T6.**

### 2.4 Acceptance tests are golden files from a real plant

`@fm/kernel` ships with fixture files derived from the pilot plant's week of paper logs (§16.1). Every formula
change re-runs them. A diff in any golden output is a release blocker requiring explicit sign-off — this is the
mechanism that keeps two screens from ever disagreeing.

---

## Part 3 — Data model

Full DDL in `db/migrations/`. This section covers the structural decisions and the traps.

### 3.1 The job-order spine (C4)

```
plants
 └─ job_orders            (id, plant_id, job_number, part_id, lot_id, qty_planned,
                           status, opened_at, closed_at)          -- spans days/shifts/machines
     └─ job_operations    (job_order_id, seq, operation_code, station_family,
                           qty_completed, qty_rejected, first_started_at, last_completed_at)
         └─ job_sessions  (job_operation_id, station_id, shift_session_id, operator_id,
                           started_at, ended_at, end_reason)      -- THE shift-scoped entity
             ├─ production_counts
             ├─ downtime_events
             └─ quality_records
```

Every capture row references a `job_session`, and inherits part / customer / lot / operator / shift / machine
through it. **No capture screen ever asks a human to type a part number, customer or lot** (§3, §14.3) —
this is enforced structurally: those columns do not exist on the capture tables.

`throughput_time` per lot = `MAX(job_operations.last_completed_at) − MIN(job_operations.first_started_at)`.

### 3.2 The Change Job event (§14.3)

`Change Job` closes the open `job_session` on that station with `end_reason = 'job_change'` and opens a new
one. The gap between the two sessions is **materialised as a `Setup/Changeover` downtime event automatically**
— that is how changeover loss gets captured for free. Consequence: `job_sessions` on a single station must
never overlap, enforced in the database, not in application code:

```sql
ALTER TABLE job_sessions ADD CONSTRAINT job_sessions_no_overlap
  EXCLUDE USING gist (station_id WITH =, tstzrange(started_at, ended_at) WITH &&);
```

The same exclusion constraint pattern guards `downtime_events` per station. This is the invariant that makes
offline replay from two devices safe to merge (Part 4) — the database refuses a physically impossible timeline.

### 3.3 Time, shifts, and the midnight problem

Entirely absent from v1.0 and a reliable source of production bugs.

- All timestamps are `TIMESTAMPTZ`, stored UTC, rendered `Asia/Kolkata`.
- `shift_sessions` carries **`business_date`**, distinct from wall-clock date. A night shift running
  22:00 → 06:00 belongs to a single `business_date` (default: its start date; configurable per plant).
- "Yesterday's losses" on the morning meeting screen means `business_date = today − 1`, never
  `started_at::date`.
- Every daily/weekly aggregate — including the Recovered Profit weekly buckets (Part 7) — groups by
  `business_date`. Week boundary is plant-configurable, default Monday, with the weekly-off from T7.

### 3.4 Cost parameters, versioned and resolvable (C3)

```
cost_families      (plant_id, family_code)                       -- §14.4: 100 machines → 6–8 numbers
cost_rates         (plant_id, scope_type, scope_id, rate_type, paise_per_hour,
                    effective_from, effective_to, confidence, source)
part_contributions (plant_id, part_id | part_family, mode, paise_per_part | band_values, effective_from)
```

`scope_type ∈ {plant, section, cost_family, station}`. Resolution is **most-specific-wins**:
`station override → cost_family → section → plant default`. This is exactly the §14.4 ladder and the
Basic→Standard upgrade path (§4.2) — **a plant upgrades by adding narrower-scope rows, never by migrating**.

`confidence ∈ {default_library, derived, measured}` carries the ±30% / ±15–20% / ±5–10% rungs of §14.4 through
to the UI, so an owner can see which numbers are estimates. The seeded machine-class rate library (§14.4
Rung 1, §16.4) lives in `reference.machine_class_rates` — plant-independent, versioned, and one of the
proprietary assets the spec identifies.

### 3.5 Capture rows are append-only facts

```
downtime_events (
  id UUID PRIMARY KEY,              -- UUIDv7, generated ON THE DEVICE (Part 4)
  plant_id, job_session_id, station_id, reason_code_id,
  started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ,
  device_started_at, device_ended_at,   -- as the tablet saw it
  clock_skew_ms INT,                    -- measured at sync handshake
  source event_source NOT NULL,         -- 'manual' | 'sensor' | 'erp'   <- the hardware seam
  cost_basis JSONB,                     -- frozen CostBasis (C3)
  machine_paise BIGINT, labour_paise BIGINT, overhead_paise BIGINT,
  lost_contribution_paise BIGINT, total_paise BIGINT,
  locked_at TIMESTAMPTZ                 -- non-null ⇒ immutable
)
```

Corrections before shift close are **new rows superseding old ones** (`supersedes_id`), not `UPDATE`s. After
`locked_at`, a `BEFORE UPDATE OR DELETE` trigger raises an exception on the table — the shift lock is enforced
by the database, not by a service-layer `if` (§3, §6, §12 risk #1). The owner-override path writes a new
`corrections` row referencing the locked original, with the override reason and approver; the original is never
touched. This is what makes the audit trail defensible to an IATF auditor later.

### 3.6 The data-source seam (§2, §9, §16.3)

`event_source` on every capture row, plus a single ingestion port:

```ts
interface CaptureIngestPort {
  submit(batch: CaptureEnvelope[], ctx: { plantId: string; source: EventSource; deviceId: string }): Promise<IngestResult>;
}
```

The PWA sync endpoint is one adapter over this port. A future sensor gateway is a second adapter over the
*same* port, with the same validation and the same exclusion constraints. Nothing above the port knows or
cares. This is the whole of the hardware-readiness requirement, and it costs one interface now instead of a
rebuild later.

### 3.7 Multi-tenancy: Postgres Row-Level Security, not a `WHERE` convention

v1.0 proposed *"every query includes plant_id"*. Conventions leak; one forgotten join in a Copilot query
shows Plant A's costs to Plant B, and the spec sells on *"your data is yours, siloed per plant"* (§11).

```sql
ALTER TABLE downtime_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY plant_isolation ON downtime_events
  USING (plant_id = current_setting('app.plant_id')::uuid);
```

The request handler sets `app.plant_id` from the verified JWT inside the transaction. The application role has
no `BYPASSRLS`. Cross-plant leakage becomes structurally impossible rather than review-dependent, and the
future 100-plant rollup (§9) is a role with an explicit aggregate policy — a screen, not a migration.

---

## Part 4 — Offline-first capture and the sync protocol

The highest-risk subsystem in the build (§12 risk #7). It is specified here in full because "we'll figure out
conflict resolution later" is how this schedule slips. §16.4 requires these rules to be settled **before the
first pilot**.

### 4.1 Why this is tractable here

The general offline-sync problem is hard. This one is not, because of the physical situation:

- One supervisor owns one zone and one tablet. Two people rarely touch the same machine's timeline at once.
- Capture rows are **immutable facts**, not mutable documents. Facts append; they don't merge.
- IDs are generated on the device, so replay is naturally idempotent.

We are therefore **not** building a general CRDT layer. We are building an ordered, idempotent outbox.

### 4.2 The protocol

**Identity.** The device generates a **UUIDv7** for every row it creates. UUIDv7 is time-ordered, so the
server can sort a replayed batch by ID and get device-authored order for free. The ID is the idempotency key:
re-delivering a batch after a flaky connection is a no-op (`ON CONFLICT (id) DO NOTHING`).

**Outbox.** Every local write appends to a Dexie `outbox` table `{ seq, id, op, payload, deviceTs }` in the
same IndexedDB transaction as the domain write. Nothing is ever "written to the server" from the UI — the UI
writes locally, and the sync worker drains the outbox. This is what makes the app feel instant and what makes
it survive a killed browser tab.

**Push.** Batched POST, ordered by `seq`, at-least-once. Server applies each envelope inside one transaction
per batch, returns per-envelope `{accepted | duplicate | rejected(reason)}`. The device only removes an
envelope from the outbox on a definitive server verdict.

**Pull.** Watermark-based: the device sends its last-seen `server_seq` and receives everything after it for
its plant, scoped to its zone. Masters (T1–T7 data, reason codes, rates) are pulled on session start and
cached with an ETag.

**Clock.** The device clock is untrusted — cheap Android tablets drift, and a supervisor can change the clock.
At each sync handshake the server returns its time; the device stores `clock_skew_ms`. Durations are computed
from **device timestamps** (monotonic *within* a device, which is what matters for a start/stop pair) but every
row stores both device and server-corrected timestamps plus the skew. Skew beyond a threshold (default 5 min)
raises a data-quality flag on the shift — visible to the owner, not blocking to the supervisor.

### 4.3 The three conflicts that actually happen, and their rules

| Conflict | Rule |
|---|---|
| **Two devices open downtime on the same station** (supervisor + relief supervisor) | The exclusion constraint (§3.2) rejects the second. Rejected envelope returns to the device as a **merge prompt**: "CNC-3 was already logged as stopped at 10:14 by Murugan — is this the same stop?" Never silently discarded; never silently duplicated. |
| **A downtime close arrives before its open** (out-of-order delivery) | Server holds the close in a `pending_correlation` table for 24h and applies it when the open lands. UUIDv7 ordering makes this rare; the buffer makes it safe. |
| **An entry arrives for an already-locked shift** (tablet offline across shift close) | Accepted into `late_arrivals`, **never** merged into locked figures. Surfaces to the owner as "3 entries arrived after Shift A closed — review". This preserves C3 and the tamper-proof claim simultaneously. It also means shift closure must warn if a known device has not synced. |

**Shift closure requires a successful sync.** The Closure Lock (§3) is meaningless if half the shift's events
are sitting in an outbox. The closure screen blocks on: outbox empty, all downtime events have reasons, all
production entries present, all escalations resolved — with the owner-override path logged (§7, Screen 6).

### 4.4 Shared-tablet identity

§14.1: operators get **no logins**. But §2 requires the inspector's quality entry to be *independently*
attributable from the supervisor's count, or cross-verification is theatre.

Resolution: the **device** is registered and holds a long-lived credential; **people** unlock with a 4-digit
PIN for a fast role switch (< 3 seconds, thick fingers, poor light). Every row records `entered_by`. The
supervisor and inspector cannot be the same identity on the same batch — a constraint the cross-verification
job checks and flags.

---

## Part 5 — Escalation engine (§3 Layer 2, §9 Stage 2)

The 5 / 15 / 30-minute ladder is a **durable scheduler**, not `setTimeout`. A server restart must not silence
a plant's escalations.

- On downtime open, enqueue three BullMQ **delayed jobs** with deterministic job IDs
  (`esc:{eventId}:5`, `:15`, `:30`). Deterministic IDs make offline replay idempotent — re-ingesting the same
  event cannot double-escalate.
- On downtime close, remove the remaining jobs by ID.
- Recipient is resolved **from the reason code**, not hardcoded: `reason_codes.escalation_route ∈
  {maintenance, stores, quality, none}` → T5 alert recipients at that level. This is a T6 column the templates
  imply but do not name; it is required for §3 Layer 2 to work as described.
- Every send writes an `escalations` row with the resolved recipient, channel, provider message ID, and
  delivery status. *"Nobody can later say they didn't know"* (§3) is a data requirement, not a slogan.
- **Channel fallback:** WATI → SMS on failure or while WATI approval is pending (§12 risk #6, §16.4). The
  `NotificationPort` interface is written first and WATI is one adapter, so the SMS-only interim period is a
  config flag rather than a code path that gets thrown away.
- Escalations fire from **server-received** events. A tablet offline for 20 minutes will escalate late; the
  escalation row records both timestamps so the delay is visible rather than hidden.

---

## Part 6 — Actions, PDCA, and severity routing (§5)

### 6.1 Triggers (§5.2) — evaluated at shift close, not continuously

```ts
interface ActionTriggerConfig {   // per-plant, tuned in onboarding (§16.5)
  singleEventPaise: bigint;       // default ₹2,500 → 250000n
  singleEventMinutes: number;     // default 30
  recurrenceCount: number;        // default 3
  recurrenceWindowShifts: number; // default 7
}
```

**Deduplication is the load-bearing rule** (§12 risk #10 — action fatigue kills the product). A recurring
pattern produces **one** Action covering the pattern, and events that roll into an existing open pattern Action
never spawn their own. Implementation: an `action_event_links` join table plus a uniqueness guard on
`(plant_id, station_id, reason_code_id, status IN ('open','in_progress','monitoring'))` for pattern-type
Actions. Before creating, always check for an open Action on the same station+reason and attach instead.

### 6.2 Severity routing (§5.3)

The morning meeting shows everything but routes only qualifying issues into PDCA/RCA:

```
route_to_pdca = cost > threshold
             OR duration > threshold
             OR reason_code.safety_flag
             OR quality_escape_flag
             OR recurrence >= N in rolling window
```

`safety_flag` and `quality_escape_flag` are code attributes — a further T6 column. The routing decision, its
inputs, and the config version are **persisted per issue**, so a plant can later audit why something was or
wasn't escalated.

The Today's Plan gate: the meeting endpoint returns `plan_unlocked: false` while any escalated Action from the
prior business date is neither `done` nor `delayed_with_reason`. Gate state is server-computed; the client
cannot skip it.

### 6.3 Recovered Profit (§5.6, §8.1) — the north star, computed conservatively

This is the renewal argument, so its arithmetic must be defensible line by line.

```
baseline_weekly_paise  := AVG(weekly loss for (station, reason_code))
                          over the 4 complete weeks before action.created_at
                          -- FROZEN onto the action row at creation. Never recomputed.

for each complete week W after action.closed_at:
    delta(W) := MAX(0, baseline_weekly_paise − actual_weekly_paise(W))

recovered(action) := Σ delta(W)   -- credited only once ≥2 consecutive weeks
                                  -- have sustained delta(W) > 0
```

Rules the implementation must enforce, each traceable to §5.6 / §12 risk #11:

1. **Baseline frozen at creation** — a copied value on the Action row, not a live query.
2. **Never negative** — `MAX(0, …)` per week, not on the sum.
3. **2 consecutive sustained weeks** before any rupee is credited; the first two weeks are credited
   retroactively once the condition is met.
4. **Reopen stops accrual** from the reopen date; already-credited weeks stand. Re-verification restarts the
   2-week sustain clock.
5. **No double-counting** — an `(station, reason_code, week)` tuple can be claimed by at most one Action.
   Enforced by unique constraint on the `recovered_profit_weeks` ledger table.
6. **Insufficient baseline** — if fewer than 4 complete weeks of history exist at creation, the Action is
   flagged `baseline_insufficient` and accrues **nothing**. Early Actions on a new plant earn no Recovered
   Profit. This is uncomfortable in month 1 and correct forever after.
7. Recovered Profit is a **ledger table**, not a computed column: one row per (action, week) with the baseline,
   actual, delta and the basis. The owner's ROI number must be explainable down to a week.

`running_roi = cumulative_recovered_paise / cumulative_fees_paise` — requires a `plant_fees` table
(subscription charges by period). Absent from v1.0 and from the spec's data list, but §5.6 and §8.1 put this
number on the owner's home screen, so it has to be stored somewhere.

**Annualised projection** = `current 4-week recoverable run-rate × 13`, and the API returns it in a field
named `projection_*` with a mandatory label — §8.1: *"always labelled as projection, never as fact."*

---

## Part 7 — The AI layer

### 7.1 Grounding architecture (C5)

```
Question / trigger
   │
   ├─► Intent classification            (claude-haiku-4-5-20251001, cheap, or rules)
   │
   ├─► Tier gate                        ← server-side data-sufficiency check, NOT a prompt
   │      Tier 0 (< 10 shifts)  → silent / "not enough data yet"
   │      Tier 1 (≥ 10 shifts)  → rule-based facts only
   │      Tier 2 (4–6 weeks)    → pattern facts unlocked
   │      Tier 3 (12+ weeks)    → correlation facts unlocked
   │
   ├─► Fact assembly                    ← deterministic SQL + @fm/kernel. ALL numbers born here.
   │      { losses: [...], patterns: [...], openActions: [...], memory: [...] }
   │
   ├─► Generation                       ← claude-sonnet-5 receives ONLY the fact bundle
   │      "Phrase these facts. Never compute. Never state a number not in the bundle."
   │
   └─► Numeric validation               ← post-check: every ₹/%/count in the output must
          appear in the fact bundle, else regenerate once, then fall back to a template.
```

That last step is not optional. It is the difference between a Copilot the owner trusts and one wrong number
that, per §11, discredits every number in the product. Templated fallback output is always acceptable; a
hallucinated rupee figure never is.

### 7.2 Coach and Copilot are one pipeline

The AI Shift Coach (05:45 daily) and the Copilot (on demand) differ only in trigger and fact-selection
strategy — same gate, same assembly, same validation. Build one pipeline with two entry points.

Tier 1 Coach output is **fully templated** (no LLM in the loop): biggest loss yesterday, same reason 3× in 5
shifts with no Action, overdue Actions. §9 explicitly ships the rule-based Coach in Stage 2 and the AI version
in Stage 3 — the pipeline shape is identical, so Stage 3 swaps the generation step only.

Every briefing states its own tier and data basis (§14.6: *"the Coach states its own confidence and data basis
in every briefing"*) — a rendered field, not model discretion.

### 7.3 Factory Memory retrieval

Postgres FTS over the four closure answers (§3 Layer 4 / §5.3) plus structured filters
(`problem_category`, `machine_type`, `part_family`) is sufficient for launch. Add `pgvector` embeddings when a
plant crosses ~200 captured fixes and lexical search starts missing paraphrases. Memory rows are written by
the Action closure prompt and by nothing else — one writer, one shape.

### 7.4 Cost control

Per-plant monthly token budget with a hard stop and alerting. Coach briefings are generated once per plant per
day and cached. Copilot answers to identical (question, plant, business_date) tuples are cached for the day.
Without this, a plant with a chatty owner is an unbounded bill.

---

## Part 8 — Ingestion: templates T1–T7 (§15)

`@fm/ingest` — strict-parse first, tolerant parsing later (§9, explicitly not a blocker).

- One Zod schema per template. Exact column headers, clear per-cell errors
  (`Row 47, 'Cycle time': expected a number, got '2.4 min'`).
- **Dry-run then commit.** Upload → parse → validate → return the §4.1 confirm summary
  (*"Found 132 parts, 19 machines, 41 operators, 3 shifts"*) → operator confirms → transactional apply.
  Never write on upload.
- **Go-live minimum is T1 + T2** (§14.1). The importer must succeed with T3–T7 absent, falling back to the
  seeded default libraries: machine-class rates (§14.4 Rung 1), the 18 default reason codes and the default
  defect codes (T6 ships pre-filled).
- Every imported value stores `source` and `confidence` (§3.4) so estimated numbers stay visibly estimated.
- **T4 is a formula-input map, not free-form.** §14.1: *"Template T4 maps each input cell to the formula that
  consumes it, so every number on every screen traces back to one template cell and one formula."* Implement
  this literally: each T4 cell has a stable `input_key` that the kernel's `CostBasis` resolution references, and
  the UI can render "this ₹420/hr came from T4 cell `cost_family.CNC-family.machine_rate`."

Three template additions this architecture requires, all justified above:
`T6.oee_treatment`, `T6.escalation_route`, `T6.safety_flag` (and `is_breakdown`).

---

## Part 9 — Repository layout

```
packages/
  kernel/         @fm/kernel      pure formulas, zero I/O, golden-file tests     ← C1
  contracts/      @fm/contracts   Zod schemas + TS types shared client/server
  ingest/         @fm/ingest      T1–T7 parsers
apps/
  api/            Fastify modular monolith
    modules/{capture,costing,actions,escalation,ai,ingest,reports,identity}
  pwa/            React PWA — capture screens 1–6, offline-first
  web/            Owner money screen, actions board, morning-meeting mode (screens 7–10)
  worker/         BullMQ: escalations, shift-close pipeline, coach, recovered-profit weekly job
db/migrations/    SQL, forward-only, RLS policies
reference/        seeded libraries: machine-class rates, T6 defaults, readiness questionnaire
```

`apps/api/modules/*` are internal modules with explicit boundaries — **not** microservices. Each owns its
tables and exposes a typed interface. If a module ever needs its own deployment, the seam already exists.

---

## Part 10 — Build sequence

Follows §9 exactly. Ordered so each step is independently demonstrable.

**Stage 1 — capture + cost (the sellable minimum)**

1. `@fm/kernel` + golden tests from the pilot plant's paper week. *Nothing else starts until the ₹9,380 in the
   spec's own example reproduces exactly — see [Open Decision 1](#part-12--open-decisions).*
2. Migrations: tenancy + RLS, masters, job-order spine, capture tables, lock triggers, exclusion constraints.
3. `@fm/ingest` T1 + T2 with the confirm screen; seeded default libraries.
4. Capture ingest port + sync endpoint (Part 4).
5. PWA screens 1–6 offline-first: shift start, machine grid + live ticker, downtime log, production entry,
   quality entry, shift closure lock.
6. Shift-close pipeline: costing, recoverable/structural split, ranked losses.
7. Owner end-of-shift WhatsApp report (WATI or SMS fallback).
8. Digital Readiness Questionnaire — a standalone scored form, cheap, disproportionate sales value (§9).

**Stage 2 — discipline and Actions**

9. Actions engine: triggers, dedup, lifecycle, RCA assignment + 5-Why template, four-question closure prompt.
10. Recovered Profit ledger + weekly sustain job + `plant_fees`.
11. Owner money screen, Actions Board, Morning Meeting Mode with severity routing and the plan gate.
12. Escalation ladder (durable, Part 5). Cross-verification reconciliation alerts.
13. Rule-based Coach (Tier 1 templates).

**Stage 3 — Copilot expansion:** pattern tier, LLM-generated Coach (Tier 2/3), Tamil.

**Parallel:** the three plugins, on the same Core data, Actions engine and Recovered-Profit ledger (§3 Layer 5).

**Immediately, in parallel with step 1:** apply for WATI (§16.4 — 3–5 weeks, blocks Stage 2).

---

## Part 11 — Non-functional targets

| Concern | Target | Note |
|---|---|---|
| Local capture write | < 100 ms, always offline-capable | IndexedDB write, no network in path |
| Live cost ticker | 1 Hz, on-device | Kernel in the browser (C1) |
| Sync push round trip | < 2 s on 3G; unlimited retry | Never blocks the UI |
| Escalation send | < 30 s from server receipt | Durable queue |
| Shift-close pipeline | < 60 s | Owner report follows immediately |
| Uptime | 99.5% at pilot | Offline-first means an API outage does not stop a shift |
| Backups | PITR, 30-day | Audit trail integrity |
| Data residency | ap-south-1 only | §7.4 of v1.0; contractual (§11) |

`entered_by` is captured on every row for audit, but **the 90-day no-blame rule (§10) is a product constraint
with a data consequence**: no default view, report, Coach briefing or Copilot answer groups losses by person.
Person-level aggregation is not a hidden filter — it is a query that does not exist in the API surface at
launch.

---

## Part 12 — Open decisions

Decisions the build cannot make alone. The first is blocking; the rest have safe defaults.

### 🔴 1. BLOCKING — the spec's worked cost example does not reconcile with its own formula

§3 Layer 2 states the CNC-3 tool breakage costs **₹3,290**, made up of ₹329 machine + ₹141 labour + ₹165
overhead + **₹2,655 lost contribution**, and describes that last figure as *"(47 ÷ 2.4 min cycle) = 19 parts ×
₹38 margin."*

**19 × ₹38 = ₹722, not ₹2,655.** Applying §8.1's formula with the spec's own inputs (D=47, MCR=420, LCR=180,
OHR=210, CT=2.4, CM=38, BF=1.0) gives **₹1,356**, not ₹3,290. For ₹2,655 to be right across 19 parts, CM would
have to be **₹139.74** — but ₹38 is stated independently in §4.1 (`₹110 − ₹72 = ₹38`) and again in the T2
example.

₹3,290 is not an isolated typo: it recurs in the ranked loss table, the AI Coach example, and the day-in-the-life
narrative, and it anchors the ₹9,380 shift total. This matters beyond cosmetics — it is the number in the sales
demo, and it is the acceptance test for the cost engine.

**Needed from the spec owner:** which is authoritative — the ₹38 margin, or the ₹2,655/₹3,290 figures? Until
answered, the kernel implements the §8.1 formula (the formula is the contract) and the golden fixture records
₹1,356 with this discrepancy annotated. **Do not "fix" the kernel to reproduce ₹3,290.**

### 2. Reason-code attributes (§2.3, Parts 5–6)

T6 needs `oee_treatment`, `escalation_route`, `safety_flag`, `is_breakdown` alongside `recoverability`.
Defaults proposed in §2.3. *Default if unresolved:* ship the proposed defaults, editable in onboarding.

### 3. Business-date rule for night shifts (§3.3)

Does a 22:00–06:00 shift belong to its start date or its end date? *Default:* start date, plant-configurable.
Affects every "yesterday" number the owner sees.

### 4. Cross-verification tolerance (§16.5)

§6 shows 3 parts in ~374 passing silently and 25 flagging. *Default:* flag at `> max(5 parts, 2%)`, tuned
against the first plant's real reconciliation data as §16.5 instructs.

### 5. Late arrivals after shift lock (§4.3)

Confirmed intent: never merge into locked figures, always surface to the owner. Needs product sign-off since
it means the locked number can be knowably incomplete — which is still better than a mutable one.

### 6. `plant_fees` ownership (§6.3)

Running ROI needs subscription amounts in the system. Who enters them, and are they visible to anyone below
the owner? *Default:* internal-only field, owner-visible.

### 7. Basic vs Standard costing at go-live (§16.5)

*Default:* Basic. The scope-resolution model (§3.4) makes the upgrade additive, never a migration — so this can
be deferred safely per plant.

---

## Appendix A — Traceability to the spec

| Spec section | Where it is implemented here |
|---|---|
| §2 hardware seam | C1 · Part 3.6 (`event_source`, `CaptureIngestPort`) |
| §3 job-order spine, routing, lots | C4 · Part 3.1 |
| §3 Layer 2 escalation | Part 5 |
| §4.1–4.2 onboarding, costing modes | Part 3.4 · Part 8 |
| §5 Actions, PDCA, severity routing | Part 6 |
| §5.6 Recovered Profit | Part 6.3 |
| §7 screens 1–10 | Part 9 (`apps/pwa`, `apps/web`) · Part 10 |
| §8 formula reference | C1 · Part 2 |
| §8.2 bottleneck factor | Part 2.1 (`bottleneckFactorBp`) · Part 3.4 |
| §9 build order + NOT-list | Part 1.2 · Part 10 |
| §10 no-blame rule | Part 11 |
| §12 risk register | Parts 4, 5, 6.1, 6.3, 3.5 |
| §14.3 Change Job | Part 3.2 |
| §14.4 cost families | Part 3.4 |
| §14.6 Coach tiers | Part 7.1 |
| §15 T1–T7 | Part 8 |
| §16.5 open decisions | Part 12 |

---

## Appendix B — What changed from v1.0, and why

`TECH-STACK-ANALYSIS.md` v1.0 is a sound structural map — its data-flow walkthrough and pattern catalogue are
still worth reading. These are the substantive changes.

| # | v1.0 | v2.0 | Why |
|---|---|---|---|
| 1 | Node API + **Python** cost engine | **One TypeScript kernel**, shared server + browser | v1.0 makes the live offline cost ticker impossible and creates two homes for the §8 formulas — the exact bug §8.1 forbids. |
| 2 | `job_orders.shift_session_id NOT NULL` | Job order spans shifts; `job_sessions` is shift-scoped | v1.0 cannot represent §14.3 split jobs or multi-day throughput time. Structural. |
| 3 | `DECIMAL(10,2)` money, JS numbers | `BIGINT` paise, `bigint` in code | Float drift in the renewal ledger. |
| 4 | Cost params versioned by `effective_date` only | **Frozen `cost_basis` snapshot per event** | v1.0 restates history when a rate is corrected, moving every Recovered Profit baseline. |
| 5 | *"Every query includes plant_id"* | **Postgres RLS** | A convention is not an isolation guarantee for a contractual claim. |
| 6 | R/S tag drives both money and Availability | **Separate `oee_treatment`** | Power cuts would silently inflate OEE. |
| 7 | No timezone/business-date model | `business_date` + Asia/Kolkata throughout | Night shifts crossing midnight corrupt every daily aggregate. |
| 8 | Sync "daemon", conflict rules TBD | Full outbox protocol, UUIDv7 idempotency, 3 named conflict rules, untrusted clocks | §16.4 requires these settled before pilot; it is the top build risk. |
| 9 | LLM answers questions | **Grounded pipeline + numeric validation + server-side tier gate** | An LLM must never author a rupee figure. |
| 10 | K8s, Elasticsearch, Weaviate, Drools, LangChain, Firebase | Removed | Operational cost with no pilot-scale benefit; Firebase also contradicts India residency. |
| 11 | Audit immutability by convention | DB triggers + `EXCLUDE` constraints + append-only supersede | The tamper-proof claim is the product. |
| 12 | Recovered Profit as columns on `actions` | **Weekly ledger table** + 7 enforcement rules | The ROI number must be explainable week by week, and un-double-countable. |
| 13 | 5-person team incl. DevOps + Python | 3–4 TypeScript engineers + a consultant | Falls out of #1 and #10. |
| 14 | Cost example accepted as given | **Flagged as blocking** (Open Decision 1) | Would have been discovered as a failing acceptance test in week 3. |

Two v1.0 SQL snippets are also non-executable as written and are replaced wholesale by `db/migrations/`:
`contribution_value DECIMAL(10,2) OR JSON`, and `CHECK (immutable = TRUE)` on a column that is never declared.
