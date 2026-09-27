"""Request and response models.

Money crosses the API as **integer paise** in a ``*_paise`` field, always
accompanied by a pre-formatted ``*_display`` string. The client never does
currency arithmetic and never picks a rounding rule — there is one money
formatter in the system and it lives in the kernel.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth --------------------------------------------------------------------


class LoginRequest(BaseModel):
    # A supervisor signs in with a phone number and a PIN; an owner with email.
    identifier: str = Field(description="Email or phone number")
    password: str
    plant_code: str | None = None


class PlantMembership(BaseModel):
    plant_id: uuid.UUID
    plant_name: str
    plant_code: str
    roles: list[str]


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID
    full_name: str
    plant_id: uuid.UUID
    plant_name: str
    roles: list[str]
    memberships: list[PlantMembership]
    preferred_language: str = "en"


class RefreshRequest(BaseModel):
    refresh_token: str
    plant_id: uuid.UUID | None = None


# --- masters -----------------------------------------------------------------
#
# Reads are open to every role in the plant; writes are for the people who own
# the configuration (owner, consultant, admin). Nothing is hard-deleted: a
# station that ran for two years keeps its history, so it is deactivated.

Section = Literal["machining", "assembly", "quality", "dispatch"]
EscalationRouteName = Literal["maintenance", "stores", "quality", "production", "none"]
RoleName = Literal["owner", "supervisor", "quality", "maintenance", "stores", "consultant", "admin"]
HHMM = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class StationOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    station_type: str | None
    section: str
    line: str | None
    zone: str | None
    is_bottleneck: bool
    bottleneck_factor: float
    cost_family_code: str | None = None
    is_active: bool = True


class StationIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    station_type: str | None = Field(default=None, max_length=100)
    section: Section = "machining"
    line: str | None = Field(default=None, max_length=64)
    zone: str | None = Field(default=None, max_length=64)
    is_bottleneck: bool = False
    #: §8.2 — 1.0 for the bottleneck or where unknown; 0-0.5 for a buffered machine.
    bottleneck_factor: float = Field(default=1.0, ge=0, le=1)
    cost_family_code: str | None = Field(default=None, max_length=64)
    is_active: bool = True


class StationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    station_type: str | None = Field(default=None, max_length=100)
    section: Section | None = None
    line: str | None = Field(default=None, max_length=64)
    zone: str | None = Field(default=None, max_length=64)
    is_bottleneck: bool | None = None
    bottleneck_factor: float | None = Field(default=None, ge=0, le=1)
    cost_family_code: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None


class ReasonCodeOut(ORMModel):
    id: uuid.UUID
    code: str
    label: str
    label_ta: str | None
    recoverability: str
    oee_treatment: str
    is_breakdown: bool
    escalation_route: str
    safety_flag: bool
    display_order: int
    is_active: bool = True


class ReasonCodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    label: str = Field(min_length=1, max_length=200)
    label_ta: str | None = Field(default=None, max_length=200)
    recoverability: Literal["R", "S"]
    oee_treatment: Literal["planned", "unplanned"] = "unplanned"
    is_breakdown: bool = False
    escalation_route: EscalationRouteName = "production"
    safety_flag: bool = False
    display_order: int = Field(default=0, ge=0, le=10_000)
    is_active: bool = True


class ReasonCodePatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    label_ta: str | None = Field(default=None, max_length=200)
    recoverability: Literal["R", "S"] | None = None
    oee_treatment: Literal["planned", "unplanned"] | None = None
    is_breakdown: bool | None = None
    escalation_route: EscalationRouteName | None = None
    safety_flag: bool | None = None
    display_order: int | None = Field(default=None, ge=0, le=10_000)
    is_active: bool | None = None


class DefectCodeOut(ORMModel):
    id: uuid.UUID
    code: str
    label: str
    label_ta: str | None
    part_family: str | None
    display_order: int = 0
    is_active: bool = True


class DefectCodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    label_ta: str | None = Field(default=None, max_length=200)
    part_family: str | None = Field(default=None, max_length=100)
    display_order: int = Field(default=0, ge=0, le=10_000)
    is_active: bool = True


class DefectCodePatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    label_ta: str | None = Field(default=None, max_length=200)
    part_family: str | None = Field(default=None, max_length=100)
    display_order: int | None = Field(default=None, ge=0, le=10_000)
    is_active: bool | None = None


class PartOut(ORMModel):
    id: uuid.UUID
    part_number: str
    description: str | None
    customer_name: str | None
    part_family: str | None
    is_active: bool = True


class PartDetailOut(PartOut):
    """Owner-confidential economics (§4.1: margins are set with the owner privately)."""

    contribution_margin_rupees: float | None = None
    margin_band: str | None = None
    material_cost_rupees: float = 0


class PartIn(BaseModel):
    part_number: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    customer_name: str | None = Field(default=None, max_length=200)
    part_family: str | None = Field(default=None, max_length=100)
    contribution_margin_rupees: float | None = Field(default=None, ge=0, le=10_000_000)
    margin_band: Literal["L", "M", "H"] | None = None
    material_cost_rupees: float = Field(default=0, ge=0, le=10_000_000)
    is_active: bool = True


class PartPatch(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    customer_name: str | None = Field(default=None, max_length=200)
    part_family: str | None = Field(default=None, max_length=100)
    contribution_margin_rupees: float | None = Field(default=None, ge=0, le=10_000_000)
    margin_band: Literal["L", "M", "H"] | None = None
    material_cost_rupees: float | None = Field(default=None, ge=0, le=10_000_000)
    is_active: bool | None = None


class OperatorOut(ORMModel):
    id: uuid.UUID
    name: str
    employee_code: str | None
    is_active: bool = True


class OperatorIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    employee_code: str | None = Field(default=None, max_length=64)
    is_active: bool = True


class OperatorPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    employee_code: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None


class ShiftOut(BaseModel):
    id: uuid.UUID
    name: str
    start_time: str
    end_time: str
    break_minutes: int
    weekly_off: str | None = None
    display_order: int = 0
    is_active: bool = True


class ShiftIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    start_time: str = HHMM
    end_time: str = HHMM
    break_minutes: int = Field(default=0, ge=0, le=240)
    weekly_off: str | None = Field(default=None, max_length=32)
    display_order: int = Field(default=0, ge=0, le=100)
    is_active: bool = True


class ShiftPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    start_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    break_minutes: int | None = Field(default=None, ge=0, le=240)
    weekly_off: str | None = Field(default=None, max_length=32)
    display_order: int | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None


class AlertRecipientOut(ORMModel):
    id: uuid.UUID
    name: str
    role_tag: str
    whatsapp_number: str | None
    escalation_level: int
    is_active: bool


class AlertRecipientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role_tag: Literal["maintenance", "stores", "quality", "production", "plant_head", "owner"]
    whatsapp_number: str | None = Field(default=None, max_length=20, pattern=r"^\+?[0-9 ]{6,20}$")
    escalation_level: int = Field(default=2, ge=1, le=3)
    is_active: bool = True


class AlertRecipientPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    role_tag: Literal["maintenance", "stores", "quality", "production", "plant_head", "owner"] | None = None
    whatsapp_number: str | None = Field(default=None, max_length=20, pattern=r"^\+?[0-9 ]{6,20}$")
    escalation_level: int | None = Field(default=None, ge=1, le=3)
    is_active: bool | None = None


class CostRateOut(BaseModel):
    id: uuid.UUID
    scope: str
    reference: str
    machine_rate_rupees: float
    labour_rate_rupees: float
    overhead_rate_rupees: float
    source: str
    effective_from: date


class CostRateIn(BaseModel):
    """A new effective-dated rate. Past events keep the rate they were costed with."""

    scope: Literal["cost_family", "section", "plant"]
    reference: str = Field(min_length=1, max_length=64)
    machine_rate_rupees: float = Field(default=0, ge=0, le=1_000_000)
    labour_rate_rupees: float = Field(default=0, ge=0, le=1_000_000)
    overhead_rate_rupees: float = Field(default=0, ge=0, le=1_000_000)
    source: Literal["default", "derived", "measured"] = "derived"
    effective_from: date | None = None


class JobOrderOut(BaseModel):
    id: uuid.UUID
    job_number: str
    part_id: uuid.UUID
    part_number: str | None = None
    customer_name: str | None
    planned_quantity: int
    status: str
    due_date: date | None = None
    notes: str | None = None


class JobOrderIn(BaseModel):
    part_id: uuid.UUID
    planned_quantity: int = Field(gt=0, le=10_000_000)
    job_number: str | None = Field(default=None, min_length=1, max_length=64)
    due_date: date | None = None
    customer_name: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class JobOrderPatch(BaseModel):
    planned_quantity: int | None = Field(default=None, gt=0, le=10_000_000)
    due_date: date | None = None
    status: Literal["planned", "paused", "completed", "cancelled"] | None = None
    notes: str | None = Field(default=None, max_length=2000)


class JobOrderRepeat(BaseModel):
    planned_quantity: int | None = Field(default=None, gt=0, le=10_000_000)
    due_date: date | None = None


# --- administration ------------------------------------------------------------


class AdminUserOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None
    roles: list[str]
    is_active: bool
    last_login_at: datetime | None


class AdminUserIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(default=None, max_length=20, pattern=r"^\+?[0-9]{6,20}$")
    password: str = Field(min_length=8, max_length=128)
    roles: list[RoleName] = Field(min_length=1)


class AdminUserPatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(default=None, max_length=20, pattern=r"^\+?[0-9]{6,20}$")
    roles: list[RoleName] | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class PlantSettingsOut(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    location: str | None
    timezone: str
    costing_mode: str
    action_cost_threshold_rupees: float
    action_duration_minutes: int
    recurrence_count: int
    recurrence_window_shifts: int
    reconciliation_tolerance_parts: int
    reconciliation_tolerance_percent: float
    monthly_fee_rupees: float
    go_live_date: date | None
    no_blame_until: date | None
    action_due_days: int = 3


class PlantSettingsPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)
    costing_mode: Literal["basic", "standard"] | None = None
    action_cost_threshold_rupees: float | None = Field(default=None, gt=0, le=10_000_000)
    action_duration_minutes: int | None = Field(default=None, gt=0, le=1440)
    recurrence_count: int | None = Field(default=None, ge=2, le=50)
    recurrence_window_shifts: int | None = Field(default=None, ge=1, le=100)
    reconciliation_tolerance_parts: int | None = Field(default=None, ge=0, le=100_000)
    reconciliation_tolerance_percent: float | None = Field(default=None, ge=0, le=100)
    monthly_fee_rupees: float | None = Field(default=None, ge=0, le=100_000_000)
    go_live_date: date | None = None
    no_blame_until: date | None = None
    action_due_days: int | None = Field(default=None, ge=1, le=60)


class PlantOwnerIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(default=None, max_length=20, pattern=r"^\+?[0-9]{6,20}$")
    password: str = Field(min_length=8, max_length=128)


class PlantCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    location: str | None = Field(default=None, max_length=200)
    timezone: str = Field(default="Asia/Kolkata", max_length=64)
    costing_mode: Literal["basic", "standard"] = "basic"
    owner: PlantOwnerIn


# --- escalations ---------------------------------------------------------------


class EscalationOut(BaseModel):
    id: uuid.UUID
    level: int
    status: str
    channel: str
    #: The role this rung was aimed at — maintenance | stores | quality |
    #: production | plant_head | supervisor. §3 Layer 2: "the system picks the
    #: recipient from the reason code". Stored on the row, so an inbox can tell
    #: a breakdown from a material wait without re-deriving the routing.
    route: str | None = None
    downtime_event_id: uuid.UUID
    station_code: str | None
    message: str | None
    recipient_name: str | None
    cost_at_send_paise: int
    cost_at_send_display: str
    due_at: datetime
    sent_at: datetime | None
    acknowledged_at: datetime | None
    acknowledged_by_name: str | None
    resolved_at: datetime | None
    is_open: bool


# --- shift -------------------------------------------------------------------


class JobAssignment(BaseModel):
    station_id: uuid.UUID
    job_order_id: uuid.UUID
    operator_id: uuid.UUID | None = None
    lot_code: str | None = Field(default=None, min_length=1, max_length=64)


class ShiftStartRequest(BaseModel):
    shift_id: uuid.UUID
    assignments: list[JobAssignment] = Field(default_factory=list, max_length=500)


class ShiftSessionOut(ORMModel):
    id: uuid.UUID
    shift_id: uuid.UUID
    business_date: date
    started_at: datetime
    ended_at: datetime | None
    status: str
    planned_minutes: int
    locked_at: datetime | None


class BlockerOut(BaseModel):
    kind: str
    detail: str
    reference_id: str | None = None


class ClosureCheckOut(BaseModel):
    can_close: bool
    blockers: list[BlockerOut]


class ShiftCloseRequest(BaseModel):
    # An owner override is allowed but never silent — §2's audit trail.
    override_reason: str | None = Field(default=None, max_length=2000)


# --- capture -----------------------------------------------------------------


class DowntimeStartRequest(BaseModel):
    station_id: uuid.UUID
    reason_code_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=1000)
    started_at: datetime | None = None
    #: Made by the device; a retry with the same value never records twice.
    client_ref: str | None = Field(default=None, max_length=64)


class DowntimeCloseRequest(BaseModel):
    reason_code_id: uuid.UUID | None = None
    ended_at: datetime | None = None
    client_ref: str | None = Field(default=None, max_length=64)


class CostBreakdownOut(BaseModel):
    machine_paise: int
    labour_paise: int
    overhead_paise: int
    lost_contribution_paise: int
    parts_not_made: int


class DowntimeEventOut(BaseModel):
    id: uuid.UUID
    station_id: uuid.UUID
    station_code: str | None = None
    reason_code_id: uuid.UUID | None
    reason_label: str | None = None
    started_at: datetime
    ended_at: datetime | None
    duration_minutes: float | None
    status: str
    note: str | None
    cost_paise: int
    cost_display: str
    breakdown: CostBreakdownOut | None = None
    source: str


class ProductionRequest(BaseModel):
    station_id: uuid.UUID
    count: int = Field(ge=0, le=1_000_000)
    recorded_at: datetime | None = None
    client_ref: str | None = Field(default=None, max_length=64)


class ProductionOut(ORMModel):
    id: uuid.UUID
    station_id: uuid.UUID
    count: int
    recorded_at: datetime
    covers_from: datetime | None


class DefectEntry(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    count: int = Field(ge=0, le=1_000_000)


class QualityRequest(BaseModel):
    """Inspected = good + rejected. ``rework_count`` is how many of the good
    parts only became good after rework — which is why they count as good for
    OEE Quality but not for First Pass Yield (§8.1), and why a reworked part is
    never also scrap.

    ``disposition`` applies to the rejected parts. ``"rework"`` is still
    accepted from older clients and means every rejected part in the batch was
    reworked to good.
    """

    part_id: uuid.UUID
    good_count: int = Field(ge=0, le=1_000_000)
    reject_count: int = Field(ge=0, le=1_000_000)
    station_id: uuid.UUID | None = None
    lot_id: uuid.UUID | None = None
    rework_count: int = Field(default=0, ge=0, le=1_000_000)
    rework_minutes: float | None = Field(default=None, ge=0, le=100_000)
    disposition: Literal["scrap", "rework", "use_as_is"] = "scrap"
    defects: list[DefectEntry] = Field(default_factory=list, max_length=50)
    client_ref: str | None = Field(default=None, max_length=64)


class QualityOut(BaseModel):
    id: uuid.UUID
    part_id: uuid.UUID
    good_count: int
    reject_count: int
    rework_count: int = 0
    scrap_cost_paise: int
    scrap_cost_display: str
    rework_cost_paise: int = 0
    rework_cost_display: str = "₹0"
    reconciliation_gap: int | None
    reconciliation_flagged: bool
    # Present only when the two independent counts disagree beyond tolerance.
    reconciliation_message: str | None = None


class ChangeJobRequest(BaseModel):
    """§14.3 — the mid-shift part change, recorded as an event of its own."""

    station_id: uuid.UUID
    new_job_order_id: uuid.UUID
    operator_id: uuid.UUID | None = None


# --- grid / reports ----------------------------------------------------------


class StationTileOut(BaseModel):
    station_id: str
    code: str
    name: str
    line: str | None
    status: str
    count: int
    target: int
    job_number: str | None
    part_number: str | None
    open_event_id: str | None
    stopped_since: datetime | None
    stopped_minutes: float
    reason_label: str | None
    live_cost_paise: int
    live_cost_display: str
    ticker_rate_per_minute: float
    shift_cost_paise: int


class LossLineOut(BaseModel):
    station: str
    reason: str
    minutes: float
    cost: int
    cost_display: str
    recoverability: str
    event_count: int


class LossReportOut(BaseModel):
    business_date: date
    recoverable_total_paise: int
    recoverable_total_display: str
    structural_total_paise: int
    structural_total_display: str
    total_minutes: float
    recoverable: list[LossLineOut]
    structural: list[LossLineOut]


class OwnerHomeOut(BaseModel):
    """Four numbers and nothing else (§7)."""

    recovered_profit_paise: int
    recovered_profit_display: str
    lost_yesterday_paise: int
    lost_yesterday_display: str
    lost_month_to_date_paise: int
    lost_month_to_date_display: str
    actions_completed: int
    actions_open: int
    running_roi: float
    annualised_projection_paise: int
    annualised_projection_display: str
    structural_yesterday_paise: int
    top_losses: list[LossLineOut]
    as_of: date


class ShiftKpiOut(BaseModel):
    shift_session_id: str
    business_date: date
    shift_name: str
    oee: float
    availability: float
    performance: float
    quality: float
    planned_minutes: float
    downtime_minutes: float
    recoverable_downtime_minutes: float
    total_count: int
    good_count: int
    reject_count: int
    target: int
    schedule_adherence: float
    ppm: float
    first_pass_yield: float
    mtbf_minutes: float
    mttr_minutes: float
    breakdown_count: int
    recoverable_loss_paise: int
    structural_loss_paise: int
    man_hours: float = 0.0
    productivity_per_man_hour: float | None = None
    downtime_percent: float = 0.0
    recoverable_downtime_percent: float = 0.0
    structural_downtime_percent: float = 0.0
    flags: list[str]


class MeetingPackOut(BaseModel):
    """Screen 7 — readable from five metres, and gated on yesterday's actions."""

    business_date: date
    locked: bool
    losses: LossReportOut
    top_quality: list[dict]
    open_actions: list[dict]
    # The gate: Today's Plan stays hidden until yesterday's escalated Actions
    # are each marked Done or Delayed-with-reason (§5.3).
    actions_gate_cleared: bool
    blocking_actions: list[dict]


