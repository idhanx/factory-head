# Specification coverage

A section-by-section audit of `FactoryManager_AI_Product_Specification_v5` against
what is built. Written so the gaps are as visible as the coverage.

Legend: ✅ built · 🟡 partial · ❌ not built · ⛔ deliberately out of scope (§9)

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
| **Kaizen AI** | Ideas priced against this plant's own losses and answered on submission, with the reasoning shown; accept → a real Action with a frozen baseline; decline requires a reason | Actions, Recovered Profit |
| **Skill Matrix AI** | Operator × station matrix; training needs from 'No Operator' stops (measured ₹/yr) and single-person stations (₹ per idle shift, from the kernel) | Actions |

Bugs caught while building the demo data, each now pinned by a test:
- an idea about a fan "near the inspection bench" priced as the plant's most valuable idea (keyword on a place, not a problem);
- keyword substrings — "oil" matching "soil";
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
| Change Job event (§14.3) | ✅ API · ❌ UI |
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

**❌ Offline capability.** §7 and §16.3 both require it: *"Everything must work
offline on the tablet and sync when internet returns — Tamil Nadu plant Wi-Fi
cannot be trusted."* Not built.

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
Parallel plugin stream ❌.

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

Still open, deliberately:

- **Offline capture and sync** (§7, §16.3) — needs a client-side store and a
  conflict policy; §16.4 says to decide those rules before the first pilot, so
  it is a work stream of its own rather than a patch.
- **WhatsApp delivery (WATI)** — blocked on a 3–5 week external approval.
- **Change Job UI** (§14.3) — the API exists; the screen does not.
- **Demo targets** — the seed's T7 targets (200 parts/shift) sit far below what
  its machines produce, so line schedule adherence shows 135–177%. A data issue,
  not a code one.

Originally deferred with reason: offline sync (needs a client-side store and a conflict
policy — a work stream of its own, §16.4 says decide the rules before the first
pilot), and WATI (blocked on a 3–5 week external approval).
