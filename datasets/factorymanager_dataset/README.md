# FactoryManager AI — full-product test dataset

**419,557 rows** across all 49 tables of the schema (Alembic head `9f3c2d6e8a41`), for 9 plants, with data up to **2026-09-12T11:20:00+05:30**.

Every rupee figure (downtime cost, scrap cost, baselines, Recovered Profit, OEE snapshots, Kaizen savings) was computed by `backend/app/kernel` — the same functions the API and worker use — so loading this data and re-running a report gives the same numbers. Money columns are **integer paise** (÷100 for ₹). Timestamps are **IST (+05:30)**.

Regenerate: `cd backend && python scripts/generate_dataset.py` (deterministic ids for a given `--seed`).

## Formats

| Folder | Contents |
|---|---|
| `sql/schema.sql` | Plain-PostgreSQL DDL at Alembic head (tables, constraints, indexes, RLS policies), stamped with the revision |
| `sql/factorymanager_data.sql` | The load: one transaction that includes each `sql/data/*.sql` part in foreign-key order |
| `sql/data/NNN_data.sql` | The `INSERT`s themselves, split so no file passes GitHub's size limit. Load the file above, not these |
| `sql/00_reset_all_tables.sql` | Optional `TRUNCATE … CASCADE` of every table — **destructive**, for a dev DB only |
| `csv/` | One CSV per table (header row; JSON as text; arrays as `{…}` literals) and `load_csv.sql`. A table too large for one file is split into `<table>.001.csv`, `<table>.002.csv` … and the loader copies each |
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

# Then prove it matches the product's code (read-only, exits non-zero on mismatch).
# verify_dataset.py connects as the owning role, so set BOTH URLs or it will
# quietly check whichever database .env points at instead of this one.
cd backend
DATABASE_URL=postgresql+psycopg://…/factorymanager_dataset \
MIGRATION_DATABASE_URL=postgresql+psycopg://…/factorymanager_dataset \
  python scripts/verify_dataset.py
