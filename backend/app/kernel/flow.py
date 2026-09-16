"""Lot flow through a routing — §14.2, implemented once.

    "The system therefore always knows, per lot: 500 through machining, 430
     through assembly (70 in WIP), 380 passed quality (20 rejected, costed), 380
     despatched. Where quantities disagree beyond tolerance, it flags — that gap
     is where parts silently disappear in most plants today."

The hard part is telling a *gap* from *work in progress*. Seventy parts between
machining and assembly on a Tuesday is a buffer, not a loss. So three rules:

1. **Parts between stages are WIP**, reported but never flagged, while the lot
   is still moving.
2. **More parts reaching a stage than left the previous one is always a flag.**
   Parts cannot appear from nowhere; either a count is wrong or a lot got mixed.
3. **Parts that passed the last check but were never despatched are flagged
   only once despatch has been recorded** — that is the moment the lot is
   supposed to be complete, and the difference is parts that left the building
   some other way, or never existed.

Pure: no database, no clock. The trace service feeds it stage totals.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .kpi import throughput_minutes
from .money import D


@dataclass(frozen=True, slots=True)
class StageTotals:
    operation_code: str
    section: str  # machining | assembly | quality | dispatch
    completed: int  # parts passed onward from this stage
    rejected: int  # parts rejected at this stage
    first_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    recorded: bool = True  # False when nothing has been logged at this stage yet


@dataclass(frozen=True, slots=True)
class FlowStage:
    operation_code: str
    section: str
    received: int  # what the previous stage passed on (the lot size for the first)
    completed: int
    rejected: int
    wip_before: int  # received − (completed + rejected); negative is impossible
    recorded: bool
    first_started_at: datetime | None
    last_completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class FlowFlag:
    operation_code: str
    kind: str  # "appeared" | "unaccounted_at_gate"
    gap: int
    message: str


@dataclass(frozen=True, slots=True)
class LotFlow:
    stages: tuple[FlowStage, ...]
    flags: tuple[FlowFlag, ...] = field(default=())
    despatched: int = 0
    complete: bool = False
    #: The first stage the lot has not fully passed — "where it physically sits".
    position: str | None = None
    throughput_minutes: Decimal | None = None
    total_rejected: int = 0


def tolerance(parts: int, *, tolerance_parts: int, tolerance_fraction: Decimal) -> int:
    """The same shape as the count reconciliation in §6: the larger of a fixed
    number of parts and a share of the quantity involved."""
    return max(tolerance_parts, int(D(parts) * D(tolerance_fraction)))


def lot_flow(
    stages: Sequence[StageTotals],
    *,
    lot_quantity: int | None = None,
    tolerance_parts: int = 5,
    tolerance_fraction: Decimal = D("0.02"),
) -> LotFlow:
    """Walk a lot through its routing and say where it is and what went missing."""
    flow: list[FlowStage] = []
    flags: list[FlowFlag] = []

    received = lot_quantity if lot_quantity is not None else None
    for index, stage in enumerate(stages):
        if index == 0 and received is None:
            # With no planned lot size, the first stage's own output is the
            # starting count: there is nothing upstream to reconcile against.
            received = stage.completed + stage.rejected

        handled = stage.completed + stage.rejected
        wip = (received or 0) - handled
        flow.append(
            FlowStage(
                operation_code=stage.operation_code,
                section=stage.section,
                received=received or 0,
                completed=stage.completed,
                rejected=stage.rejected,
                wip_before=wip,
                recorded=stage.recorded,
                first_started_at=stage.first_started_at,
                last_completed_at=stage.last_completed_at,
            )
        )

        # Rule 2: parts cannot appear from nowhere. The first stage is exempt
        # when it is measured against a planned lot size — a job producing more
        # than planned is over-production, not a counting error.
        allowed = tolerance(
            received or 0,
            tolerance_parts=tolerance_parts,
            tolerance_fraction=tolerance_fraction,
        )
        if index > 0 and stage.recorded and -wip > allowed:
            flags.append(
                FlowFlag(
                    operation_code=stage.operation_code,
                    kind="appeared",
                    gap=-wip,
                    message=(
                        f"{-wip} more parts reached {stage.operation_code} than left "
                        f"{stages[index - 1].operation_code}. A count is wrong or lots "
                        "have been mixed."
                    ),
                )
            )

        received = stage.completed

    despatch = next((s for s in reversed(flow) if s.section == "dispatch"), None)
    despatched = despatch.completed if despatch and despatch.recorded else 0
    complete = bool(despatch and despatch.recorded)

    # Rule 3: only once the lot has been despatched is a shortfall at the gate a
    # loss rather than parts still waiting to go.
    if complete and despatch is not None:
        allowed = tolerance(
            despatch.received,
            tolerance_parts=tolerance_parts,
            tolerance_fraction=tolerance_fraction,
        )
        if despatch.wip_before > allowed:
            flags.append(
                FlowFlag(
                    operation_code=despatch.operation_code,
                    kind="unaccounted_at_gate",
                    gap=despatch.wip_before,
                    message=(
                        f"{despatch.wip_before} parts passed the last check but were "
                        "never despatched. This is the gap between QC and the gate "
                        "where parts silently disappear."
                    ),
                )
            )

    # Where the lot physically sits. A despatched lot is not "at" any stage —
    # even one with a shortfall at the gate has left; the shortfall is a flag,
    # not a location.
    position = None
    if not complete:
        for stage in flow:
            if not stage.recorded or stage.wip_before > 0:
                position = stage.operation_code
                break

    first = next((s.first_started_at for s in flow if s.first_started_at), None)
    last = despatch.last_completed_at if complete and despatch else None

    return LotFlow(
        stages=tuple(flow),
        flags=tuple(flags),
        despatched=despatched,
        complete=complete,
        position=position,
        throughput_minutes=(
            throughput_minutes(first_operation_start=first, last_operation_completion=last)
            if first and last
            else None
        ),
        total_rejected=sum(s.rejected for s in flow),
    )


@dataclass(frozen=True, slots=True)
class PartFlow:
    part_number: str
    flow: LotFlow


def combine_part_flows(
    parts: Sequence[PartFlow], *, throughput_minutes: Decimal | None = None
) -> LotFlow:
    """One flow for a batch lot that holds several parts.

    Each part is walked through *its own* routing first. They cannot be merged by
    operation code: in the same batch, "Op30" is despatch for a two-step part and
    inspection for a three-step one, and merging them invents shortfalls at the
    gate that never happened.

    Combined, conservatively:

    * flags from every part, each naming its part;
    * **complete only when every part is complete** — a batch with one part
      despatched and another still at inspection has not left the building;
    * the position is the first part still moving, named with its part.
    """
    if len(parts) == 1:
        return parts[0].flow

    flags: list[FlowFlag] = []
    for part in parts:
        for flag in part.flow.flags:
            flags.append(
                FlowFlag(
                    operation_code=f"{part.part_number} {flag.operation_code}",
                    kind=flag.kind,
                    gap=flag.gap,
                    message=f"{part.part_number}: {flag.message}",
                )
            )

    complete = bool(parts) and all(part.flow.complete for part in parts)
    moving = next((part for part in parts if not part.flow.complete), None)

    return LotFlow(
        stages=(),
        flags=tuple(flags),
        despatched=sum(part.flow.despatched for part in parts),
        complete=complete,
        position=(
            f"{moving.part_number} {moving.flow.position}"
            if moving is not None and moving.flow.position
            else None
        ),
        throughput_minutes=throughput_minutes if complete else None,
        total_rejected=sum(part.flow.total_rejected for part in parts),
    )
