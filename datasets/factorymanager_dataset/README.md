# FactoryManager AI — full-product test dataset

**111,308 rows** across all 40 tables of the schema (Alembic head `0ba4b6bb4343`), for 4 plants, with data up to **2026-09-12T11:20:00+05:30**.

Every rupee figure (downtime cost, scrap cost, baselines, Recovered Profit, OEE snapshots, Kaizen savings) was computed by `backend/app/kernel` — the same functions the API and worker use — so loading this data and re-running a report gives the same numbers. Money columns are **integer paise** (÷100 for ₹). Timestamps are **IST (+05:30)**.

Regenerate: `cd backend && python scripts/generate_dataset.py` (deterministic ids for a given `--seed`).

## Formats

| Folder | Contents |
|---|---|
| `sql/schema.sql` | Plain-PostgreSQL DDL at Alembic head (tables, constraints, indexes, RLS policies), stamped with the revision |
| `sql/factorymanager_data.sql` | One transaction of multi-row `INSERT`s in foreign-key order |
| `sql/00_reset_all_tables.sql` | Optional `TRUNCATE … CASCADE` of every table — **destructive**, for a dev DB only |
| `csv/` | One CSV per table (header row; JSON as text; arrays as `{…}` literals) and `load_csv.sql` |
| `excel/factorymanager_dataset.xlsx` | Every table as a sheet plus a README sheet |
| `forms/<PLANT>/T1…T7_*.xlsx` | Filled onboarding templates in the exact layout `/onboarding` accepts |
| `forms/Readiness_Questionnaire_Responses.xlsx` | The Step-0 questionnaire and every response |
| `manifest.json` | Row counts, seed, plants, demo logins |

## Loading

The target must be an **empty** schema at Alembic head — the dataset contains its own `KUMARAN` plant and demo users, which collide with `scripts/seed.py` data.

```bash
# A. SQL only — no Python needed
createdb factorymanager_dataset
psql <url> -v ON_ERROR_STOP=1 -f sql/schema.sql
psql <url> -v ON_ERROR_STOP=1 -f sql/factorymanager_data.sql

# B. Through the project's own migrations
cd backend
createdb factorymanager_dataset            # or reuse a dev DB after 00_reset_all_tables.sql
DATABASE_URL=postgresql+psycopg://factorymanager:factorymanager@localhost:5432/factorymanager_dataset alembic upgrade head
psql postgresql://factorymanager:factorymanager@localhost:5432/factorymanager_dataset -v ON_ERROR_STOP=1 \
     -f ../datasets/factorymanager_dataset/sql/factorymanager_data.sql

# C. From CSV (schema created by A or B first)
cd ../datasets/factorymanager_dataset/csv && psql <url> -f load_csv.sql

# Then prove it matches the product's code (read-only, exits non-zero on mismatch)
cd backend && DATABASE_URL=postgresql+psycopg://…/factorymanager_dataset python scripts/verify_dataset.py
```

## Plants

| Code | Plant | Costing | Lots | Shifts | Live from | Shift sessions | Downtime events | Coach tier now | Verified Recovered Profit |
|---|---|---|---|---|---|---:|---:|---:|---:|
| KUMARAN | Kumaran Auto Components, Hosur, Tamil Nadu | standard | job_lot | 2 | 2026-06-01 | 177 | 1,421 | 3 | ₹204,859 |
| SRIVARI | Sri Vari Pressings Pvt Ltd, Ambattur Industrial Estate, Chennai, Tamil Nadu | basic | shift_batch | 3 | 2026-06-15 | 229 | 1,323 | 3 | ₹217,081 |
| KOVAI | Kovai Precision Engineering, SIDCO Industrial Estate, Kurichi, Coimbatore, Tamil Nadu | standard | job_lot | 2 | 2026-07-20 | 93 | 695 | 2 | ₹94,227 |
| SAKTHI | Sakthi Fasteners & Forgings, SIPCOT Sriperumbudur, Kanchipuram, Tamil Nadu | basic | day_batch | 2 | 2026-08-24 | 35 | 236 | 1 | ₹0 |

## Rows per table

