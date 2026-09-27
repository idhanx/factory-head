# Specification coverage

A section-by-section audit of `FactoryManager_AI_Product_Specification_v5` against
what is built. Written so the gaps are as visible as the coverage.

Legend: ✅ built · 🟡 partial · ❌ not built · ⛔ deliberately out of scope (§9)

---

## Section 1 — Who uses it

§1's "Who uses it" table is a contract, not a cast list: it names six roles and
gives each one duties and a cadence. Audited row by row, and the two rows that
had nothing behind them are now built (`tests/test_role_desks.py`).

| Role | §1 duty | Cadence | Where it lives |
|---|---|---|---|
| **Plant Owner / MD** | Daily loss report, monthly trends, approves overrides | daily, 2 min | `/owner`, `/kpis/monthly`, override on shift close |
| **Shift Supervisor** | Starts the shift, hourly counts, downtime with reasons, closes the shift | through each shift | `/shift`, `/floor` |
| **Quality Inspector** | Good parts and rejections with defect codes | 2–4×/shift | `/quality` — its own login, so §2's cross-check means something |
| **Maintenance In-charge** | Receives escalation alerts · **logs what was done to fix breakdowns** | when events occur | `/maintenance` ✅ **new** |
| **Stores In-charge** | Receives material-shortage alerts · **confirms material staging** | when events occur | `/stores` ✅ **new** |
| **Onboarding consultant** | Cost parameters, machine list, reason codes; trains the team | once + reviews | `/onboarding`, `/settings` |

### What was missing, and why it mattered

Maintenance and Stores both landed on `/actions` — the PDCA backlog, which
answers a different question from the one either role opens an app to ask. The
three defects behind that:

1. **Escalations carried a name but not a route.** §3 Layer 2 says *"the system
   picks the recipient from the reason code"*, and the ladder did exactly that —
   then stored only the person. So `GET /escalations` handed the whole plant's
   ladder to everyone, and the routing decision was lost the day the recipient
   list changed. `escalations.route` now records it; the backfill separated the
   demo plant's existing rungs into 1,160 maintenance and 509 stores.
2. **Nowhere to log a repair.** `POST /downtime/{id}/close` took no note, so
   §1's second maintenance duty had no home and §3 Layer 4's *"what usually
   fixes this?"* had nothing per-event to draw on.
3. **Stores had no screen at all.** §6's 5:45 AM briefing asks Stores to confirm
   material is at the line before 8:00; there was no way to answer.

### Two rules these modules are built against

**The route decides whose stop it is — never `is_breakdown`.** §8.1 reserves
that flag for MTBF/MTTR (*"'Machine Breakdown' code only"*) and the default
library sets it on one of eighteen codes, while **six** route to maintenance.
Filtering the desk on it would have hidden five of the six — including the tool
breakage §6's own Priority 1 assigns to Suresh K. in Maintenance. Caught by a
test, not in a pilot.

**Neither desk adds a daily register.** §4.3's 15-minute budget is sacred and
§3 applies it to every surface, plugins included. The staging queue is today's
job orders; no row exists until somebody answers, so a plant that ignores the
screen is left exactly as it was. A repair note is testimony beside the record —
it never touches cost, duration, reason code or times, which is what keeps §2's
"the data locks permanently" true.

---

## Section 3 — The six layers

| Layer | Scope | Status |
|---|---|---|
| **1 Factory Core** | Shift start, machine grid, downtime, production, quality, closure lock | ✅ |
| **2 Factory Brain** | Cost engine, ranked losses, escalation ladder, Actions/PDCA, Recovered Profit, AI Coach | ✅ |
| **3 Factory Copilot** | Plain-language Q&A, tier-gated | ✅ web · ❌ WhatsApp (WATI pending) |
| **4 Factory Memory** | Four closure questions, searchable forever | ✅ |
| **5 Factory Plugins** | Compliance/Audit AI, Kaizen AI, Skill Matrix AI | ✅ built — see below |
| **6 Industry Benchmark** | Cross-plant comparison | ⛔ needs 40–50 live plants |