# --- actions -----------------------------------------------------------------


class ActionOut(BaseModel):
    id: uuid.UUID
    reference: str
    title: str
    description: str | None
    trigger: str
    state: str
    pdca_phase: str
    station_code: str | None
    reason_label: str | None
    linked_cost_paise: int
    linked_cost_display: str
    occurrence_count: int
    routed_to_rca: bool
    severity_reasons: list[str]
    owner_name: str | None
    due_date: date | None
    root_cause: str | None
    countermeasure: str | None
    evidence_photo_url: str | None = None
    baseline_weekly_loss_paise: int
    recovered_profit_paise: int
    recovered_profit_display: str
    sustained_weeks: int
    is_overdue: bool
    closed_at: datetime | None = None
    meeting_status_on: date | None = None
    photos: list[dict] = Field(default_factory=list)
    #: The states this user may move the Action to by hand, right now.
    allowed_transitions: list[str] = Field(default_factory=list)
    can_close: bool = False
    created_at: datetime


class ActionCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    station_id: uuid.UUID | None = None
    reason_code_id: uuid.UUID | None = None
    owner_name: str | None = Field(default=None, max_length=200)
    due_date: date | None = None


class ActionUpdateRequest(BaseModel):
    owner_id: uuid.UUID | None = None
    owner_name: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    root_cause: str | None = Field(default=None, max_length=4000)
    countermeasure: str | None = Field(default=None, max_length=4000)
    state: str | None = Field(default=None, max_length=16)
    meeting_status: Literal["done", "delayed"] | None = None
    delay_reason: str | None = Field(default=None, max_length=1000)