| Table | Rows |
|---|---:|
| `plants` | 4 |
| `users` | 37 |
| `alert_recipients` | 35 |
| `audit_log` | 1,074 |
| `audit_packs` | 12 |
| `compliance_clauses` | 65 |
| `copilot_queries` | 730 |
| `cost_families` | 22 |
| `cost_rates` | 38 |
| `defect_codes` | 42 |
| `margin_band_values` | 30 |
| `operators` | 124 |
| `parts` | 39 |
| `readiness_assessments` | 60 |
| `reason_codes` | 82 |
| `shifts` | 13 |
| `template_uploads` | 31 |
| `user_plant_roles` | 43 |
| `coach_briefings` | 770 |
| `job_orders` | 449 |
| `routing_operations` | 155 |
| `shift_sessions` | 534 |
| `stations` | 55 |
| `actions` | 650 |
| `lots` | 608 |
| `operator_skills` | 357 |
| `production_targets` | 139 |
| `dispatch_records` | 1,926 |
| `job_sessions` | 7,506 |
| `kaizen_ideas` | 111 |
| `memory_entries` | 531 |
| `recovered_profit_entries` | 2,231 |
| `root_cause_analyses` | 185 |
| `stage_completions` | 11,855 |
| `training_needs` | 43 |
| `downtime_events` | 3,675 |
| `production_counts` | 59,917 |
| `quality_records` | 5,015 |
| `action_events` | 1,493 |
| `escalations` | 10,622 |
| **Total** | **111,308** |

## Product scenarios covered

Each line below is present in the data, so the screen or rule it names can be tested against real rows.

- **Shift Start / Closure Lock (Screens 1, 6)** — shift status: closed 528, open 4, reopened 2. The last shift of each plant is still open at the data cut-off, with a stop that has no reason code and stations with no count yet (closure blockers). Closed shifts carry a frozen KPI `summary`; ~2% were closed with an owner override (logged in `audit_log`).
- **Machine grid & live ticker (Screens 2, 3)** — downtime status: closed 3,667, open 8. Two stations per plant are stopped right now, one without a reason yet. Sources: manual 3,202, system 473 (`system` = changeover gaps captured by Change Job, §14.3; `sensor` = pulse counters on SAKTHI headers from 1 Sep).
- **Escalation ladder (5/15/30 min)** — 10,622 rungs: cancelled 5,362, failed 56, pending 8, sent 5,196; channels push 3,522, sms 518, whatsapp 6,582. SAKTHI has no WhatsApp approval, so its rungs go by SMS; ~3% of WhatsApp sends fail and fall back to SMS.
- **Production entry (Screen 4)** — 59,917 hourly counts; ~1% corrected before close (audit trail); ~3% of station-shifts padded by the supervisor.
- **Quality entry & cross-verification (Screen 5)** — disposition: rework 286, scrap 4,504, use_as_is 225; 133 records flagged by count reconciliation. Scrap is costed with `kernel.scrap_cost` (material + processing of completed ops + lost contribution × BF).
- **Job Order spine & traceability (§3, §14.2-14.3)** — job orders: cancelled 8, completed 327, paused 1, planned 18, running 95; lots: day_batch 56, job_lot 304, shift_batch 248; jobs span shifts, days and stations; Change Job sessions link via `previous_session_id`; stage completions through every routing op, dispatch with vehicle numbers.
- **Costing modes & effective-dated rates** — KUMARAN (standard) corrects the CNC machine rate on 13 Jul and overhead on 3 Aug; events before keep the old snapshot (history never restates). SRIVARI (basic) moves from default library rates to derived rates on 6 Jul. Margin-band fallback is used for band-only parts. Every `cost_rates.derivation` reproduces its rate through the kernel formula.
- **Actions & PDCA (§5)** — 650 actions. Trigger: big_single_event 181, compliance_capa 6, kaizen 32, manual 22, recurring_pattern 403, skill_gap 6. State: cancelled 99, in_progress 21, monitoring 135, open 48, planned 32, reopened 88, root_cause 5, verified 222. Includes overdue, delayed (with reasons), cancelled structural and duplicate actions, RCA-routed actions with completed 5-Whys and AI suggestions from the plant's own memory, and fixes that did not hold (automatically reopened).
- **Recovered Profit (§5.6)** — 2,231 weekly ledger rows, frozen baselines, 2-consecutive-week rule, never negative; accrual stops on reopen.
- **Factory Memory (Layer 4)** — 531 entries (closures plus entries imported from the old maintenance register).
- **AI Coach (§14.6)** — 770 briefings; tier counts {0: 61, 1: 230, 2: 401, 3: 78} — silent in the first shifts, rule-based, then pattern tier after 4 weeks and deep tier after 12.
- **Copilot (Layer 3)** — 730 questions; intents memory 153, pattern 113, period_loss 202, quality 96, today 166; English and Tamil; web and WhatsApp; pattern questions refused while the plant is below Tier 2.
- **Onboarding (§4.1, §15)** — 60 readiness questionnaires (prospects, live plants and abandoned ones) scored by `app.ingest.readiness`; 31 template uploads including failed first attempts.
- **Plugins (Layer 5)** — compliance clauses: gap 2, not_applicable 1, partial 4, ready 58; 12 audit packs; Kaizen: accepted 6, implemented 26, rejected 34, scored 45; 357 skill-matrix entries; training needs: coverage 2, gap 41.