### Layer 5 — built

§9 is explicit that the three plugins are **a parallel work stream, not a later
stage**, all on the same Core data, Actions engine and Recovered-Profit ledger.
Each one adds no daily data entry — the rule §3 sets for every plugin.

| Plugin | What it does | Feeds |
|---|---|---|
| **Compliance / Audit AI** | Live readiness across ISO 9001, IATF 16949 and ZED, scored from Core data; every clause opens onto the exact records that scored it; cross-standard mapping; frozen evidence packs | — |
| **Kaizen AI** | The full Plugin Edition — see the section below | Actions, Recovered Profit, Factory Memory, Skill Matrix, Compliance |
| **Skill Matrix AI** | Operator × station matrix; training needs from 'No Operator' stops (measured ₹/yr) and single-person stations (₹ per idle shift, from the kernel) | Actions |

### Kaizen AI — Plugin Edition v2

Built against *Kaizen AI — Plugin Edition v2*, which is the Standalone Edition
with §2's deltas applied. What §2.1 **removed** matters as much as what was
added, and none of it may creep back:

| Removed from standalone | Because | What replaces it |
|---|---|---|
| Plant Cost Master (Groups A and B) | Core's T4 holds every rate | Read Core. Zero Kaizen cost-setup screens |
| The manual bottleneck list | Core carries a Bottleneck Factor per machine | Read BF; the question leaves the interview |
| Kaizen's own savings formulas | Two implementations of one formula produce two numbers | Core's formula module, called once |
| Kaizen's PDCA object and status chips | Core's Actions engine is the PDCA cycle | An approved Kaizen creates a Core Action |
| Kaizen's 2-week verification rule | Two rules for one metric means the total depends on who you ask | Core's Recovered Profit ledger, unchanged |
| A separate Friday digest | A second weekly blast trains people to ignore both | A block inside Core's weekly owner digest |
| Onboarding templates K1–K3 | People, stations and cost data arrive via Core's T1–T7 | Only K4 and K5 remain — about ten minutes |

What the plugin adds that no standalone product can:

| Step | Feature |
|---|---|
| 1 | Voice-first capture, transcribed on the device; the model tidies and may never introduce a number nobody said |
| 2 | Vision AI under three hard rules — operator consent, "possible" never "confirmed", five finding categories only |
| 3–4 | Category confirmed in one tap; 4–6 questions, minus everything Core already knows |
| **5A** | **The Core History Check** — what this problem has already cost, on events Core recorded, in three tiers, with its rank among the plant's recurring losses, its trend, duplicate-Action and recurrence detection |
| 6 | Core's formula module prices it; Kaizen's classification layer splits hard / soft / stars and never merges them |
| 7 | One cheaper alternative, never two, ending in a question — AI advises, never blocks |
| 8 | The Business Impact Card, with the ticking cost of doing nothing |
| 9 | Implementation forecast from Core's own Action and closure history, available from the first Kaizen |
| 10 | Approval templates A/B/C plus threshold rules; above Core's threshold it becomes a Core Action |
| 12–13 | Operator-facing progress driven by the Action's status, with a message on every transition |
| §9 | Verification through Core's Recovered Profit — dual attribution, the rupee counted once |
| §10 | Pattern detection across Kaizen submissions **and** Core events; one plant-wide Action, not seventeen |
| §11 | Priority scoring weighted towards Core-verified cost over projected savings; weights are configuration |

AI runs on a **local Ollama** (`OLLAMA_*` in `backend/.env`) so photos and voice
never leave the plant, and every path has a deterministic fallback — nothing in
the operator journey blocks on a model being up. The money is never
model-generated.