class MemoryCaptureRequest(BaseModel):
    """§3 Layer 4 — the four questions, asked of whoever fixed it."""

    what_fixed_it: str = Field(min_length=3, max_length=4000)
    what_was_replaced: str | None = Field(default=None, max_length=4000)
    photo_url: str | None = Field(default=None, max_length=500)
    check_first_next_time: str | None = Field(default=None, max_length=4000)


class ActionCloseRequest(BaseModel):
    countermeasure: str = Field(min_length=3, max_length=4000)
    root_cause: str | None = Field(default=None, max_length=4000)
    memory: MemoryCaptureRequest


class RcaUpdateRequest(BaseModel):
    five_whys: list[dict] = Field(max_length=10)
    conclusion: str | None = Field(default=None, max_length=4000)
    assigned_team: str | None = Field(default=None, max_length=64)
    due_date: date | None = None


class MemoryEntryOut(BaseModel):
    id: uuid.UUID
    problem_summary: str | None
    what_fixed_it: str
    what_was_replaced: str | None
    check_first_next_time: str | None
    fixed_by_name: str | None
    occurred_on: date | None
    station_code: str | None = None


# --- AI ----------------------------------------------------------------------


class CoachPriorityOut(BaseModel):
    rank: int
    station: str
    headline: str
    evidence: str
    action: str
    assignee: str
    deadline: str
    value_paise: int
    value_display: str


