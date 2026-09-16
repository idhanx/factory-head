"""Prove a loaded dataset agrees with the product's own code.

    DATABASE_URL=postgresql+psycopg://…/factorymanager_dataset python scripts/verify_dataset.py

Read-only: every service call runs inside a transaction that is rolled back.
Checks, per plant:

* row counts match ``manifest.json``;
* every closed downtime event's ``cost_total`` re-computes from its own frozen
  snapshot through ``kernel.downtime_event_cost``;
* every closed shift's frozen ``summary`` equals ``reports.shift_kpis`` now;
* the hourly worker's ``accrue_recovered_profit`` reproduces every stored
  Recovered Profit figure and PDCA state;
* every lot (job, shift-batch and day-batch) walks through ``traceability.flow_for`` (``kernel.lot_flow``)
  with no 'appeared' or 'unaccounted at gate' flags, and the WIP board runs;
* Owner Home, the machine grid, the closure lock, the Coach facts and a Copilot
  question all run without error;
* the demo logins verify against their stored hashes.

Exits non-zero on any mismatch.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, text

from app.ai import copilot, facts
from app.core.db import AdminSessionFactory as SessionFactory
from app.core.db import set_plant_scope
from app.core.security import verify_password
from app.kernel import downtime_event_cost, format_inr
from app.models import (
    Action,
    Base,
    DowntimeEvent,
    DowntimeStatus,
    Lot,
    PdcaState,
    Plant,
    ShiftSession,
    ShiftSessionStatus,
    User,
)
from app.services import actions as action_service
from app.services import reports, traceability
from app.services import shifts as shift_service
from app.services.costing import snapshot_from_row

DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "datasets"
    / "factorymanager_dataset"
    / "manifest.json"
)
SUMMARY_KEYS = (
    "oee",
    "availability",
    "performance",
    "quality",
    "total_count",
    "good_count",
    "reject_count",
    "target",
    "recoverable_loss",
    "structural_loss",
    "breakdown_count",
)


async def verify(manifest: dict) -> int:
    failures = 0
    as_of = date.fromisoformat(manifest["as_of"])

    async with SessionFactory() as session:
        for table, expected in manifest["tables"].items():
            if table not in Base.metadata.tables:
                raise SystemExit(
                    f"manifest names a table the schema does not have: {table!r}"
                )
            # Safe: the name was just checked against the model metadata.
            actual = await session.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
            if actual != expected:
                print(f"  ✗ {table}: {actual} rows, manifest says {expected}")
                failures += 1
        print(
            f"row counts ........... {'ok' if not failures else 'MISMATCH'} ({sum(manifest['tables'].values()):,} rows)"
        )

        logins = [
            c for c in manifest.get("demo_logins", []) if c["plant"] in ("KUMARAN", "ALL")
        ]
        for login in logins:
            column = User.email if "@" in login["login"] else User.phone
            user = await session.scalar(select(User).where(column == login["login"]))
            if user is None or not verify_password(login["password"], user.password_hash):
                print(f"  ✗ login {login['login']} does not verify")
                failures += 1
        print(f"demo logins .......... checked {len(logins)}")

        plant_ids = (
            await session.scalars(select(Plant.id).order_by(Plant.go_live_date))
        ).all()
        for plant_id in plant_ids:
            plant = await session.get(Plant, plant_id)
            await set_plant_scope(session, plant.id)
            print(f"\n[{plant.code}] {plant.name}")

            events = (
                await session.scalars(
                    select(DowntimeEvent).where(
                        DowntimeEvent.plant_id == plant.id,
                        DowntimeEvent.status == DowntimeStatus.CLOSED,
                    )
                )
            ).all()
            wrong = 0
            for event in events:
                minutes = Decimal(
                    (event.ended_at - event.started_at).total_seconds()
                ) / Decimal(60)
                if (
                    downtime_event_cost(minutes, snapshot_from_row(event)).total
                    != event.cost_total
                ):
                    wrong += 1
            print(
                f"  downtime costs ..... {len(events):,} events, {wrong} differ from kernel"
            )
            failures += wrong

            closed = (
                await session.scalars(
                    select(ShiftSession).where(
                        ShiftSession.plant_id == plant.id,
                        ShiftSession.status == ShiftSessionStatus.CLOSED,
                    )
                )
            ).all()
            wrong = 0
            for shift in closed:
                live = await reports.shift_kpis(session, plant=plant, shift_session=shift)
                frozen = shift.summary or {}
                diffs = [
                    k
                    for k in SUMMARY_KEYS
                    if abs(float(getattr(live, k)) - float(frozen.get(k, -1))) > 1e-4
                ]
                if diffs:
                    wrong += 1
                    if wrong <= 3:
                        print(
                            f"    ✗ {shift.business_date} {diffs}: live {[getattr(live, k) for k in diffs]} frozen {[frozen.get(k) for k in diffs]}"
                        )
            print(
                f"  shift summaries .... {len(closed):,} closed shifts, {wrong} differ from shift_kpis"
            )
            failures += wrong

            rows = (
                await session.scalars(
                    select(Action).where(
                        Action.plant_id == plant.id,
                        Action.state.in_([PdcaState.MONITORING, PdcaState.VERIFIED]),
                    )
                )
            ).all()
            stored = {a.id: (a.recovered_profit, a.state, a.sustained_weeks) for a in rows}
            wrong = 0
            for action in rows:
                await action_service.accrue_recovered_profit(
                    session, plant=plant, action=action, as_of=as_of
                )
                if (
                    action.recovered_profit,
                    action.state,
                    action.sustained_weeks,
                ) != stored[action.id]:
                    wrong += 1
            print(f"  recovered profit ... {len(rows)} actions re-accrued, {wrong} differ")
            failures += wrong
            await session.rollback()

            plant = await session.get(Plant, plant_id)
            await set_plant_scope(session, plant.id)
            lots = (
                await session.scalars(select(Lot).where(Lot.plant_id == plant.id))
            ).all()
            flags: Counter[str] = Counter()
            despatched = 0
            for lot in lots:
                # Job lots and multi-part batch lots alike: the service traces each
                # part along its own routing (kernel.combine_part_flows).
                flow, _ = await traceability.flow_for(session, plant, lot)
                flags.update(flag.kind for flag in flow.flags)
                despatched += flow.complete
            board = await traceability.wip_board(session, plant=plant)
            print(
                f"  lot traceability ... {len(lots)} lots ({despatched} despatched), "
                f"{len(board)} on the WIP board, flags: {dict(flags) or 'none'}"
            )
            failures += sum(flags.values())

            home = await reports.owner_home(session, plant=plant, as_of=as_of)
            live_shift = await session.scalar(
                select(ShiftSession).where(
                    ShiftSession.plant_id == plant.id,
                    ShiftSession.status == ShiftSessionStatus.OPEN,
                )
            )
            grid = await reports.machine_grid(
                session, plant=plant, shift_session=live_shift
            )
            blockers = (
                await shift_service.closure_blockers(session, live_shift)
                if live_shift
                else None
            )
            bundle = await facts.build_shift_facts(session, plant=plant, as_of=as_of)
            answer = await copilot.ask(
                session,
                plant=plant,
                question="Why does the bottleneck keep stopping this week?",
                as_of=as_of,
                use_llm=False,
            )
            verified = await session.scalar(
                select(func.count(Action.id)).where(
                    Action.plant_id == plant.id, Action.state == PdcaState.VERIFIED
                )
            )
            print(
                f"  owner home ......... recovered {format_inr(home.recovered_profit)} from {verified} verified actions, ROI {home.running_roi}x, month-to-date loss {format_inr(home.lost_month_to_date)}"
            )
            print(
                f"  live shift ......... {len(grid)} tiles; closure blockers: {sorted({b.kind for b in blockers.blockers}) if blockers else 'none'}"
            )
            print(
                f"  coach / copilot .... tier {bundle.tier}; copilot intent '{answer.intent}' answered"
            )
            await session.rollback()

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    failures = asyncio.run(verify(manifest))
    print("\nALL CHECKS PASSED" if not failures else f"\n{failures} CHECK(S) FAILED")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