## Demo logins

Floor roles sign in with a phone number; owners and platform accounts with email.

| Plant | Name | Role | Login | Password |
|---|---|---|---|---|
| ALL | Arjun Mehta | consultant | `consultant@factorymanager.ai` | `consult1234` |
| ALL | Platform Admin | admin | `admin@factorymanager.ai` | `admin1234` |
| KUMARAN | Mr. Kumaran | owner | `owner@kumaranauto.in` | `owner1234` |
| KUMARAN | Murugan V. | supervisor | `+919000000011` | `shift1234` |
| KUMARAN | Kavitha R. | quality | `+919000000012` | `quality1234` |
| KUMARAN | Suresh K. | maintenance | `+919000000013` | `maint1234` |
| KUMARAN | Ramesh P. | stores | `+919000000014` | `stores1234` |
| KUMARAN | Anitha D. | supervisor | `+919000000015` | `plant1234` |
| KUMARAN | Senthil Kumar R. | supervisor | `+919000000016` | `shift1234` |
| KUMARAN | Priya S. | quality | `+919000000017` | `quality1234` |
| SRIVARI | Mr. Venkatesan S. | owner | `owner@srivaripressings.in` | `owner1234` |
| SRIVARI | Lakshmi Narayanan K. | supervisor | `+919100000101` | `plant1234` |
| SRIVARI | Palanisamy R. | supervisor | `+919100000102` | `shift1234` |
| SRIVARI | Chandrasekar M. | supervisor | `+919100000103` | `shift1234` |
| SRIVARI | Thangaraj V. | supervisor | `+919100000104` | `shift1234` |
| SRIVARI | Revathi M. | quality | `+919100000105` | `quality1234` |
| SRIVARI | Karpagam S. | quality | `+919100000106` | `quality1234` |
| SRIVARI | Balamurugan T. | maintenance | `+919100000107` | `maint1234` |
| SRIVARI | Jayakumar P. | stores | `+919100000108` | `stores1234` |
| KOVAI | Ms. Deepa Ramasamy | owner | `owner@kovaiprecision.in` | `owner1234` |
| KOVAI | Karthikeyan V. | supervisor | `+919200000201` | `plant1234` |
| KOVAI | Sivakumar N. | supervisor | `+919200000202` | `shift1234` |
| KOVAI | Rajesh Babu M. | supervisor | `+919200000203` | `shift1234` |
| KOVAI | Nandhini P. | quality | `+919200000204` | `quality1234` |
| KOVAI | Gunasekaran A. | maintenance | `+919200000205` | `maint1234` |
| KOVAI | Manoj Kumar S. | stores | `+919200000206` | `stores1234` |
| SAKTHI | Mr. Sakthivel P. | owner | `owner@sakthifasteners.in` | `owner1234` |
| SAKTHI | Muthukumar R. | supervisor | `+919300000301` | `plant1234` |
| SAKTHI | Ganesan K. | supervisor | `+919300000302` | `shift1234` |
| SAKTHI | Arumugam S. | supervisor | `+919300000303` | `shift1234` |
| SAKTHI | Sangeetha R. | quality | `+919300000304` | `quality1234` |
| SAKTHI | Elango V. | maintenance | `+919300000305` | `maint1234` |
| SAKTHI | Pandian M. | stores | `+919300000306` | `stores1234` |

## Notes

- Customer names for SRIVARI, KOVAI and SAKTHI are fictional; KUMARAN reuses the customers from `scripts/seed.py`. Phone numbers are placeholders.
- Tamil labels on reason and defect codes should be reviewed by a native speaker before a real plant sees them.
- `password_hash` values are argon2 hashes of the passwords above; they differ between regenerations (random salts). All other ids and values are stable for a given seed.