class CoachBriefingOut(BaseModel):
    id: uuid.UUID
    business_date: date
    tier: int
    tier_label: str
    priorities: list[CoachPriorityOut]
    rendered_text: str | None
    generated_by: str
    data_basis: dict | None


class CopilotRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    #: The language the *answer* comes back in. It is not a guess at what the
    #: question was typed in: a supervisor reading Hindi still names machines
    #: and reason codes in the English the plant's records use.
    language: Literal["en", "ta", "hi"] = "en"


class CopilotResponse(BaseModel):
    answer: str
    intent: str
    tier_available: bool
    facts: dict
    evidence: list[dict]
    answered_by: str
    latency_ms: int


# --- onboarding --------------------------------------------------------------


class ReadinessSubmission(BaseModel):
    plant_name: str = Field(min_length=1, max_length=200)
    respondent_name: str | None = Field(default=None, max_length=200)
    respondent_phone: str | None = Field(default=None, max_length=20)
    answers: dict[str, Any] = Field(max_length=100)


class ReadinessResult(BaseModel):
    score: int
    band: str
    priority_gaps: list[str]
    estimated_onboarding_hours: int
    message: str


class UploadPreview(BaseModel):
    #: Present only when the file parsed cleanly — the id to confirm.
    upload_id: uuid.UUID | None = None
    template: str
    filename: str
    rows_found: int
    columns: list[str]
    preview: list[dict]
    errors: list[dict]
    # "Found: 132 part numbers, 19 machines, 41 operators, 3 shifts. Confirm?"
    summary: str


