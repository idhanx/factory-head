"""Re-measure every closed Action under the current Recovered Profit rules.

    python scripts/recompute_recovered_profit.py            # report only
    python scripts/recompute_recovered_profit.py --apply    # write the results

The worker re-measures Actions in monitoring and verified every hour, but it
never touches a reopened Action again — so a rule change leaves reopened
Actions carrying credit worked out under the old rule. This brings every closed
Action onto one rule: credit from the week after the two sustained weeks, kept
up to the moment an Action reopened, never twice for the same loss.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.db import AdminSessionFactory, set_plant_scope
from app.kernel import format_inr
from app.models import Action, PdcaState, Plant, RecoveredProfitEntry
from app.services import actions as action_service
from app.services.clock import local_date, plant_today


async def main(apply: bool) -> None:
    async with AdminSessionFactory() as session:
        for plant in (await session.scalars(select(Plant).order_by(Plant.code))).all():
            await set_plant_scope(session, plant.id)
            actions = (
                await session.scalars(
                    select(Action)
                    .where(
                        Action.plant_id == plant.id,
                        Action.closed_at.is_not(None),
                        Action.state.in_(
                            [PdcaState.MONITORING, PdcaState.VERIFIED, PdcaState.REOPENED]
                        ),
                    )
                    .order_by(Action.closed_at)  # earliest first: it owns shared weeks
                )
            ).all()
            before = sum(
                row.saving
                for row in (
                    await session.scalars(
                        select(RecoveredProfitEntry).where(
                            RecoveredProfitEntry.plant_id == plant.id,
                            RecoveredProfitEntry.counted.is_(True),
                        )
                    )
                ).all()
            )
            reopened = 0
            for action in actions:
                action.recovered_profit_prior = 0
                if action.state is PdcaState.REOPENED:
                    # Measure the weeks up to the reopen, then stop there.
                    until = local_date(plant, action.reopened_at or action.closed_at)
                    action.state = PdcaState.MONITORING
                    await action_service.accrue_recovered_profit(
                        session, plant=plant, action=action, as_of=until
                    )
                    for row in (
                        await session.scalars(
                            select(RecoveredProfitEntry).where(
                                RecoveredProfitEntry.action_id == action.id,
                                RecoveredProfitEntry.week_start >= until,
                            )
                        )
                    ).all():
                        row.counted = False
                    action.state = PdcaState.REOPENED
                    action.recovered_profit_prior = action.recovered_profit
                    continue
                today = plant_today(plant)
                await action_service.accrue_recovered_profit(
                    session, plant=plant, action=action, as_of=today
                )
                if await action_service.reopen_if_recurred(
                    session, plant=plant, action=action, as_of=today
                ):
                    reopened += 1
            await session.flush()
            after = sum(
                row.saving
                for row in (
                    await session.scalars(
                        select(RecoveredProfitEntry).where(
                            RecoveredProfitEntry.plant_id == plant.id,
                            RecoveredProfitEntry.counted.is_(True),
                        )
                    )
                ).all()
            )
            print(
                f"{plant.code:10} closed actions {len(actions):4}  newly reopened {reopened:3}  "
                f"recovered {format_inr(before):>12} -> {format_inr(after):>12}"
            )
        if apply:
            await session.commit()
            print("Applied.")
        else:
            await session.rollback()
            print("Report only — nothing written. Re-run with --apply to write.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
