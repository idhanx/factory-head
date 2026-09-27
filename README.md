# FactoryManager AI

**The AI Plant Manager for MSME manufacturing.** Machine → Money → Action.

Every minute of downtime and every rejected part becomes a rupee figure the
moment it happens, ranked by what is actually recoverable — and the plant is
told the one action that recovers the most.

Built from `FactoryManager_AI_Product_Specification_v5 - 08.09.2026.pdf`
(42pp, Sections 1–16). Where the spec left a decision open, the choice and its
reasoning are in [`ARCHITECTURE.md`](prd-files/ARCHITECTURE.md) and
[`DECISIONS.md`](prd-files/DECISIONS.md).

---

> **Running it:** see [`RUNNING.md`](prd-files/RUNNING.md) for the full set of commands,
> troubleshooting, and the optional LLM/MQTT services.

## Quick start

```bash
# 1. Infrastructure (Postgres + TimescaleDB, Redis, EMQX)
docker compose up -d db redis emqx

# 2. Backend
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
python scripts/seed.py          # six weeks of demo history
uvicorn app.main:app --reload --port 8080

# 3. Worker — escalations, Recovered Profit accrual, the 5:45 AM Coach
python -m app.workers.runner

# 4. Frontend
cd ../frontend
npm install
npm run dev                     # http://localhost:3000
```

Or the whole stack: `docker compose up`.

### Demo logins (seeded plant: Kumaran Auto Components, Hosur)

| Role | Sign in with | Password | Lands on |
|---|---|---|---|
| Owner | `owner@kumaranauto.in` | `owner1234` | Money screen |
| Supervisor | `+919000000011` | `shift1234` | Machine grid |
| Quality | `+919000000012` | `quality1234` | Quality entry |
| Maintenance | `+919000000013` | `maint1234` | Actions board |

---

## What is built

The six layers of §3, bottom-up. Layers 1 and 2 are the launch product.

| Layer | What it does | Status |
|---|---|---|
| **1 — Factory Core** | Shift start, machine grid, downtime log, production entry, quality entry, shift-closure lock | ✅ |
| **2 — Factory Brain** | Cost engine, ranked losses, escalation ladder, Actions & PDCA, Recovered Profit, AI Shift Coach | ✅ |
| **3 — Factory Copilot** | Plain-language questions over the plant's own data, tier-gated | ✅ web; WhatsApp pending WATI |
| **4 — Factory Memory** | The four closure questions, searchable forever | ✅ |
| **5 — Factory Plugins** | Compliance/Audit, Kaizen, Skill Matrix | Data model ready; UI not built |
| **6 — Industry Benchmark** | Cross-plant comparison | Not built — needs 40–50 live plants (§3) |

### The ten screens (§7)

| # | Screen | Route | User |
|---|---|---|---|
| 1 | Shift Start | `/shift` | Supervisor |
| 2 | Machine Grid | `/floor` | Supervisor |
| 3 | Downtime Log | `/floor` (sheet) | Supervisor |
| 4 | Production Entry | `/floor` (sheet) | Supervisor |
| 5 | Quality Entry | `/quality` | Inspector |
| 6 | Shift Closure | `/shift` | Supervisor |
| 7 | Morning Meeting Mode | `/meeting` | Whole team |
| 8 | Owner Home (Money) | `/owner` | Owner |
| 9 | Actions Board | `/actions` | Supervisor / owners |
| 10 | RCA & Closure Prompt | `/actions/[id]` | Whoever closes it |

Screens 3 and 4 are sheets over the grid rather than separate pages: a
supervisor logging a stop is already standing at the grid, and the 20-second
budget should not be spent on navigation.

---

## Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 15 (App Router) + TypeScript |
| UI | Tailwind CSS v4 + shadcn/ui primitives (Radix) |
| Real-time | WebSockets (Redis pub/sub fan-out) |
| Backend | FastAPI + Python 3.12 |
| API | REST + WebSocket |
| Database | PostgreSQL 16 + TimescaleDB |
| Cache / scheduler | Redis 7 |
| MQTT broker | EMQX 5 |
| RAG | Postgres FTS now; LlamaIndex + pgvector when Memory has depth |
| LLM serving | vLLM (OpenAI-compatible endpoint) |
| ML | scikit-learn / XGBoost / PyTorch (Coach tiers 2–3) |