class UploadConfirm(BaseModel):
    upload_id: uuid.UUID


# --- ingest ------------------------------------------------------------------


class SensorReading(BaseModel):
    """The data-source seam (§2, §9).

    A CT clamp posting a cycle count and a supervisor tapping a number land in
    the same table through the same validation. Nothing above the data layer
    can tell them apart — which is the entire point of building it now.
    """

    station_code: str
    kind: Literal["production", "downtime_start", "downtime_end"]
    value: int | None = None
    reason_code: str | None = None
    occurred_at: datetime | None = None
    device_id: str | None = None


# ---------------------------------------------------------------------------
# Layer 5 — Factory Plugins (§3 Layer 5)
#
# Note what is absent: none of these carry a daily-capture payload. The one rule
# governing every plugin is that it feeds on existing Core data rather than
# adding to the 15-minute daily entry budget.
# ---------------------------------------------------------------------------


class ClauseOut(BaseModel):
    id: uuid.UUID
    standard: str
    clause_ref: str
    title: str
    requirement: str | None
    status: str
    evidence_count: int
    last_evidence_at: datetime | None
    gap_note: str | None
    #: Cross-standard mapping — one captured fact closing clauses elsewhere.
    also_satisfies: list[str]


class ComplianceOverview(BaseModel):
    overall_score: int
    by_standard: dict[str, int]
    ready: int
    partial: int
    gaps: int
    clauses: list[ClauseOut]


class AuditPackOut(BaseModel):
    id: uuid.UUID
    standard: str
    period_start: date
    period_end: date
    readiness_score: int
    clause_count: int
    evidence_rows: int
    created_at: datetime


# --- Kaizen AI (Plugin Edition) ----------------------------------------------
#
# Money crosses this surface as integer paise plus a pre-formatted display
# string, and the hard / soft split is never merged into one figure — that
# separation is the credibility engine of the whole plugin (§7.2).


class KaizenContextOut(BaseModel):
    """§5.1 — what Core already knows, shown for one-tap confirmation."""

    station_id: uuid.UUID | None = None
    station_code: str | None = None
    station_name: str | None = None
    line: str | None = None
    department: str | None = None
    job_order_id: uuid.UUID | None = None
    job_number: str | None = None
    part_id: uuid.UUID | None = None
    part_number: str | None = None
    operation_id: uuid.UUID | None = None
    operation_code: str | None = None
    bottleneck_factor: float | None = None
    is_bottleneck: bool | None = None
    #: Question keys Core has answered, so Step 4 never asks them.
    known: list[str] = Field(default_factory=list)
    prompt: str = ""


class KaizenTranscriptRequest(BaseModel):
    """Step 1 — voice to text. Phase 1 transcribes on the device."""

    text: str = Field(min_length=1, max_length=4000)
    language: str | None = Field(default=None, max_length=8)


class KaizenTranscriptOut(BaseModel):
    text: str
    original: str
    #: False when the local model was unavailable and the words are unchanged.
    cleaned: bool