Bugs caught while building, each now pinned by a test:
- **`PdcaState` exists in both the kernel and the models**, so `action.state is PdcaState.VERIFIED` silently never matched and no Kaizen ever reached Verified. A `str` enum's `==` *does* match, which is what made it so quiet;
- a real local model answered the vision prompt with a bare JSON **array** instead of the object it was asked for, crashing the parser mid-journey;
- the same model **copied the worked example** out of the challenge prompt verbatim, so every Kaizen came back with "move the toolbox to the midpoint";
- "the insert breaks" classified as a *cost* Kaizen, which has no reason code to match against, so the History Check found nothing;
- an idea about a fan "near the inspection bench" priced as the plant's most valuable idea (keyword on a place, not a problem);
- keyword substrings — "oil" matching "soil", "tool" matching both "stool" and "toolbox";
- an idea naming "CNC-3" in its text priced plant-wide;
- single-operator risk reported as a station's *entire* downtime ×1.5 (₹1.6 lakh/yr for CNC-3) instead of the cost of one idle shift (₹10,921);
- a compliance clause scored as a gap while listing 25 rows of evidence.

---

## Section 3 / 14.2 — The job-order spine and multi-stage plants

| Capability | Status |
|---|---|
| Job orders spanning shifts, days and machines | ✅ |
| Lots, three granularities (day-batch → shift-batch → job lot) | ✅ |
| Routing operations in the data model from day one | ✅ |
| Change Job event (§14.3) | ✅ API · ✅ UI (`components/change-job.tsx`, from the machine grid) |
| **Stage completion capture** (Op10 → Op20 → Op30 → Op40) | ✅ one tap per stage, from the in-progress board |
| **WIP visibility** — "500 through machining, 430 through assembly, 70 in WIP" | ✅ `app/kernel/flow.py`, in-progress board |
| **Dispatch reconciliation** — despatched vs produced | ✅ flagged once despatch is recorded |
| **Traceability query** — lot → job, machine, operator, inspector, defects, same-day events | ✅ ~120 ms, lot #4471 reproduces §3's answer |

§3 calls traceability *"what that gives us for free"*. Built, and seeded with
§3's own worked example — search `4471`:

> Lot #4471 was Job Order 2231 for AK-447, run on CNC-3, Shift A, 26 August,
> operator Kumar M., inspected by Kavitha R., 362 good / 9 rejected — 7 for burr —
> and CNC-3 had a tool breakage event (47 min, ₹1,356) during the run.

Three rules tell a *gap* from *work in progress* (`app/kernel/flow.py`): parts
between stages are WIP and never flagged; more parts reaching a stage than left
the previous one is always flagged; parts that passed QC but never left the gate
are flagged only once despatch is recorded. Day- and shift-batch lots (no job
order) trace too — §3's first rung for a plant with no lot discipline.

Two seed defects were corrected on the way: every routing's final step was stored
as *quality* rather than *dispatch*, and every lot's quantity was a copy of the
job's planned quantity rather than what was made.

---

## Section 4 — The data

| Item | Status |
|---|---|
| 4.1 Digital Readiness Questionnaire (scored) | ✅ |
| 4.1 Master upload + confirm screen | ✅ T1–T7, strict-parse |
| 4.2 Three costing modes, identical formulas | ✅ |
| 4.3 Daily data, 15-minute entry budget | ✅ |
| 4.4 Live line status, escalations, end-of-shift report, meeting pack, Coach, Copilot | ✅ |
| 4.4 **Monthly owner report** | ✅ `/kpis/monthly`, in §5.6's order, printable |

---

## Section 5 — Actions & PDCA

Fully built: trigger rules, severity routing, the PDCA lifecycle, RCA with
pre-filled 5-Why, the four Factory Memory questions on closure, and Recovered
Profit with the baseline freeze and two-week sustain rule. ✅

---

## Section 7 — The ten screens

| # | Screen | Status |
|---|---|---|
| 1 Shift Start | ✅ |
| 2 Machine Grid | ✅ |
| 3 Downtime Log | ✅ |
| 4 Production Entry | ✅ |
| 5 Quality Entry | ✅ |
| 6 Shift Closure | ✅ |
| 7 Morning Meeting Mode | ✅ |
| 8 Owner Home (Money) | ✅ |
| 9 Actions Board | ✅ |
| 10 RCA & Closure Prompt | ✅ |

Plus the two non-screen deliverables: escalation ladder ✅, AI Shift Coach ✅.