```

## Plants

| Code | Plant | Costing | Lots | Shifts | Live from | Shift sessions | Downtime events | Coach tier now | Verified Recovered Profit |
|---|---|---|---|---|---|---:|---:|---:|---:|
| KUMARAN | Kumaran Auto Components, Hosur, Tamil Nadu | standard | job_lot | 2 | 2026-02-16 | 351 | 2,247 | 3 | ₹379,296 |
| SRIVARI | Sri Vari Pressings Pvt Ltd, Ambattur Industrial Estate, Chennai, Tamil Nadu | basic | shift_batch | 3 | 2026-03-16 | 454 | 2,063 | 3 | ₹324,886 |
| KOVAI | Kovai Precision Engineering, SIDCO Industrial Estate, Kurichi, Coimbatore, Tamil Nadu | standard | job_lot | 2 | 2026-04-20 | 245 | 1,277 | 3 | ₹406,138 |
| SAKTHI | Sakthi Fasteners & Forgings, SIPCOT Sriperumbudur, Kanchipuram, Tamil Nadu | basic | day_batch | 2 | 2026-08-24 | 33 | 224 | 1 | ₹0 |
| VELAVAN | Velavan Forge & Machining Pvt Ltd, SIPCOT Ranipet, Vellore, Tamil Nadu | standard | job_lot | 2 | 2026-04-06 | 267 | 1,848 | 3 | ₹1,505,353 |
| ANNAI | Annai Polymers & Precision Moulding, Sriperumbudur, Kanchipuram, Tamil Nadu | basic | day_batch | 3 | 2026-05-18 | 298 | 1,210 | 3 | ₹464,595 |
| NILGIRI | Nilgiri Pumps & Castings, Kurichi Industrial Estate, Coimbatore, Tamil Nadu | standard | shift_batch | 2 | 2026-06-15 | 151 | 1,082 | 3 | ₹263,533 |
| THENI | Theni Sheet Metal & Fabrication, Kappalur Industrial Estate, Madurai, Tamil Nadu | basic | shift_batch | 2 | 2026-07-13 | 103 | 740 | 2 | ₹40,619 |
| VAIGAI | Vaigai Auto Electricals Pvt Ltd, Ambattur Industrial Estate, Chennai, Tamil Nadu | standard | job_lot | 3 | 2026-08-10 | 82 | 511 | 2 | ₹4,569 |

## Rows per table

| Table | Rows |
|---|---:|
| `plants` | 9 |
| `users` | 84 |
| `alert_recipients` | 82 |
| `attachments` | 3,412 |
| `audit_log` | 4,281 |
| `audit_packs` | 27 |
| `compliance_clauses` | 145 |
| `copilot_queries` | 2,938 |
| `cost_families` | 49 |
| `cost_rates` | 96 |
| `defect_codes` | 97 |
| `kaizen_settings` | 9 |
| `margin_band_values` | 63 |
| `notifications` | 18,709 |
| `operators` | 347 |
| `parts` | 92 |
| `push_subscriptions` | 104 |
| `readiness_assessments` | 65 |
| `reason_codes` | 197 |
| `shifts` | 30 |
| `template_uploads` | 71 |
| `user_plant_roles` | 100 |
| `coach_briefings` | 2,854 |
| `job_orders` | 1,791 |
| `kaizen_category_codes` | 153 |
| `routing_operations` | 406 |
| `shift_sessions` | 1,984 |
| `stations` | 122 |
| `actions` | 2,272 |
| `lots` | 2,313 |
| `material_stagings` | 1,730 |
| `operator_skills` | 800 |
| `production_targets` | 275 |
| `sync_holds` | 39 |
| `dispatch_records` | 6,824 |
| `job_sessions` | 27,257 |
| `kaizen_ideas` | 542 |
| `kaizen_patterns` | 14 |
| `memory_entries` | 2,006 |
| `recovered_profit_entries` | 12,228 |
| `root_cause_analyses` | 1,077 |
| `stage_completions` | 43,576 |
| `training_needs` | 80 |
| `downtime_events` | 11,202 |
| `kaizen_points` | 850 |
| `production_counts` | 212,846 |
| `quality_records` | 18,347 |
| `action_events` | 4,467 |
| `escalations` | 32,495 |
| **Total** | **419,557** |

## Product scenarios covered

Each line below is present in the data, so the screen or rule it names can be tested against real rows.

- **Shift Start / Closure Lock (Screens 1, 6)** — shift status: closed 1,974, open 9, reopened 1. The last shift of each plant is still open at the data cut-off, with a stop that has no reason code and stations with no count yet (closure blockers). Closed shifts carry a frozen KPI `summary`; ~2% were closed with an owner override (logged in `audit_log`).
- **Machine grid & live ticker (Screens 2, 3)** — downtime status: closed 11,184, open 18. Two stations per plant are stopped right now, one without a reason yet. Sources: manual 8,851, system 2,351 (`system` = changeover gaps captured by Change Job, §14.3; `sensor` = pulse counters on SAKTHI headers from 1 Sep).
- **Escalation ladder (5/15/30 min)** — 32,495 rungs: cancelled 16,484, failed 140, pending 18, sent 15,853; channels push 10,785, sms 2,052, whatsapp 19,658. SAKTHI has no WhatsApp approval, so its rungs go by SMS; ~3% of WhatsApp sends fail and fall back to SMS.
- **Production entry (Screen 4)** — 212,846 hourly counts; ~1% corrected before close (audit trail); ~3% of station-shifts padded by the supervisor.
- **Quality entry & cross-verification (Screen 5)** — disposition: rework 952, scrap 16,730, use_as_is 665; 360 records flagged by count reconciliation. Scrap is costed with `kernel.scrap_cost` (material + processing of completed ops + lost contribution × BF).
- **Job Order spine & traceability (§3, §14.2-14.3)** — job orders: cancelled 17, completed 1,376, paused 10, planned 42, running 346; lots: day_batch 275, job_lot 1,030, shift_batch 1,008; jobs span shifts, days and stations; Change Job sessions link via `previous_session_id`; stage completions through every routing op, dispatch with vehicle numbers.
- **Costing modes & effective-dated rates** — KUMARAN (standard) corrects the CNC machine rate on 13 Jul and overhead on 3 Aug; VELAVAN corrects the forging rate on 8 Jun after a tariff revision and machining labour on 1 Jul; NILGIRI re-derives the VTL rate on 17 Aug. Events before each correction keep the old snapshot — history never restates. SRIVARI and ANNAI (basic) move from default library rates to derived rates on 6 Jul. Margin-band fallback is used for band-only parts. Every `cost_rates.derivation` reproduces its rate through the kernel formula.
- **Actions & PDCA (§5)** — 2,272 actions. Trigger: big_single_event 1,054, compliance_capa 14, kaizen 47, manual 91, recurring_pattern 1,050, skill_gap 16. State: cancelled 421, in_progress 65, monitoring 424, open 114, planned 107, reopened 518, root_cause 38, verified 585. Includes overdue, delayed (with reasons), cancelled structural and duplicate actions, RCA-routed actions with completed 5-Whys and AI suggestions from the plant's own memory, and fixes that did not hold (automatically reopened).
- **Recovered Profit (§5.6)** — 12,228 weekly ledger rows, frozen baselines, 2-consecutive-week rule, never negative; accrual stops on reopen.
- **Factory Memory (Layer 4)** — 2,006 entries (closures plus entries imported from the old maintenance register).
- **AI Coach (§14.6)** — 2,854 briefings; tier counts {0: 138, 1: 544, 2: 1048, 3: 1124} — silent in the first shifts, rule-based, then pattern tier after 4 weeks and deep tier after 12.
- **Copilot (Layer 3)** — 2,938 questions; intents memory 583, pattern 458, period_loss 778, quality 344, today 775; English and Tamil; web and WhatsApp; pattern questions refused while the plant is below Tier 2.
- **Onboarding (§4.1, §15)** — 65 readiness questionnaires (prospects, live plants and abandoned ones) scored by `app.ingest.readiness`; 71 template uploads including failed first attempts.
- **Plugins (Layer 5)** — compliance clauses: gap 2, not_applicable 1, partial 17, ready 125; 27 audit packs; Kaizen: accepted 11, approved 5, implemented 128, rejected 172, scored 217, verified 9; 800 skill-matrix entries; training needs: coverage 8, gap 72.
- **Kaizen AI v2 (Plugin Edition)** — every idea carries the Step 5A history snapshot it was priced from, the hard/soft split from `kernel.kaizen.classify`, and the 0-100 priority score and verdict from `kernel.kaizen.priority_score`. 850 points awards on outcomes (submitted / approved / implemented / verified, never on submission count); 14 detected patterns (§10); one `kaizen_settings` row and 153 category ↔ Core-code mappings per plant.
- **Delivery outbox (§14.5)** — 18,709 notification rows behind the escalations and Coach briefings: push 13,078, sms 660, whatsapp 4,971; failed 140, sent 18,569. 104 push subscriptions, including endpoints that have started failing.
- **Photos (§14.5)** — 3,412 attachments: the inspector's photo of a rejection and the operator's photo on a Kaizen submission, stored by sha256 under a per-plant, per-month storage key.
- **Stores staging** — 1,730 staging rows for tomorrow's jobs: pending 249, short 238, staged 1,243. A `short` row is what a Material Wait stop looks like the day before it happens.
- **Offline sync (§14.1)** — 39 entries that reached the server after their shift had locked: applied 21, discarded 11, held 7. Nothing is silently dropped and nothing is silently applied; a supervisor decides, and the reason is on the row.

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
| VELAVAN | Mr. R. Velavan | owner | `owner@velavanforge.in` | `owner1234` |
| VELAVAN | Jagadeesan M. | supervisor | `+919400000401` | `plant1234` |
| VELAVAN | Alagappan S. | supervisor | `+919400000402` | `shift1234` |
| VELAVAN | Nagarajan K. | supervisor | `+919400000403` | `shift1234` |
| VELAVAN | Bhuvaneswari R. | quality | `+919400000404` | `quality1234` |
| VELAVAN | Chitra M. | quality | `+919400000405` | `quality1234` |
| VELAVAN | Perumal T. | maintenance | `+919400000406` | `maint1234` |
| VELAVAN | Dhanasekaran V. | maintenance | `+919400000407` | `maint1234` |
| VELAVAN | Kaliyaperumal S. | stores | `+919400000408` | `stores1234` |
| ANNAI | Mrs. Annapoorani G. | owner | `owner@annaipolymers.in` | `owner1234` |
| ANNAI | Srinivasan R. | supervisor | `+919500000501` | `plant1234` |
| ANNAI | Ezhilarasan K. | supervisor | `+919500000502` | `shift1234` |
| ANNAI | Vasanthakumar P. | supervisor | `+919500000503` | `shift1234` |
| ANNAI | Rajendran M. | supervisor | `+919500000504` | `shift1234` |
| ANNAI | Amudha S. | quality | `+919500000505` | `quality1234` |
| ANNAI | Jothi Lakshmi V. | quality | `+919500000506` | `quality1234` |
| ANNAI | Sridhar N. | maintenance | `+919500000507` | `maint1234` |
| ANNAI | Anbarasu L. | stores | `+919500000508` | `stores1234` |
| NILGIRI | Mr. Nallasamy P. | owner | `owner@nilgiripumps.in` | `owner1234` |
| NILGIRI | Ramakrishnan S. | supervisor | `+919600000601` | `plant1234` |
| NILGIRI | Marimuthu A. | supervisor | `+919600000602` | `shift1234` |
| NILGIRI | Selvaraj K. | supervisor | `+919600000603` | `shift1234` |
| NILGIRI | Hemalatha R. | quality | `+919600000604` | `quality1234` |
| NILGIRI | Vijayalakshmi T. | quality | `+919600000605` | `quality1234` |
| NILGIRI | Chinnasamy M. | maintenance | `+919600000606` | `maint1234` |
| NILGIRI | Rajamani V. | stores | `+919600000607` | `stores1234` |
| THENI | Mr. Thangavel M. | owner | `owner@thenisheetmetal.in` | `owner1234` |
| THENI | Balasubramanian R. | supervisor | `+919700000701` | `plant1234` |
| THENI | Ilangovan S. | supervisor | `+919700000702` | `shift1234` |
| THENI | Manikandan P. | supervisor | `+919700000703` | `shift1234` |
| THENI | Sumathi K. | quality | `+919700000704` | `quality1234` |
| THENI | Karuppasamy V. | maintenance | `+919700000705` | `maint1234` |
| THENI | Ponnusamy N. | stores | `+919700000706` | `stores1234` |
| VAIGAI | Mr. Vaigaivanan K. | owner | `owner@vaigaielectricals.in` | `owner1234` |
| VAIGAI | Shanmugasundaram R. | supervisor | `+919800000801` | `plant1234` |
| VAIGAI | Prabakaran M. | supervisor | `+919800000802` | `shift1234` |
| VAIGAI | Yuvarani S. | supervisor | `+919800000803` | `shift1234` |
| VAIGAI | Kandasamy T. | supervisor | `+919800000804` | `shift1234` |
| VAIGAI | Sasirekha P. | quality | `+919800000805` | `quality1234` |
| VAIGAI | Nithya K. | quality | `+919800000806` | `quality1234` |
| VAIGAI | Loganathan V. | maintenance | `+919800000807` | `maint1234` |
| VAIGAI | Arulmozhi S. | stores | `+919800000808` | `stores1234` |

## Notes

- Every company, customer and person outside KUMARAN is fictional; KUMARAN reuses the customers from `scripts/seed.py` so the demo stays recognisable. Phone numbers are placeholders.
- Tamil labels on reason and defect codes should be reviewed by a native speaker before a real plant sees them.
- `password_hash` values are argon2 hashes of the passwords above; they differ between regenerations (random salts). All other ids and values are stable for a given seed.