class KaizenVisionFinding(BaseModel):
    """Step 2 — a suggestion shown ONLY to the operator until they accept it."""

    category: Literal[
        "housekeeping", "safety_hazard", "leak_or_spill", "missing_label", "obstruction"
    ]
    text: str = Field(max_length=280)
    accepted: bool = False


class KaizenVisionOut(BaseModel):
    findings: list[KaizenVisionFinding] = Field(default_factory=list)
    ran: bool
    note: str | None = None
    attachment_id: uuid.UUID | None = None


class KaizenQuestion(BaseModel):
    key: str
    text: str
    kind: Literal["choice", "number", "text", "boolean"]
    options: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    unit: str | None = None
    depends_on: list[str] | None = None
    optional: bool = False


class KaizenClassifyRequest(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=3, max_length=4000)
    station_id: uuid.UUID | None = None
    vision_findings: list[KaizenVisionFinding] = Field(default_factory=list)


class KaizenClassifyOut(BaseModel):
    """Steps 3 and 4 — the category to confirm, and the questions that follow."""

    category: str
    category_label: str
    saving_type: str
    safety_flagged: bool
    #: Every category, so "Change" shows the operator a full list.
    categories: list[dict[str, str]]
    questions: list[KaizenQuestion]
    context: KaizenContextOut
    #: Questions Core answered, and therefore never asked.
    skipped: list[str] = Field(default_factory=list)


class KaizenAnswer(BaseModel):
    key: str = Field(max_length=64)
    question: str | None = Field(default=None, max_length=300)
    answer: Any = None
    unit: str | None = Field(default=None, max_length=16)
    #: "operator" when tapped, "core" when inherited and never asked.
    source: Literal["operator", "core"] = "operator"


class KaizenSubmission(BaseModel):
    """Everything the phone collected across Steps 1-4."""

    title: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=10, max_length=4000)
    track: Literal["quick", "full"] = "full"
    voice_transcript: str | None = Field(default=None, max_length=4000)
    voice_language: str | None = Field(default=None, max_length=8)
    station_id: uuid.UUID | None = None
    category: str | None = None
    vision_findings: list[KaizenVisionFinding] = Field(default_factory=list)
    vision_ran: bool = False
    interview: list[KaizenAnswer] = Field(default_factory=list)
    investment_paise: int = Field(default=0, ge=0)
    needs_shutdown: bool = False
    proposed_fix: str | None = Field(default=None, max_length=1000)
    context_confirmed: bool = False
    unknown_station_note: str | None = Field(default=None, max_length=300)
    #: Submit-on-behalf. Points and recognition go to this operator, never to
    #: the person filling the form.
    credited_operator_id: uuid.UUID | None = None
    submitted_by_name: str | None = Field(default=None, max_length=200)
    attachment_ids: list[uuid.UUID] = Field(default_factory=list)


class KaizenSavingOut(BaseModel):
    """The two columns, never merged (§7.2)."""

    hard_paise: int
    hard_display: str
    soft_paise: int
    soft_display: str
    basis: str | None = None
    provisional: bool = False
    provisional_note: str | None = None
    #: §7.3 — the single question the approval card may ask, never blocking.
    missing_rate_key: str | None = None
    missing_rate_label: str | None = None


class KaizenImpactOut(BaseModel):
    """The Business Impact Card. Rupees and stars, never mixed."""

    saving: KaizenSavingOut
    stars: dict[str, int]
    effort_score: int
    impact_score: int
    investment_paise: int
    investment_display: str
    investment_is_estimate: bool
    payback_months: float | None = None
    roi_percent: float | None = None
    cost_of_doing_nothing: dict[str, Any]


class KaizenIdeaOut(BaseModel):
    id: uuid.UUID
    reference: str
    track: str
    title: str
    description: str
    voice_transcript: str | None = None

    submitted_by_name: str
    credited_name: str
    credited_operator_id: uuid.UUID | None = None
    board: str
    department: str | None = None
    submitted_on: date | None = None

    station_code: str | None = None
    line: str | None = None
    part_number: str | None = None
    reason_label: str | None = None
    defect_label: str | None = None
    unknown_station_note: str | None = None

    category: str | None = None
    category_label: str | None = None
    saving_type: str | None = None
    status: str
    #: HIGH | MEDIUM | LOW. The 0-100 score is never shown as a headline (§11.2).
    priority_verdict: str | None = None
    safety_flagged: bool = False
    is_recurrence: bool = False

    impact: KaizenImpactOut
    #: Step 5A, frozen at submission so an approver's evidence is reproducible.
    history: dict[str, Any] | None = None
    similar: list[dict[str, Any]] | None = None
    challenge: dict[str, Any] | None = None
    forecast: dict[str, Any] | None = None
    vision_findings: list[KaizenVisionFinding] = Field(default_factory=list)
    interview: list[dict[str, Any]] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    score_rationale: str | None = None

    action_id: uuid.UUID | None = None
    duplicate_action_id: uuid.UUID | None = None
    promoted_from_id: uuid.UUID | None = None
    approval_route: list[str] = Field(default_factory=list)
    approval_step: int = 0
    approval_trail: list[dict[str, Any]] = Field(default_factory=list)
    decision_note: str | None = None

    verified_saving_paise: int = 0
    verified_saving_display: str = "\u20b90"
    points_awarded: int = 0
    created_at: datetime


