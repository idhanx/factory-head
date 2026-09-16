# Running the project

## It is already running

| Service | URL |
|---|---|
| **Web app** | http://localhost:3000 |
| API docs (Swagger) | http://localhost:8080/docs |
| API health | http://localhost:8080/health |

Sign in at http://localhost:3000/login — the demo buttons fill the credentials
for you, or:

| Role | Sign in with | Password | Lands on |
|---|---|---|---|
| Owner | `owner@kumaranauto.in` | `owner1234` | Money screen |
| Supervisor | `+919000000011` | `shift1234` | Machine grid |
| Quality | `+919000000012` | `quality1234` | Quality entry |
| Maintenance | `+919000000013` | `maint1234` | Actions board |

---

## Option A — Docker (the normal way)

Brings up Postgres+TimescaleDB, Redis, EMQX, API, worker and web together.

```bash
cd /home/srinath/Documents/IEA-UK/Factory-Mang-Ai

docker compose up -d                  # everything
docker compose logs -f api            # watch the API
docker compose down                   # stop
docker compose down -v                # stop and wipe the database
```

First run only — create the schema and load the demo plant:

```bash
docker compose exec api alembic upgrade head
docker compose exec api python scripts/seed.py
```

This needs to pull images from Docker Hub. If that fails with a DNS or network
error, use Option B.

---

## Option B — Run locally (what is running now)

### 1. Database and Redis

The project expects Postgres on **5432** and Redis on **6379**, which is what
`backend/.env` points at.

The path of least resistance is to take both from the compose stack and run
only the app processes locally — no `sudo`, and the data lives in named
volumes that survive a reboot:

```bash
make infra          # or: docker compose up -d db redis
```

This also gets you TimescaleDB, `pg_trgm` and `uuid-ossp`, which the system
Postgres packages do not provide out of the box.

Alternatively, the system cluster, if you have `sudo`:

```bash
sudo pg_ctlcluster 18 main start
sudo -u postgres psql -c "CREATE USER factorymanager PASSWORD 'factorymanager' SUPERUSER;"
sudo -u postgres psql -c "CREATE DATABASE factorymanager OWNER factorymanager;"
redis-server --daemonize yes
```

> **Do not run the database out of `/tmp`.** A throwaway instance started there
> on a non-standard port is how this setup lost its schema and its demo data to
> a reboot, and left `backend/.env` pointing at a port with nothing behind it.
> Every `connection refused` on startup has so far traced back to that.

### 2. Schema and demo data

```bash
cd backend
pip install -r requirements-dev.txt    # first time only

alembic upgrade head                   # create the schema and the fm_app role
python scripts/seed.py                 # 6 weeks of demo history (~30s)
```

**Two database roles.** Migrations and seed scripts connect as the owning role
(`MIGRATION_DATABASE_URL`). The API and worker connect as `fm_app`
(`DATABASE_URL`), a non-superuser that Postgres row-level security applies to:
every query is confined to the signed-in plant, and a query without a plant
returns nothing. The migration creates `fm_app` with `APP_DB_PASSWORD` from
`backend/.env`; set that before the first `alembic upgrade head`. If you point
`DATABASE_URL` at a superuser instead, the app still works but plant isolation
is back to relying on code alone.

`seed.py` **wipes and rebuilds** the demo plant every time, then runs the two
additive seeders below.

To add the newer demo data to a database you are **already using**, without
wiping anything (both are safe to run repeatedly):

```bash
python scripts/seed_plugins.py   # Layer 5: audit clauses, skill matrix, Kaizen ideas
python scripts/seed_stages.py    # stage flow + despatch for every lot, and lot #4471
```

Then try **Lots → search `4471`** for §3's traceability story.

 That is intended —
it is a demo fixture, not your data.

### 3. The three processes

Each in its own terminal:

```bash
# API
cd backend && uvicorn app.main:app --reload --port 8080

# Worker — escalations, Recovered Profit accrual, the 5:45 AM Coach briefing
cd backend && python -m app.workers.runner

# Web
cd frontend && npm install             # first time only
npm run dev
```

The worker is optional for clicking around, but without it escalations never
fire, Recovered Profit never accrues, and the Audit and Skill Matrix screens
are never re-scored (reading those screens no longer re-scores them; use the
"Re-score now" / "Refresh" buttons, or let the worker do it hourly).

---

## Make targets

```bash
make help        # list everything
make infra       # docker: Postgres + Redis + EMQX only
make migrate     # alembic upgrade head
make seed        # reload the demo plant
make api         # run the API
make worker      # run the worker
make web         # run the frontend
make test        # 165 backend tests (schema built by the migrations)
make lint        # ruff
make typecheck   # tsc --noEmit
```

---

## Verifying it works

```bash
cd backend
pytest -q                              # 165 tests, ~20s
curl -s localhost:8080/health          # {"status":"ok","database":"ok",...}
```

A quick end-to-end check from the command line:

