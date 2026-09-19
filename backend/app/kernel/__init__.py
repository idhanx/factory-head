"""``app.kernel`` — the calculation kernel.

One implementation of every formula in spec §8. Pure: no database handle, no
clock, no network, no config lookup. Inputs in, numbers out.

The API layer, the background workers and the AI fact-builder all import from
here, which is what guarantees the property the spec demands — that two screens
can never show two different OEE numbers for the same shift.
"""

from .cost import (
    annualised_projection,
    blended_machine_rate,
    contribution_margin,
    downtime_event_cost,
    labour_cost_rate,
    live_ticker_rate_per_minute,
    machine_cost_rate,
    overhead_rate,
    rank_losses,
    recoverable_loss,
    rework_cost,
    scrap_cost,
    structural_loss,
)
from .flow import (
    FlowFlag,
    FlowStage,
    LotFlow,
    PartFlow,
    StageTotals,
    combine_part_flows,
    lot_flow,
)
from .kaizen import (
    PLUGIN_WEIGHTS,
    STANDALONE_WEIGHTS,
    Board,
    CostOfDoingNothing,
    ImpactStars,
    Level,
    LevelStanding,
    PointsEvent,
    PriorityScore,
    SavingSplit,
    SavingType,
    ScoreInputs,
    ScoreWeights,
    bottleneck_question_applies,
    classify,
    combine,
    cost_of_doing_nothing,
    is_quick_win,
    level_for,
    participation_percent,
    payback_months,
    points_for,
    priority_score,
    roi_percent,
    trend_factor,
    value_bonus,
    verdict_for,
)
from .kpi import (
    StationRun,
    aggregate_oee,
    availability,
    breakdown_minutes,
    compute_oee,
    count_breakdowns,
    count_reconciliation,
    downtime_percent,
    first_pass_yield,
    mtbf,
    mttr,
    performance,
    planned_production_time,
    ppm,
    productivity,
    quality_rate,
    schedule_adherence,
    throughput_minutes,
)
from .money import D, Paise, floor_int, format_inr, round_paise, rupees, to_rupees
from .profit import (
    RecoveredProfitResult,
    WeeklyLoss,
    fix_failed,
    freeze_baseline,
    plant_recovered_profit,
    recovered_profit,
    running_roi,
)
from .rules import (
    CLOSABLE_STATES,
    ESCALATION_LADDER,
    MANUAL_TRANSITIONS,
    PDCA_PHASE,
    ActionThresholds,
    ActionTrigger,
    ClosureCheck,
    CoachTier,
    DataSufficiency,
    EscalationLevel,
    EventForTrigger,
    PdcaState,
    ShiftClosureBlocker,
    TriggeredAction,
    can_transition,
    check_shift_closure,
    coach_tier,
    escalation_schedule,
    evaluate_action_triggers,
    route_by_severity,
)
from .types import (
    FORMULA_VERSION,
    CostingMode,
    CostRates,
    CostSnapshot,
    DowntimeCost,
    DowntimeSlice,
    LossLine,
    MarginBand,
    OeeResult,
    OeeTreatment,
    PartEconomics,
    RankedLosses,
    Recoverability,
    ScrapCost,
)

__all__ = [n for n in dir() if not n.startswith("_")]