class KaizenPreviewRequest(KaizenSubmission):
    """Steps 5A-9 without committing — what the operator sees before they submit."""


class KaizenPreviewOut(BaseModel):
    category: str
    category_label: str
    saving_type: str
    context: KaizenContextOut
    history: dict[str, Any]
    similar: list[dict[str, Any]] = Field(default_factory=list)
    impact: KaizenImpactOut
    challenge: dict[str, Any] | None = None
    forecast: dict[str, Any] | None = None
    priority_verdict: str
    rationale: str


class KaizenDecision(BaseModel):
    accept: bool
    note: str | None = Field(default=None, max_length=2000)
    owner_name: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    #: Replaces the operator's guess with the real figure from Maintenance or
    #: Purchase; payback and ROI recalculate on the spot.
    investment_paise: int | None = Field(default=None, ge=0)


class KaizenChallengeChoice(BaseModel):
    """Step 7 — which option went forward, and why an alternative was dismissed.

    Every dismissal is logged so the suggestions improve: AI does not know the
    plant layout and will sometimes propose something physically impossible.
    """

    chosen: Literal["mine", "alternative", "both"]
    dismissed_reason: str | None = Field(default=None, max_length=500)


class KaizenPromoteRequest(BaseModel):
    """A Quick Observation that turned out to matter (§"AI auto-promotes")."""

    interview: list[KaizenAnswer] = Field(default_factory=list)
    investment_paise: int = Field(default=0, ge=0)
    needs_shutdown: bool = False
    proposed_fix: str | None = Field(default=None, max_length=1000)


class KaizenRateRequest(BaseModel):
    """§7.3 — the one place Kaizen writes a cost value, into CORE's cost master."""

    key: Literal["electricity_kwh", "compressed_air_m3", "scrap_recovery_kg"]
    paise: int = Field(gt=0)
    effective_from: date | None = None


class KaizenStandingOut(BaseModel):
    """The operator's home screen: level, points, and this month's counter."""

    name: str
    points: int
    level: str
    level_label: str
    next_level_label: str | None = None
    points_to_next: int = 0
    level_floor: int = 0
    level_ceiling: int | None = None
    this_month: int = 0
    monthly_minimum: int = 1
    days_left_in_month: int = 0
    verified_saving_paise: int = 0
    verified_saving_display: str


class KaizenPatternOut(BaseModel):
    id: uuid.UUID
    label: str
    kaizen_count: int
    event_count: int
    departments: list[str] = Field(default_factory=list)
    #: Recorded, not estimated — only Core events contribute (§10.2).
    verified_cost_paise: int
    verified_cost_display: str
    lost_minutes: int
    recommendation: str | None = None
    action_id: uuid.UUID | None = None
    detected_on: date


class KaizenProjectRequest(BaseModel):
    owner_name: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    estimated_cost_paise: int = Field(default=0, ge=0)


class KaizenSettingsOut(BaseModel):
    template: str
    steps: list[str] = Field(default_factory=list)
    auto_approve_below_paise: int
    auto_approve_min_score: int
    safety_always_to_owner: bool
    finance_above_paise: int
    history_window_days: int
    score_weights: dict[str, int]
    pattern_min_items: int
    pattern_min_value_paise: int
    patterns_include_quick: bool
    promote_above_paise: int
    monthly_minimum: int
    quick_win_investment_paise: int
    enabled_categories: list[str] = Field(default_factory=list)
    #: Core's own Action threshold. Kaizen never introduces a second one (§4.3).
    action_threshold_paise: int
    action_threshold_display: str


class KaizenSettingsUpdate(BaseModel):
    template: Literal["simple", "standard", "open"] | None = None
    steps: list[str] | None = None
    auto_approve_below_paise: int | None = Field(default=None, ge=0)
    auto_approve_min_score: int | None = Field(default=None, ge=0, le=100)
    safety_always_to_owner: bool | None = None
    finance_above_paise: int | None = Field(default=None, ge=0)
    history_window_days: int | None = Field(default=None, ge=30, le=730)
    score_weights: dict[str, int] | None = None
    pattern_min_items: int | None = Field(default=None, ge=2)
    pattern_min_value_paise: int | None = Field(default=None, ge=0)
    patterns_include_quick: bool | None = None
    promote_above_paise: int | None = Field(default=None, ge=0)
    #: "Do not raise the default above 1." One 30-second observation clears it.
    monthly_minimum: int | None = Field(default=None, ge=0, le=10)
    quick_win_investment_paise: int | None = Field(default=None, ge=0)
    enabled_categories: list[str] | None = None


class KaizenDigestOut(BaseModel):
    """§12.1 — the block Kaizen contributes to Core's weekly owner digest."""

    block: dict[str, Any]
    rendered: str


class KaizenDashboardOut(BaseModel):
    """§12.2, in build order: Quick Wins first, the rest later."""

    quick_wins: list[KaizenIdeaOut]
    pending: list[KaizenIdeaOut]
    delayed: list[KaizenIdeaOut]
    top_savings: list[KaizenIdeaOut]
    safety: list[KaizenIdeaOut]
    patterns: list[KaizenPatternOut]
    participation: list[dict[str, Any]]
    leaderboards: dict[str, list[dict[str, Any]]]
    totals: dict[str, Any]