Two substitutions from the stack as specified, both documented in
[`DECISIONS.md`](prd-files/DECISIONS.md):

- **psycopg3 instead of asyncpg** — one driver serves both the async app and
  Alembic's synchronous migrations.
- **A ~100-line Redis scheduler instead of Celery/arq** — the only durable-delay
  requirement is the 5/15/30-minute ladder, whose due times already live in
  Postgres. The worker is a tick with a lock, not a queue.

---

## How it is organised

```
backend/
├── app/
│   ├── kernel/        ← every formula in §8, implemented exactly once. Pure.
│   ├── models/        ← 35 tables: the job-order spine, capture rows, actions
│   ├── services/      ← DB ↔ kernel: costing, shifts, capture, actions, reports
│   ├── ai/            ← fact bundles, Coach, Copilot, the vLLM client
│   ├── api/v1/        ← REST routers
│   ├── ws/            ← the live channel
│   ├── ingest/        ← T1–T7 template parsers, readiness questionnaire
│   └── workers/       ← escalation ladder, profit accrual, Coach generation
├── alembic/           ← migrations (incl. RLS policies and hypertables)
├── scripts/           ← schema creation, demo seed
└── tests/             ← 63 tests: kernel golden files + service integration
frontend/src/
├── app/(app)/         ← the ten screens, role-routed
├── components/        ← station tile, UI primitives
└── lib/               ← API client, WebSocket hook, money formatting
infra/emqx/            ← the sensor seam, documented before any device exists
```

### The one rule worth knowing before you change anything

> "Every formula must be implemented once, in one place, and every
> screen/report/Copilot answer must draw from that single implementation. Two
> screens showing two different OEE numbers for the same shift is a
> credibility-ending bug." — §8.1

That place is [`backend/app/kernel/`](backend/app/kernel/). It has **zero I/O**:
no database handle, no clock, no network, no config lookup. The API, the
worker and the AI fact-builder all import from it. Its golden tests
(`tests/test_kernel_cost.py`) are anchored on the spec's own worked examples,
and a diff in any of them is a release blocker.

---

## Testing

```bash
cd backend
pytest                              # 63 tests
pytest tests/test_kernel_cost.py    # the golden cost fixtures
```

The service tests run against a real Postgres (`factorymanager_test`, created
and dropped per run). Mocking the session would test the mocks: most of what
matters — effective-dated rate resolution, the one-open-downtime-per-station
constraint, RLS — exists only in the database.

---

## Before production

Four things this build does **not** yet do safely:

1. **Connect as a non-owner database role.** RLS policies are in place and
   verified, but the app currently connects as the table owner, who bypasses
   RLS. Create an application role (`NOINHERIT`, no `BYPASSRLS`) and grant it
   only DML. Until then, tenancy rests on the plant-scoped JWT alone.
2. **Set `SECRET_KEY`.** Startup refuses the dev default outside `local`, but
   check it.
3. **Apply for the WhatsApp Business API (WATI) — §16.4 says day one.**
   Approval takes 3–5 weeks in India and both escalations and the Copilot want
   it. The ladder runs without it and stays timestamped; it just reaches fewer
   phones.
4. **Decide the open items in §16.5** — they have defaults, and the defaults are
   listed in [`DECISIONS.md`](prd-files/DECISIONS.md).

---

## Documentation

- [`ARCHITECTURE.md`](prd-files/ARCHITECTURE.md) — data model, calculation architecture, tenancy, AI grounding
- [`DECISIONS.md`](prd-files/DECISIONS.md) — what was decided while building, and why
- [`API.md`](prd-files/API.md) — endpoints, auth, the WebSocket protocol
- [`infra/emqx/README.md`](infra/emqx/README.md) — the sensor seam
- [`IMPLEMENTATION-ROADMAP.md`](prd-files/IMPLEMENTATION-ROADMAP.md) — the 30-week plan
- [`CRITICAL-DECISIONS-AND-RISKS.md`](prd-files/CRITICAL-DECISIONS-AND-RISKS.md) — risk register
# factory-head