**✅ Offline capability.** §7 and §16.3 both require it: *"Everything must work
offline on the tablet and sync when internet returns — Tamil Nadu plant Wi-Fi
cannot be trusted."* Built — `lib/offline/` (IndexedDB outbox, idempotent
`client_ref` per entry, `POST /sync/batch`, and a held-entry queue an owner
resolves in Settings).

---

## Section 8 — KPIs

The formula reference is fully implemented in `app/kernel/` and unit-tested
against the spec's worked examples. ✅

**✅ Role-based KPI pages** (`/kpis`) — Executive, Supervisor, Quality, Maintenance,
about a dozen KPIs, every one from data already captured, every row a machine,
line, reason or defect (never a person).

**A bug found while building them.** The per-shift OEE measured all stations'
downtime and output against *one* shift's planned minutes, and divided inspected
good parts by total production. A healthy shift reported OEE 42%, Availability
69%, Quality 60%. OEE is now built per station through the kernel's single
`compute_oee` and aggregated by numerators and denominators
(`kernel.aggregate_oee`): the same shift reads OEE 75.7% (A 98.2%, P 77.8%,
Q 99.1%). The endpoint that served it also returned a 500; fixed.

---

## Section 9 — Build order

Stage 1 ✅ · Stage 2 ✅ · Stage 3 🟡 (Copilot pattern tier ✅, WhatsApp ❌)
Parallel plugin stream ✅ (all three plugins built — see Layer 5 above).

The NOT-building list was respected in full: no hardware or firmware, no
benchmarking, no ERP/Tally integration, no ABC costing, no material-batch
traceability, no Tamil voice, no consultant portal.

---

## Sections 10–13, 15–16

| Item | Status |
|---|---|
| §10 No-blame rule as a design constraint | ✅ enforced in UI and reports |
| §12 Risk mitigations (closure lock, cross-verification, baseline freeze, action fatigue) | ✅ |
| §13 Hardware upgrade path — the data-source seam | ✅ built and tested before any device exists |
| §15 T1–T7 template pack | ✅ including the extra T6 column §2.3 argued for |
| §16.4 WATI application | ❌ external, still to be applied for |

---

## What is being built now, in order

1. ~~**Layer 5 — the three plugins.**~~ ✅ done.
2. ~~**Multi-stage tracking**~~ ✅ done.
3. ~~**Traceability query**~~ ✅ done.
4. ~~**Role KPI pages**~~ ✅ done.
5. ~~**Monthly owner report**~~ ✅ done.
6. ~~**Offline capture and sync**~~ ✅ done.
7. ~~**Change Job UI**~~ ✅ done.
8. ~~**The Maintenance and Stores desks**~~ ✅ done — §1's last two unserved roles.

### Still open

| Item | Why it is still open |
|---|---|
| **WhatsApp delivery (WATI)** | §16.4 — blocked on a 3–5 week external approval. The escalation ladder and Copilot both run today over push and in-app; the transport is already abstracted (`app/delivery/transports.py`), so WATI is a provider swap, not a build. |
| **Layer 6 — Industry Benchmark** | ⛔ by §3's own rule: it needs 40–50 live plants. *"Nothing is built for it now."* |
| **Tamil voice, ERP/Tally, ABC costing, material-batch traceability, consultant portal, hardware** | ⛔ on §9's NOT-building list, which *"is as binding as the feature list"*. |

### Known demo-data issues (data, not code)

- The seed's T7 targets (200 parts/shift) sit far below what its machines
  produce, so line schedule adherence shows 135–177%.
- The demo plant tags `TOOL_CHANGE` "Tool Change (planned)" as
  Recoverable/unplanned while the default library (§16.4) ships it as
  Structural/planned. The label and the attributes disagree; the attributes are
  what every formula reads, so the desks and KPIs treat it as recoverable
  maintenance work. Worth settling against a real plant's own tagging.
- Three stops in the demo plant ran for 1,900–6,200 minutes across several days
  before being closed. Real enough as data, but they dominate any cost ranking
  they appear in.
