"""README.md for the generated dataset folder — facts computed from the data itself."""

from __future__ import annotations

from collections import Counter


def render_readme(manifest: dict, generator) -> str:
    store = generator.store
    rows = store.rows
    counts = manifest["tables"]

    def by(table: str, column: str) -> str:
        tally = Counter(str(r[column]) for r in rows[table])
        return ", ".join(f"{k} {v:,}" for k, v in sorted(tally.items()))

    plants = {p["id"]: p["code"] for p in rows["plants"]}
    per_plant = Counter(plants[r["plant_id"]] for r in rows["downtime_events"])
    shifts_per_plant = Counter(plants[r["plant_id"]] for r in rows["shift_sessions"])
    latest_tier = {}
    for r in sorted(rows["coach_briefings"], key=lambda r: r["delivered_at"]):
        latest_tier[plants[r["plant_id"]]] = r["tier"]
    recovered = Counter()
    for r in rows["actions"]:
        if r["state"] == "verified":
            recovered[plants[r["plant_id"]]] += r["recovered_profit"]
    flagged = sum(1 for r in rows["quality_records"] if r["reconciliation_flagged"])

    lines = [
        "# FactoryManager AI — full-product test dataset",
        "",
        f"**{manifest['total_rows']:,} rows** across all {sum(1 for c in counts.values() if c)} tables of the schema "
        f"(Alembic head `{manifest['schema_revision'].split()[0]}`), for {len(manifest['plants'])} plants, "
        f"with data up to **{manifest['data_stops_at']}**.",
        "",
        "Every rupee figure (downtime cost, scrap cost, baselines, Recovered Profit, OEE snapshots, Kaizen savings) "
        "was computed by `backend/app/kernel` — the same functions the API and worker use — so loading this data and "
        "re-running a report gives the same numbers. Money columns are **integer paise** (÷100 for ₹). "
        "Timestamps are **IST (+05:30)**.",
        "",
        "Regenerate: `cd backend && python scripts/generate_dataset.py` (deterministic ids for a given `--seed`).",
        "",
        "## Formats",
        "",
        "| Folder | Contents |",
        "|---|---|",
        "| `sql/schema.sql` | Plain-PostgreSQL DDL at Alembic head (tables, constraints, indexes, RLS policies), stamped with the revision |",
        "| `sql/factorymanager_data.sql` | One transaction of multi-row `INSERT`s in foreign-key order |",
        "| `sql/00_reset_all_tables.sql` | Optional `TRUNCATE … CASCADE` of every table — **destructive**, for a dev DB only |",
        "| `csv/` | One CSV per table (header row; JSON as text; arrays as `{…}` literals) and `load_csv.sql` |",
        "| `excel/factorymanager_dataset.xlsx` | Every table as a sheet plus a README sheet |",
        "| `forms/<PLANT>/T1…T7_*.xlsx` | Filled onboarding templates in the exact layout `/onboarding` accepts |",
        "| `forms/Readiness_Questionnaire_Responses.xlsx` | The Step-0 questionnaire and every response |",
        "| `manifest.json` | Row counts, seed, plants, demo logins |",
        "",
        "## Loading",
        "",
        "The target must be an **empty** schema at Alembic head — the dataset contains its own `KUMARAN` plant and "
        "demo users, which collide with `scripts/seed.py` data.",
        "",
        "```bash",
        "# A. SQL only — no Python needed",
        "createdb factorymanager_dataset",
        "psql <url> -v ON_ERROR_STOP=1 -f sql/schema.sql",
        "psql <url> -v ON_ERROR_STOP=1 -f sql/factorymanager_data.sql",
        "",
        "# B. Through the project's own migrations",
        "cd backend",
        "createdb factorymanager_dataset            # or reuse a dev DB after 00_reset_all_tables.sql",
        "DATABASE_URL=postgresql+psycopg://factorymanager:factorymanager@localhost:5432/factorymanager_dataset alembic upgrade head",
        "psql postgresql://factorymanager:factorymanager@localhost:5432/factorymanager_dataset -v ON_ERROR_STOP=1 \\",
        "     -f ../datasets/factorymanager_dataset/sql/factorymanager_data.sql",
        "",
        "# C. From CSV (schema created by A or B first)",
        "cd ../datasets/factorymanager_dataset/csv && psql <url> -f load_csv.sql",
        "",
        "# Then prove it matches the product's code (read-only, exits non-zero on mismatch)",
        "cd backend && DATABASE_URL=postgresql+psycopg://…/factorymanager_dataset python scripts/verify_dataset.py",
        "```",
        "",
        "## Plants",
        "",
        "| Code | Plant | Costing | Lots | Shifts | Live from | Shift sessions | Downtime events | Coach tier now | Verified Recovered Profit |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|",
    ]
    for p in manifest["plants"]:
        code = p["code"]
        lines.append(
            f"| {code} | {p['name']}, {p['location']} | {p['costing_mode']} | {p['lot_granularity']} | {len(p['shifts'])} | {p['go_live']} | "
            f"{shifts_per_plant[code]:,} | {per_plant[code]:,} | {latest_tier.get(code, 0)} | ₹{recovered[code] / 100:,.0f} |"
        )

    lines += [
        "",
        "## Rows per table",
        "",
        "| Table | Rows |",
        "|---|---:|",
        *[f"| `{name}` | {count:,} |" for name, count in counts.items()],
        f"| **Total** | **{manifest['total_rows']:,}** |",
        "",
        "## Product scenarios covered",
        "",
        "Each line below is present in the data, so the screen or rule it names can be tested against real rows.",
        "",
        f"- **Shift Start / Closure Lock (Screens 1, 6)** — shift status: {by('shift_sessions', 'status')}. The last shift of each plant is still open at the data cut-off, with a stop that has no reason code and stations with no count yet (closure blockers). Closed shifts carry a frozen KPI `summary`; ~2% were closed with an owner override (logged in `audit_log`).",
        f"- **Machine grid & live ticker (Screens 2, 3)** — downtime status: {by('downtime_events', 'status')}. Two stations per plant are stopped right now, one without a reason yet. Sources: {by('downtime_events', 'source')} (`system` = changeover gaps captured by Change Job, §14.3; `sensor` = pulse counters on SAKTHI headers from 1 Sep).",
        f"- **Escalation ladder (5/15/30 min)** — {counts['escalations']:,} rungs: {by('escalations', 'status')}; channels {by('escalations', 'channel')}. SAKTHI has no WhatsApp approval, so its rungs go by SMS; ~3% of WhatsApp sends fail and fall back to SMS.",
        f"- **Production entry (Screen 4)** — {counts['production_counts']:,} hourly counts; ~1% corrected before close (audit trail); ~3% of station-shifts padded by the supervisor.",
        f"- **Quality entry & cross-verification (Screen 5)** — disposition: {by('quality_records', 'disposition')}; {flagged:,} records flagged by count reconciliation. Scrap is costed with `kernel.scrap_cost` (material + processing of completed ops + lost contribution × BF).",
        f"- **Job Order spine & traceability (§3, §14.2-14.3)** — job orders: {by('job_orders', 'status')}; lots: {by('lots', 'granularity')}; jobs span shifts, days and stations; Change Job sessions link via `previous_session_id`; stage completions through every routing op, dispatch with vehicle numbers.",
        "- **Costing modes & effective-dated rates** — KUMARAN (standard) corrects the CNC machine rate on 13 Jul and overhead on 3 Aug; events before keep the old snapshot (history never restates). SRIVARI (basic) moves from default library rates to derived rates on 6 Jul. Margin-band fallback is used for band-only parts. Every `cost_rates.derivation` reproduces its rate through the kernel formula.",
        f"- **Actions & PDCA (§5)** — {counts['actions']:,} actions. Trigger: {by('actions', 'trigger')}. State: {by('actions', 'state')}. Includes overdue, delayed (with reasons), cancelled structural and duplicate actions, RCA-routed actions with completed 5-Whys and AI suggestions from the plant's own memory, and fixes that did not hold (automatically reopened).",
        f"- **Recovered Profit (§5.6)** — {counts['recovered_profit_entries']:,} weekly ledger rows, frozen baselines, 2-consecutive-week rule, never negative; accrual stops on reopen.",
        f"- **Factory Memory (Layer 4)** — {counts['memory_entries']:,} entries (closures plus entries imported from the old maintenance register).",
        f"- **AI Coach (§14.6)** — {counts['coach_briefings']:,} briefings; tier counts {dict(sorted(Counter(r['tier'] for r in rows['coach_briefings']).items()))} — silent in the first shifts, rule-based, then pattern tier after 4 weeks and deep tier after 12.",
        f"- **Copilot (Layer 3)** — {counts['copilot_queries']:,} questions; intents {by('copilot_queries', 'intent')}; English and Tamil; web and WhatsApp; pattern questions refused while the plant is below Tier 2.",
        f"- **Onboarding (§4.1, §15)** — {counts['readiness_assessments']:,} readiness questionnaires (prospects, live plants and abandoned ones) scored by `app.ingest.readiness`; {counts['template_uploads']:,} template uploads including failed first attempts.",
        f"- **Plugins (Layer 5)** — compliance clauses: {by('compliance_clauses', 'status')}; {counts['audit_packs']} audit packs; Kaizen: {by('kaizen_ideas', 'status')}; {counts['operator_skills']:,} skill-matrix entries; training needs: {by('training_needs', 'kind')}.",
        "",
        "## Demo logins",
        "",
        "Floor roles sign in with a phone number; owners and platform accounts with email.",
        "",
        "| Plant | Name | Role | Login | Password |",
        "|---|---|---|---|---|",
        *[
            f"| {c['plant']} | {c['name']} | {c['role']} | `{c['login']}` | `{c['password']}` |"
            for c in manifest["demo_logins"]
        ],
        "",
        "## Notes",
        "",
        "- Customer names for SRIVARI, KOVAI and SAKTHI are fictional; KUMARAN reuses the customers from `scripts/seed.py`. Phone numbers are placeholders.",
        "- Tamil labels on reason and defect codes should be reviewed by a native speaker before a real plant sees them.",
        "- `password_hash` values are argon2 hashes of the passwords above; they differ between regenerations (random salts). All other ids and values are stable for a given seed.",
        "",
    ]
    return "\n".join(lines)