class SkillUpdate(BaseModel):
    operator_id: uuid.UUID
    station_id: uuid.UUID
    #: 0 none · 1 training · 2 competent · 3 can train others
    level: int = Field(ge=0, le=3)
    certified_on: date | None = None


class SkillMatrixStation(BaseModel):
    id: str
    code: str
    line: str | None
    is_bottleneck: bool
    coverage: int
    at_risk: bool


class SkillMatrixOperator(BaseModel):
    id: str
    name: str
    employee_code: str | None
    skills: dict[str, int]
    certified_count: int


class SkillMatrixOut(BaseModel):
    stations: list[SkillMatrixStation]
    operators: list[SkillMatrixOperator]
    min_coverage: int


class TrainingNeedOut(BaseModel):
    id: uuid.UUID
    station_code: str
    #: "gap" — already cost money · "coverage" — one absence away from costing it
    kind: str
    #: Measured, for gaps. Always zero for coverage risks: nothing has been lost
    #: yet, and inventing an absence rate to annualise one would be a guess.
    annual_loss_paise: int
    annual_loss_display: str
    #: For coverage risks: what one shift with the station idle costs, from the
    #: same kernel formula as every downtime event.
    exposure_per_day_paise: int = 0
    exposure_per_day_display: str = "₹0"
    is_bottleneck: bool = False
    recommendation: str | None
    evidence: dict
    has_action: bool


# ---------------------------------------------------------------------------
# Traceability and stages (§3, §14.2)
# ---------------------------------------------------------------------------


class StageRequest(BaseModel):
    """One tap at a stage: which lot, which operation, how many passed."""

    lot_code: str = Field(min_length=1, max_length=64)
    operation_code: str = Field(min_length=1, max_length=32)
    quantity_completed: int = Field(ge=0)
    quantity_rejected: int = Field(default=0, ge=0)
    station_id: uuid.UUID | None = None


class DispatchRequest(BaseModel):
    lot_code: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=10_000_000)
    #: §14.2 — "vehicle/challan reference (one field)".
    vehicle_reference: str | None = Field(default=None, max_length=128)


# ---------------------------------------------------------------------------
# The role desks (§1, "Who uses it") — Maintenance and Stores
# ---------------------------------------------------------------------------
#
# §1 gives these two roles a cadence of "when events occur", not "daily". Each
# desk therefore answers one question — what is mine, right now, and what is it
# costing — and offers exactly the one write §1 names for that role. Every
# number below comes from Core data already captured; neither desk adds a
# register, and neither spends any of §4.3's 15-minute budget.


class OpenStopOut(BaseModel):
    """A machine that is stopped now, with its live rupee ticker."""

    event_id: uuid.UUID
    station_id: uuid.UUID
    station_code: str
    station_name: str
    reason_code: str | None
    reason_label: str | None
    is_breakdown: bool
    started_at: datetime
    minutes: int
    cost_paise: int
    cost_display: str
    note: str | None
    #: Present once somebody has written up what they did (§1, Maintenance).
    repair_note: str | None = None
    repair_part_changed: str | None = None
    repaired_by_name: str | None = None
    repaired_at: datetime | None = None
    escalation_level: int = 0


class ClosedBreakdownOut(BaseModel):
    """A breakdown that ended without a repair note — knowledge about to be lost."""

    event_id: uuid.UUID
    station_code: str
    station_name: str
    reason_label: str | None
    started_at: datetime
    ended_at: datetime | None
    minutes: int
    cost_paise: int
    cost_display: str


class RepairLogRequest(BaseModel):
    """§1 — "Logs what was done to fix breakdowns"."""

    note: str = Field(min_length=1, max_length=2000)
    #: The one structured field worth having: Factory Memory's second capture
    #: question is "what did you replace or change?" (§3, Layer 4).
    part_changed: str | None = Field(default=None, max_length=200)


class MaintenanceDeskOut(BaseModel):
    """Everything the maintenance in-charge needs, ranked by money."""

    alerts: list[EscalationOut]
    open_breakdowns: list[OpenStopOut]
    other_open_stops: list[OpenStopOut]
    awaiting_repair_note: list[ClosedBreakdownOut]
    open_cost_paise: int
    open_cost_display: str


class StagingRowOut(BaseModel):
    """One job order and Stores' answer about its material for today."""

    job_order_id: uuid.UUID
    job_number: str
    part_number: str
    part_description: str | None
    customer_name: str | None
    planned_quantity: int
    station_codes: list[str]
    status: Literal["pending", "staged", "short"]
    quantity_required: int | None = None
    quantity_staged: int | None = None
    note: str | None = None
    confirmed_by_name: str | None = None
    confirmed_at: datetime | None = None
    #: What not answering has already cost today, in this plant's own numbers.
    wait_events: int = 0
    wait_cost_paise: int = 0
    wait_cost_display: str = "₹0"


class StagingConfirmRequest(BaseModel):
    """§1 — "Confirms material staging". One tap, plus a number if short."""

    job_order_id: uuid.UUID
    status: Literal["staged", "short"]
    #: Required when short; defaults to the job's planned quantity when staged.
    quantity_staged: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=1000)


class StoresDeskOut(BaseModel):
    """Everything the stores in-charge needs, worst first."""

    business_date: date
    alerts: list[EscalationOut]
    open_waits: list[OpenStopOut]
    queue: list[StagingRowOut]
    pending_count: int
    wait_cost_today_paise: int
    wait_cost_today_display: str