```bash
TOKEN=$(curl -s -X POST localhost:8080/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"identifier":"owner@kumaranauto.in","password":"owner1234"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s localhost:8080/api/v1/reports/owner-home -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

---

## Optional services

Both are **off by default** and the product works fully without them.

### LLM (vLLM) — Coach and Copilot phrasing

Without it, every Coach briefing and Copilot answer falls back to deterministic
text. That text is never wrong; it just reads plainer. To enable:

```bash
# Serve any OpenAI-compatible model
vllm serve Qwen/Qwen2.5-7B-Instruct --port 8000

# backend/.env
LLM_ENABLED=true
LLM_BASE_URL=http://localhost:8000/v1
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
```

### MQTT / EMQX — the sensor seam

Nothing publishes to it yet; it exists so the hardware phase is a data-source
swap rather than a rebuild. The ingest endpoint returns **503** while disabled,
deliberately, so a half-configured broker cannot quietly inject data.

```bash
# backend/.env
MQTT_ENABLED=true
INGEST_TOKEN=<a long random secret, also configured in the EMQX rule>
```

Dashboard at http://localhost:18083. Setup is in
[`infra/emqx/README.md`](infra/emqx/README.md).

---

## Messages: WhatsApp, SMS and browser push

Escalations, the end-of-shift report, the 8 AM owner summary, the 5:45 AM
Coach briefing, RCA requests and count-mismatch alerts all go through one
outbox that the worker sends every 15 seconds and retries with backoff.
WhatsApp is tried first, SMS if WhatsApp is not available or keeps failing,
and browser push to anyone who turned it on with the bell in the header.

Nothing is configured by default. Until it is, every message is still
recorded — as **skipped**, with the reason — in **Settings → Notifications**,
and escalations that reached nobody are marked **failed** rather than sent.

```bash
# backend/.env
WATI_ENABLED=true            WATI_BASE_URL=…  WATI_API_TOKEN=…
MSG91_ENABLED=true           MSG91_AUTH_KEY=… MSG91_TEMPLATE_ID=…
WEBPUSH_ENABLED=true         VAPID_PRIVATE_KEY=$(python scripts/generate_vapid_keys.py | head -1 | cut -d= -f2)
```

Restart the API and the worker after changing these. Who receives what comes
from **Settings → Alert recipients** (T5): `maintenance`/`stores`/`quality`
for escalations by reason, `plant_head` and `owner` for the 30-minute rung
and the owner reports.

---

## Offline tablets

Stops, restarts, counts and quality batches recorded without a connection are
kept on the tablet and synced when the connection returns; the pill in the
header shows what is waiting. Entries whose shift was locked before they
synced are held for the owner in **Settings → Held entries**.

The service worker only runs in a production build (Next's dev assets change
on every save):

```bash
cd frontend && npm run build && npm run start
```

Then in Chrome: visit the grid and quality pages once, DevTools → Network →
**Offline**, record a stop and a count, reload, go back online.

---

## Photos

Action and Factory Memory photos are resized, stripped of metadata and stored
under `backend/storage/<plant>/` (git-ignored). Back that directory up with
the database.

---

## Backups

Take one before any migration against a database you care about:

```bash
mkdir -p backups
docker exec fm-db pg_dump -U factorymanager -d iea_factory_ai -Fc > backups/iea_factory_ai_$(date +%Y%m%d_%H%M).dump

# restore into a fresh database
docker exec -i fm-db psql -U factorymanager -d postgres -c 'CREATE DATABASE iea_restore;'
docker exec -i fm-db pg_restore -U factorymanager -d iea_restore --no-owner < backups/<file>.dump
```

`backups/` is git-ignored.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `connection refused` on 5432 | Postgres is not running (`make infra`), or `.env` points at the wrong port. `db`, `redis` and `emqx` carry `restart: unless-stopped`, so they come back by themselves after a reboot — if they did not, the containers predate that policy: `docker compose up -d db redis` recreates them with it (named volumes keep the data). Note the host also has a native Postgres 18 cluster on 5432; it is disabled, and enabling it would collide with the container |
| `relation "..." does not exist` | `alembic upgrade head` has not been run since the last model change |
| Web loads but every request 401s | API is down, or the token expired — sign in again |
| Empty screens, no data | `python scripts/seed.py` has not been run |
| Escalations never fire | The worker is not running |
| Every list is empty but the data is there | The app role has no plant bound — a script or job querying without `set_plant_scope`, or `DATABASE_URL` pointing at `fm_app` for a seed script (scripts use `MIGRATION_DATABASE_URL`) |
| Recovered Profit changed after an upgrade | Run `python scripts/recompute_recovered_profit.py` (report) then `--apply` to bring every closed Action onto the current rule |
| `password authentication failed for user "fm_app"` | `APP_DB_PASSWORD` and the password in `DATABASE_URL` differ; re-run `alembic upgrade head` or `ALTER ROLE fm_app PASSWORD '…'` |
| Coach says "not enough history" | Correct behaviour below ~10 closed shifts — reseed to get 6 weeks |
| `docker compose` fails pulling images | No Docker Hub access; use Option B |
