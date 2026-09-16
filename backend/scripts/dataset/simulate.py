"""The simulation: every plant, day by day, shift by shift.

The shape of one simulated day mirrors the product's own rhythm (§6):

    05:45  Coach briefing          (facts as of this morning; tier-gated)
    shift  job sessions, hourly production entries, downtime with escalations,
           inspector quality entries, stage completions
    close  Shift Closure Lock, frozen KPI summary, Action trigger evaluation
    day    Actions move through PDCA, fixes change the machine's future,
           the hourly worker reopens failed fixes and accrues Recovered Profit
    eve    Copilot questions, Kaizen submissions, dispatch of finished lots

Wherever the product has a formula, the kernel computes it. Wherever the
product has a service with a DB query, the query is re-expressed over the
in-memory rows with the same filters, so loading this dataset and re-running
the service gives the same answer.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from types import SimpleNamespace

from app.ai.coach import Priority, _action_for, _paise, _route_for
from app.ai.copilot import _deterministic_answer
from app.ai.facts import FactBundle
from app.ingest import readiness
from app.kernel import (
    ESCALATION_LADDER,
    StationRun,
    aggregate_oee,
    blended_machine_rate,
    breakdown_minutes,
    coach_tier,
    count_breakdowns,
    count_reconciliation,
    downtime_event_cost,
    evaluate_action_triggers,
    first_pass_yield,
    format_inr,
    freeze_baseline,
    labour_cost_rate,
    machine_cost_rate,
    mtbf,
    mttr,
    overhead_rate,
    ppm,
    rank_losses,
    recovered_profit,
    route_by_severity,
    rupees,
    schedule_adherence,
    scrap_cost,
)
from app.kernel.profit import WeeklyLoss
from app.kernel.rules import ActionThresholds, CoachTier, DataSufficiency, EventForTrigger
from app.kernel.types import (
    FORMULA_VERSION,
    CostingMode,
    CostRates,
    CostSnapshot,
    DowntimeSlice,
    LossLine,
    OeeTreatment,
    PartEconomics,
    Recoverability,
)
from app.models import ClauseStatus
from app.services.compliance import CLAUSE_LIBRARY, readiness_score
from app.services.downtime import escalation_message
from app.services.kaizen import SCOPED_SHARE, UNSCOPED_SHARE, match_reason
from app.services.skills import COMPETENT, MIN_COVERAGE, MIN_GAP_EVENTS

from . import texts
from .catalog import (
    BASE_EVENT_PROFILE,
    DEFECT_TAMIL,
    INITIALS,
    OPERATOR_NAMES,
    PLATFORM_PEOPLE,
    REASON_LIBRARY,
    PlantSpec,
)
from .store import Store

IST = timezone(timedelta(hours=5, minutes=30))
HOLIDAYS = {date(2026, 8, 15)}  # Independence Day — plants closed
ACTIVE_STATES = ("open", "root_cause", "planned", "in_progress")

EXTRA_DURATIONS = {
    "DIE_CHANGE": (14, 34),
    "COIL_CHANGE": (8, 24),
    "DIE_CRACK": (30, 90),
    "CMM_QUEUE": (10, 28),
    "HT_LOAD_WAIT": (10, 28),
    "WIP_STARVED": (8, 26),
}

REJECT_BASE = [
    ("PRESS", 0.008),
    ("WELD", 0.015),
    ("ASSY", 0.006),
    ("FIN", 0.004),
    ("HEAD", 0.002),
    ("ROLL", 0.003),
    ("HT", 0.004),
    ("FORGE", 0.014),
    ("GRIND", 0.009),
    ("HMC", 0.006),
    ("CNC", 0.012),
    ("VMC", 0.014),
    ("TAP", 0.007),
]

EVENT_NOTES = {
    "TOOL_BREAKAGE": [
        "Insert chipped on finishing pass",
        "Drill broke at the second hole",
        "Parting tool broke",
        "Punch tip broken",
    ],
    "MATERIAL_WAIT": [
        "Waiting for bar stock from stores",
        "Blanks not cut yet",
        "Coil not issued",
        "Forklift busy at dispatch",
    ],
    "MACHINE_BREAKDOWN": [
        "Spindle alarm",
        "Hydraulic pressure low",
        "Servo alarm 401",
        "Main motor tripped",
        "Lubrication pump failure",
    ],
    "QUALITY_HOLD": [
        "First-off rejected, waiting for QC",
        "Gauge doubt — sent to QC",
        "Customer complaint lot on hold",
    ],
    "NO_OPERATOR": [
        "Operator on leave",
        "Operator moved to the bottleneck",
        "Operator late — bus breakdown",
    ],
    "POWER_CUT": [
        "TNEB supply failure",
        "DG took 6 min to start",
        "Voltage dip tripped the panel",
    ],
    "SETUP_CHANGEOVER": [
        "Die change for next job",
        "Fixture change",
        "Waiting for first-off approval",
    ],
    "PROGRAM_ISSUE": ["Wrong program revision loaded", "Offset lost after alarm reset"],
    "FIXTURE_ISSUE": ["Clamp not holding", "Locating pin worn"],
    "COOLANT_ISSUE": ["Coolant low", "Chip conveyor jammed"],
    "PLANNED_MAINTENANCE": ["Weekly PM", "Monthly PM — lubrication and way-cover cleaning"],
    "MEETING_TRAINING": ["5S training", "Safety meeting"],
}

VEHICLE_PREFIX = {
    "KUMARAN": "TN 70",
    "SRIVARI": "TN 02",
    "KOVAI": "TN 37",
    "SAKTHI": "TN 21",
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def at(day: date, hhmm: str | time) -> datetime:
    value = time.fromisoformat(hhmm) if isinstance(hhmm, str) else hhmm
    return datetime.combine(day, value, tzinfo=IST)


def daterange(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def overlap_s(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> int:
    return max(0, int((min(a1, b1) - max(a0, b0)).total_seconds()))


def q4(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def ratio(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001")))


def working_day(day: date) -> bool:
    return day.weekday() != 6 and day not in HOLIDAYS


def next_working_day(day: date, n: int = 1) -> date:
    for _ in range(n):
        day += timedelta(days=1)
        while not working_day(day):
            day += timedelta(days=1)
    return day


# ---------------------------------------------------------------------------
# simulation state objects
# ---------------------------------------------------------------------------


@dataclass
class Job:
    row: dict
    part: SimpleNamespace
    op_index: int
    target: int
    done: int = 0
    scrap: int = 0
    lot: dict | None = None
    ready_at: datetime | None = None
    finish_good: int = 0
    finish_from: int = 0
    ready_day: date | None = None
    worked: bool = False
    paused: bool = False
    # The first operation this plant ran for the job; its output is what
    # "entered the routing" and so is the lot's quantity.
    start_index: int = 0
    # Earlier operations done before go-live, backfilled as imported stages.
    imported: bool = False
    # Material identity travels with the parts (§3: the bin card moves with the
    # material). ``ledger`` is what the current op may consume, per source lot,
    # first-in-first-out; ``output`` is what it has passed on, per lot.
    ledger: list = field(default_factory=list)
    output: dict = field(default_factory=dict)
    finish_lots: list = field(default_factory=list)

    @property
    def op(self) -> SimpleNamespace:
        return self.part.ops[self.op_index]


@dataclass
class Session:
    row: dict
    job: Job
    op: SimpleNamespace
    start: datetime
    end: datetime | None = None
    true_count: int = 0
    reported: int = 0
    scrap: int = 0  # parts physically scrapped at this stage
    found: int = 0  # defective parts found, whatever their disposition
    lot: dict | None = None
    inspect: bool = False
    disposition: str = "scrap"
    quality_hold: bool = False
    alloc: dict = field(default_factory=dict)  # lot id -> [lot, consumed, scrapped]


@dataclass
class Ev:
    row: dict
    station: SimpleNamespace
    reason: SimpleNamespace | None
    bd: date
    start: datetime
    end: datetime | None


@dataclass
class Plan:
    action: dict
    reason: SimpleNamespace | None
    station: SimpleNamespace | None
    rca: dict | None
    steps: list[tuple[date, str]] = field(default_factory=list)
    done: set[int] = field(default_factory=set)
    owner: tuple[str, dict | None] | None = None
    closures: int = 0
    last_week_count: int = -1
    kaizen: dict | None = None
    need: dict | None = None
    part_id: object = None


# ---------------------------------------------------------------------------
# Generator — owns the store, platform accounts and cross-plant tables
# ---------------------------------------------------------------------------


class Generator:
    def __init__(self, *, seed: int, as_of: date, now: datetime, plants: list[PlantSpec]):
        import random

        self.rng = random.Random(seed)
        self.store = Store(self.rng)
        self.as_of = as_of
        self.now = now
        self.plant_specs = plants
        self.forms: dict[str, dict[str, list[dict]]] = {}
        self.credentials: list[dict] = []
        self.sims: list[PlantSim] = []
        self._hashes: dict[str, str] = {}

    def password_hash(self, password: str) -> str:
        # One argon2 hash per distinct password: every account still verifies,
        # and generation does not spend a minute hashing the same string.
        if password not in self._hashes:
            from app.core.security import hash_password

            self._hashes[password] = hash_password(password)
        return self._hashes[password]

    def run(self) -> Store:
        created = datetime(2026, 5, 18, 10, 0, tzinfo=IST)
        platform = []
        for name, email, password, role in PLATFORM_PEOPLE:
            user = self.store.add(
                "users",
                at=created,
                email=email,
                phone=None,
                full_name=name,
                password_hash=self.password_hash(password),
                preferred_language="en",
                is_active=True,
                last_login_at=self.now - timedelta(hours=self.rng.randint(1, 30)),
            )
            platform.append((user, role))
            self.credentials.append(
                {
                    "plant": "ALL",
                    "name": name,
                    "role": role,
                    "login": email,
                    "password": password,
                }
            )

        for spec in self.plant_specs:
            sim = PlantSim(self, spec, platform)
            sim.run()
            self.sims.append(sim)

        self._readiness_assessments()
        self.store.validate()
        return self.store

    def _readiness_assessments(self) -> None:
        """§4.1 Step 0 — prospects who never signed, plus each live plant's own answers."""
        rng = self.rng
        keys = [q.key for q in readiness.QUESTIONS]

        def answers_for(maturity: float) -> dict:
            out = {}
            for question in readiness.QUESTIONS:
                chance = min(
                    0.95, max(0.05, maturity + (0.1 if question.weight <= 5 else -0.05))
                )
                value = rng.random() < chance
                out[question.key] = (
                    rng.choice(["yes", "Yes", True])
                    if value
                    else rng.choice(["no", "No", False])
                )
            return out

        rows = []
        for sim in self.sims:
            maturity = {"standard": 0.62, "basic": 0.38}[sim.spec.costing_mode]
            rows.append(
                (
                    sim.spec.name,
                    sim,
                    maturity,
                    sim.spec.go_live - timedelta(days=rng.randint(20, 45)),
                )
            )

        names = list(texts.PROSPECT_NAMES)
        rng.shuffle(names)
        for name in names:
            city = rng.choice(texts.PROSPECT_CITIES)
            day = date(2026, 4, 1) + timedelta(
                days=rng.randint(0, (self.as_of - date(2026, 4, 1)).days)
            )
            rows.append((f"{name}, {city}", None, rng.uniform(0.15, 0.85), day))

        for plant_name, sim, maturity, day in rows:
            answers = answers_for(maturity)
            started = at(day, f"{rng.randint(7, 21):02d}:{rng.randint(0, 59):02d}")
            complete = sim is not None or rng.random() > 0.09
            if not complete:
                # Abandoned half-way: the answers stop, nothing is scored.
                answers = {k: answers[k] for k in keys[: rng.randint(3, 11)]}
            outcome = readiness.score(answers) if complete else None
            respondent = (
                sim.owner_person.name
                if sim
                else f"{rng.choice(OPERATOR_NAMES)} {rng.choice(INITIALS)}."
            )
            phone = (
                sim.owner_recipient["whatsapp_number"]
                if sim
                else f"+9194{rng.randint(10_000_000, 99_999_999)}"
            )
            self.store.add(
                "readiness_assessments",
                at=started,
                plant_id=sim.pid if sim else None,
                plant_name=plant_name,
                respondent_name=respondent,
                respondent_phone=phone,
                answers=answers,
                score=outcome.score if outcome else 0,
                band=outcome.band if outcome else None,
                priority_gaps=outcome.priority_gaps if outcome else None,
                estimated_onboarding_hours=outcome.estimated_onboarding_hours
                if outcome
                else None,
                completed_at=started + timedelta(minutes=rng.randint(4, 14))
                if complete
                else None,
            )


# ---------------------------------------------------------------------------
# PlantSim — one plant from onboarding to "now"
# ---------------------------------------------------------------------------


class PlantSim:
    def __init__(self, g: Generator, spec: PlantSpec, platform: list[tuple[dict, str]]):
        self.g = g
        self.s = g.store
        self.rng = g.rng
        self.spec = spec
        self.platform = platform
        self.now = g.now
        self.as_of = g.as_of

        self.rate_rows: list[SimpleNamespace] = []
        self.stations: dict[str, SimpleNamespace] = {}
        self.parts: dict[str, SimpleNamespace] = {}
        self.reasons: dict[str, SimpleNamespace] = {}
        self.defects_by_family: dict[str | None, list[str]] = defaultdict(list)
        self.defect_labels: dict[str, str] = {}
        self.shifts: list[SimpleNamespace] = []
        self.operators: list[SimpleNamespace] = []
        self.skills: dict[tuple, dict] = {}

        self.queue: dict[str, list[Job]] = defaultdict(list)
        self.active: dict[str, Job | None] = {}
        self.finishing: list[Job] = []
        self.jobs: list[Job] = []
        self.job_seq = 0
        self.batch_lots: dict[tuple, dict] = {}
        self.last_part_at: dict[str, SimpleNamespace] = {}

        self.events_by_date: dict[date, list[Ev]] = defaultdict(list)
        self.pair_events: dict[tuple[str, str], list[Ev]] = defaultdict(list)
        self.closed_event_count = 0
        self.closed_shift_times: list[datetime] = []
        self.shift_rows: list[dict] = []
        self.first_bd: date | None = None

        self.actions: list[Plan] = []
        self.action_seq = 0
        self.event_actions: dict[str, list[Plan]] = defaultdict(list)
        self.fix_effect: dict[tuple[str, str], tuple[date, float]] = {}
        self.rp_rows: dict[tuple, dict] = {}
        self.memory: list[dict] = []
        self.quality_rows: list[dict] = []
        self.target_rows: list[dict] = []
        self.kaizen_seq = 0
        self.kaizen_rows: list[dict] = []
        self.kaizen_decisions: dict[str, tuple] = {}
        self.session_part: dict = {}
        self.clause_rows: list[dict] = []
        self.snapshot_cache: dict[tuple, CostSnapshot] = {}
        self.absent: dict[date, set] = {}

    # ======================================================================
    # orchestration
    # ======================================================================

    def run(self) -> None:
        spec = self.spec
        self.onboard_day = spec.go_live - timedelta(days=7)
        self.onboard_at = at(self.onboard_day, "10:30")
        self._plant()
        self._people()
        self._families_and_rates()
        self._stations()
        self._parts()
        self._reasons_and_defects()
        self._shifts()
        self._operators_and_skills()
        self._targets()
        self._forms_and_uploads()
        self._historical_memory()
        self._simulate()
        self._finalise()

    # ======================================================================
    # masters (T1-T7)
    # ======================================================================

    def _plant(self) -> None:
        spec = self.spec
        self.plant = self.s.add(
            "plants",
            at=at(self.onboard_day - timedelta(days=10), "11:00"),
            name=spec.name,
            code=spec.code,
            location=spec.location,
            timezone="Asia/Kolkata",
            currency="INR",
            costing_mode=spec.costing_mode,
            business_date_from_shift_start=True,
            action_cost_threshold=rupees(spec.action_cost_threshold),
            action_duration_minutes=spec.action_duration_minutes,
            recurrence_count=spec.recurrence_count,
            recurrence_window_shifts=spec.recurrence_window_shifts,
            reconciliation_tolerance_parts=spec.tolerance_parts,
            reconciliation_tolerance_bp=spec.tolerance_bp,
            no_blame_until=spec.go_live + timedelta(days=spec.no_blame_days),
            go_live_date=spec.go_live,
            monthly_fee=rupees(spec.monthly_fee),
            is_active=True,
        )
        self.plant["updated_at"] = self.onboard_at
        self.pid = self.plant["id"]
        self.limits = ActionThresholds(
            cost_threshold=self.plant["action_cost_threshold"],
            duration_minutes=spec.action_duration_minutes,
            recurrence_count=spec.recurrence_count,
            recurrence_window_shifts=spec.recurrence_window_shifts,
        )

    def _people(self) -> None:
        spec, rng = self.spec, self.rng
        self.recipients: dict[str, list[dict]] = defaultdict(list)
        self.person_user: dict[str, dict] = {}
        self.supervisor_for: dict[str, tuple] = {}
        self.inspector_for: dict[str, tuple] = {}
        created = self.onboard_at

        for person in spec.people:
            email = person.login if "@" in person.login else None
            phone = None if email else person.login
            user = self.s.add(
                "users",
                at=created,
                email=email,
                phone=phone,
                full_name=person.name,
                password_hash=self.g.password_hash(person.password),
                preferred_language=person.language,
                is_active=True,
                last_login_at=self.now - timedelta(minutes=rng.randint(20, 60 * 40)),
            )
            self.s.add(
                "user_plant_roles",
                at=created,
                user_id=user["id"],
                plant_id=self.pid,
                role=person.app_role,
            )
            recipient = self.s.add(
                "alert_recipients",
                at=created,
                plant_id=self.pid,
                name=person.name,
                role_tag=person.role_tag,
                whatsapp_number=phone or f"+9198{rng.randint(10_000_000, 99_999_999)}",
                escalation_level=person.level,
                user_id=user["id"],
                is_active=True,
            )
            self.recipients[person.role_tag].append(recipient)
            self.person_user[person.name] = user
            self.g.credentials.append(
                {
                    "plant": spec.code,
                    "name": person.name,
                    "role": person.app_role,
                    "login": person.login,
                    "password": person.password,
                }
            )
            if person.role_tag == "owner":
                self.owner_person, self.owner_user, self.owner_recipient = (
                    person,
                    user,
                    recipient,
                )
            if person.role_tag == "plant_head":
                self.head_person, self.head_user = person, user
            if person.shift and person.app_role == "supervisor":
                self.supervisor_for[person.shift] = (person, user)
            if person.shift and person.app_role == "quality":
                self.inspector_for[person.shift] = (person, user)
            self._audit(
                created + timedelta(minutes=5),
                self.platform[0][0],
                "user_plant_role",
                user["id"],
                "create",
                None,
                {"role": person.app_role, "user": person.name},
                "Onboarding — T5 people imported",
            )

        # A supervisor who has since left: login disabled, history intact.
        leaver = self.s.add(
            "users",
            at=created,
            email=None,
            phone=f"+91{spec.people[1].login[3:5]}{rng.randint(10_000_000, 99_999_999)}",
            full_name=f"{rng.choice(OPERATOR_NAMES)} {rng.choice(INITIALS)}. (left)",
            password_hash=self.g.password_hash("shift1234"),
            preferred_language="ta",
            is_active=False,
            last_login_at=self.onboard_at + timedelta(days=3),
        )
        self.s.add(
            "user_plant_roles",
            at=created,
            user_id=leaver["id"],
            plant_id=self.pid,
            role="supervisor",
        )
        # A contractor who gets escalations but has no login, now inactive.
        self.s.add(
            "alert_recipients",
            at=created,
            plant_id=self.pid,
            name=f"{rng.choice(OPERATOR_NAMES)} {rng.choice(INITIALS)}. (electrical AMC)",
            role_tag="maintenance",
            whatsapp_number=f"+9199{rng.randint(10_000_000, 99_999_999)}",
            escalation_level=2,
            user_id=None,
            is_active=False,
        )
        for user, role in self.platform:
            self.s.add(
                "user_plant_roles",
                at=created - timedelta(days=2),
                user_id=user["id"],
                plant_id=self.pid,
                role=role,
            )

        first_quality = next(p for p in spec.people if p.app_role == "quality")
        self.default_inspector = (first_quality, self.person_user[first_quality.name])

    def _person(self, route: str) -> tuple[str, dict]:
        tag = {"maintenance": "maintenance", "stores": "stores", "quality": "quality"}.get(
            route
        )
        if tag and self.recipients.get(tag):
            recipient = self.recipients[tag][0]
            return recipient["name"], self.person_user.get(recipient["name"])
        first_sup = next(iter(self.supervisor_for.values()))
        return first_sup[0].name, first_sup[1]

    def _families_and_rates(self) -> None:
        spec = self.spec
        self.family_rows = {}
        for code, name, description in spec.families:
            self.family_rows[code] = self.s.add(
                "cost_families",
                at=self.onboard_at,
                plant_id=self.pid,
                code=code,
                name=name,
                description=description,
            )

        hours = 200 * len(spec.shifts)
        machines = max(
            1, sum(1 for st in spec.stations if st.section in ("machining", "assembly"))
        )
        consultant = self.platform[0][0]
        for rate in spec.rates:
            effective = rate.effective_from or self.onboard_day
            scope_ref = (
                str(self.family_rows[rate.ref]["id"])
                if rate.scope == "cost_family"
                else rate.ref
            )
            entered = (
                at(effective - timedelta(days=2), "11:15")
                if rate.effective_from
                else self.onboard_at
            )
            row = self.s.add(
                "cost_rates",
                at=entered,
                plant_id=self.pid,
                scope=rate.scope,
                scope_ref=scope_ref,
                machine_rate=rupees(rate.machine),
                labour_rate=rupees(rate.labour),
                overhead_rate=rupees(rate.overhead),
                source=rate.source,
                derivation=self._derivation(rate, hours, machines),
                effective_from=effective,
                effective_to=None,
            )
            self.rate_rows.append(
                SimpleNamespace(
                    scope=rate.scope,
                    ref=scope_ref,
                    eff=effective,
                    machine=row["machine_rate"],
                    labour=row["labour_rate"],
                    overhead=row["overhead_rate"],
                    row=row,
                )
            )
            if rate.effective_from:
                self._audit(
                    entered,
                    consultant,
                    "cost_rate",
                    row["id"],
                    "create",
                    None,
                    {
                        "scope": rate.scope,
                        "reference": rate.ref,
                        "machine_rate": row["machine_rate"],
                        "labour_rate": row["labour_rate"],
                        "overhead_rate": row["overhead_rate"],
                        "effective_from": effective.isoformat(),
                    },
                    rate.note or "Rate correction — applies forward only",
                )

        groups = defaultdict(list)
        for rate in self.rate_rows:
            groups[(rate.scope, rate.ref)].append(rate)
        for rows in groups.values():
            rows.sort(key=lambda r: r.eff)
            for older, newer in pairwise(rows):
                older.row["effective_to"] = newer.eff - timedelta(days=1)
                older.row["updated_at"] = newer.row["created_at"]

    def _derivation(self, rate, hours: int, machines: int) -> dict:
        """The owner's own numbers behind a rate, reproducible with the kernel."""
        note = rate.note
        if rate.source == "default":
            return {
                "note": note
                or "Pre-loaded default library value (±30%). Replace with the plant's figures.",
                "library": "msme-2026.1",
                "accuracy": "±30%",
            }

        def split(total_rupees: int, shares: list[float]) -> list[int]:
            parts = [int(total_rupees * share) for share in shares[:-1]]
            parts.append(total_rupees - sum(parts))
            return [rupees(p) for p in parts]

        if rate.scope == "cost_family" and self.spec.costing_mode == "basic":
            total = int(Decimal(rate.machine) * hours)
            dep, power, wages, maint, oh = split(total, [0.24, 0.20, 0.30, 0.08, 0.18])
            check = blended_machine_rate(
                monthly_depreciation=dep,
                monthly_power=power,
                monthly_operator_wages=wages,
                monthly_maintenance=maint,
                monthly_overhead_share=oh,
                monthly_available_hours=Decimal(hours),
            )
            assert check == rupees(rate.machine), (check, rate)
            return {
                "method": "blended_machine_rate",
                "units": "paise",
                "monthly_depreciation": dep,
                "monthly_power": power,
                "monthly_operator_wages": wages,
                "monthly_maintenance": maint,
                "monthly_overhead_share": oh,
                "monthly_available_hours": hours,
                "note": note,
                "accuracy": "±15-20%",
            }

        if rate.scope == "cost_family":
            total = int(Decimal(rate.machine) * hours)
            dep, power, tooling, maint = split(total, [0.45, 0.30, 0.15, 0.10])
            check = machine_cost_rate(
                depreciation=dep,
                power=power,
                tooling=tooling,
                maintenance=maint,
                available_hours=Decimal(hours),
            )
            assert check == rupees(rate.machine), (check, rate)
            return {
                "method": "machine_cost_rate",
                "units": "paise",
                "depreciation": dep,
                "power": power,
                "tooling": tooling,
                "maintenance": maint,
                "available_hours": hours,
                "note": note,
            }

        if rate.scope == "section":
            total = int(Decimal(rate.labour) * hours)
            wages, supervisor = split(total, [0.86, 0.14])
            check = labour_cost_rate(
                operator_wages=wages,
                supervisor_share=supervisor,
                worked_hours=Decimal(hours),
            )
            assert check == rupees(rate.labour), (check, rate)
            return {
                "method": "labour_cost_rate",
                "units": "paise",
                "operator_wages": wages,
                "supervisor_share": supervisor,
                "worked_hours": hours,
                "note": note,
            }

        total = int(Decimal(rate.overhead) * hours * machines)
        check = overhead_rate(
            total_factory_overhead=rupees(total),
            total_available_machine_hours=Decimal(hours * machines),
        )
        assert check == rupees(rate.overhead), (check, rate)
        return {
            "method": "overhead_rate",
            "units": "paise",
            "total_factory_overhead": rupees(total),
            "total_available_machine_hours": hours * machines,
            "note": note,
        }

    def _stations(self) -> None:
        for spec in self.spec.stations:
            factor = (
                Decimal(spec.factor)
                if spec.factor
                else (Decimal("1.00") if spec.bottleneck else Decimal("0.40"))
            )
            row = self.s.add(
                "stations",
                at=self.onboard_at,
                plant_id=self.pid,
                code=spec.code,
                name=spec.name,
                station_type=spec.kind,
                section=spec.section,
                is_bottleneck=spec.bottleneck,
                bottleneck_factor=factor,
                cost_family_id=self.family_rows[spec.family]["id"],
                line=spec.line,
                zone=spec.line or spec.section,
                is_active=True,
            )
            reject = next(
                (rate for key, rate in REJECT_BASE if key in spec.family.upper()), 0.01
            )
            self.stations[spec.code] = SimpleNamespace(
                code=spec.code,
                spec=spec,
                row=row,
                id=row["id"],
                family=spec.family,
                family_id=row["cost_family_id"],
                section=spec.section,
                producing=spec.section in ("machining", "assembly"),
                bf=factor,
                reject=reject,
                line_code="".join(
                    w[0] for w in (spec.line or spec.section).split()
                ).upper(),
            )
            self.active[spec.code] = None
        self.producing = [st for st in self.stations.values() if st.producing]
        self.producing_families = {st.family for st in self.producing}
        self.qc_station = next(
            st for st in self.stations.values() if st.section == "quality"
        )
        self.dispatch_station = next(
            st for st in self.stations.values() if st.section == "dispatch"
        )
        self.family_section = {st.family: st.section for st in self.producing}

    def _parts(self) -> None:
        spec = self.spec
        self.band_values: dict[tuple[str, str], int] = {}
        for family, values in spec.margin_bands.items():
            for band, value in zip(("L", "M", "H"), values, strict=True):
                self.s.add(
                    "margin_band_values",
                    at=self.onboard_at,
                    plant_id=self.pid,
                    part_family=family,
                    band=band,
                    value=rupees(value),
                )
                self.band_values[(family, band)] = rupees(value)

        superseded = spec.parts[0]
        catalogue = [(p, True) for p in spec.parts] + [
            (
                type(superseded)(
                    f"{superseded.number}-R0",
                    f"{superseded.description} (superseded rev)",
                    superseded.customer,
                    superseded.family,
                    superseded.margin,
                    superseded.band,
                    superseded.material,
                    superseded.routing,
                    0,
                    superseded.lot_size,
                ),
                False,
            )
        ]
        family_names = {code: name for code, name, _ in spec.families}
        for part, active in catalogue:
            row = self.s.add(
                "parts",
                at=self.onboard_at,
                plant_id=self.pid,
                part_number=part.number,
                description=part.description,
                customer_name=part.customer,
                part_family=part.family,
                contribution_margin=rupees(part.margin) if part.margin else None,
                margin_band=part.band,
                material_cost=rupees(part.material),
                is_active=active,
            )
            ops = []
            for index, op in enumerate(part.routing):
                producing = op.family in self.producing_families
                if producing:
                    section, name = self.family_section[op.family], family_names[op.family]
                elif index == len(part.routing) - 1:
                    section, name = "dispatch", "Pack & Dispatch"
                else:
                    section, name = "quality", "Final Inspection"
                op_row = self.s.add(
                    "routing_operations",
                    at=self.onboard_at,
                    plant_id=self.pid,
                    part_id=row["id"],
                    sequence=(index + 1) * 10,
                    operation_code=op.code,
                    name=name,
                    section=section,
                    cost_family_id=self.family_rows[op.family]["id"],
                    cycle_time_min=Decimal(op.cycle_min),
                    cycle_time_source=op.source,
                )
                ops.append(
                    SimpleNamespace(
                        index=index,
                        code=op.code,
                        row=op_row,
                        id=op_row["id"],
                        family=op.family,
                        family_id=op_row["cost_family_id"],
                        ct=Decimal(op.cycle_min),
                        producing=producing,
                        section=section,
                        name=name,
                    )
                )
            margin = (
                rupees(part.margin)
                if part.margin
                else self.band_values.get((part.family, part.band), 0)
            )
            self.parts[part.number] = SimpleNamespace(
                number=part.number,
                spec=part,
                row=row,
                id=row["id"],
                ops=ops,
                margin=margin,
                material=row["material_cost"],
                active=active,
            )
        self.first_op_families = {p.ops[0].family for p in self.parts.values() if p.active}

    def _reasons_and_defects(self) -> None:
        library = list(REASON_LIBRARY) + list(self.spec.extra_reasons)
        library.append(
            (
                "OTHER",
                "Other (retired — pick a specific reason)",
                "மற்றவை",
                "R",
                "unplanned",
                False,
                "production",
                False,
                99,
            )
        )
        for code, label, label_ta, rs, oee, breakdown, route, safety, order in library:
            row = self.s.add(
                "reason_codes",
                at=self.onboard_at,
                plant_id=self.pid,
                code=code,
                label=label,
                label_ta=label_ta,
                recoverability=rs,
                oee_treatment=oee,
                is_breakdown=breakdown,
                escalation_route=route,
                safety_flag=safety,
                display_order=order,
                is_active=code != "OTHER",
            )
            self.reasons[code] = SimpleNamespace(
                code=code,
                label=label,
                rs=rs,
                oee=oee,
                breakdown=breakdown,
                route=route,
                safety=safety,
                row=row,
                id=row["id"],
            )

        for order, (code, label, family, tamil_key) in enumerate(
            self.spec.defects, start=1
        ):
            self.s.add(
                "defect_codes",
                at=self.onboard_at,
                plant_id=self.pid,
                code=code,
                label=label,
                label_ta=DEFECT_TAMIL.get(tamil_key),
                part_family=family,
                display_order=order,
                is_active=True,
            )
            self.defects_by_family[family].append(code)
            self.defect_labels[code] = label

    def _shifts(self) -> None:
        for index, sh in enumerate(self.spec.shifts, start=1):
            start, end = time.fromisoformat(sh.start), time.fromisoformat(sh.end)
            row = self.s.add(
                "shifts",
                at=self.onboard_at,
                plant_id=self.pid,
                name=sh.name,
                start_time=start,
                end_time=end,
                break_minutes=sh.break_minutes,
                weekly_off=sh.weekly_off,
                display_order=index,
                is_active=True,
            )
            duration = int(
                (
                    datetime.combine(date.min, end) - datetime.combine(date.min, start)
                ).total_seconds()
                // 60
            )
            if duration <= 0:
                duration += 24 * 60
            self.shifts.append(
                SimpleNamespace(
                    name=sh.name,
                    row=row,
                    id=row["id"],
                    start=start,
                    duration=duration,
                    planned=duration - sh.break_minutes,
                    break_minutes=sh.break_minutes,
                    letter=sh.name.split()[-1][0],
                    index=index - 1,
                )
            )
        # The shift the plant ran before go-live, kept for history but inactive.
        self.s.add(
            "shifts",
            at=self.onboard_at,
            plant_id=self.pid,
            name="General Shift (pre go-live)",
            start_time=time(9, 0),
            end_time=time(17, 30),
            break_minutes=60,
            weekly_off="Sun",
            display_order=9,
            is_active=False,
        )

    def _operators_and_skills(self) -> None:
        spec, rng = self.spec, self.rng
        pool = [f"{name} {initial}." for name in OPERATOR_NAMES for initial in INITIALS]
        rng.shuffle(pool)
        crews = len(self.shifts)
        total = spec.operators_per_shift * crews + 3
        for index in range(total + 2):
            active = index < total
            row = self.s.add(
                "operators",
                at=self.onboard_at,
                plant_id=self.pid,
                name=pool[index],
                employee_code=f"{spec.operator_prefix}-E{index + 1:03d}",
                certified_station_ids=None,
                is_active=active,
            )
            crew = index % crews if index < spec.operators_per_shift * crews else None
            self.operators.append(
                SimpleNamespace(
                    row=row, id=row["id"], name=pool[index], crew=crew, active=active
                )
            )

        skill_day = self.spec.go_live + timedelta(
            days=min(10, max(1, (self.as_of - self.spec.go_live).days - 1))
        )
        created = at(skill_day, "15:00")
        thin = {c.station for c in spec.chronic if c.reason == "NO_OPERATOR"}
        thin.add(next(st.code for st in self.producing if st.spec.bottleneck))
        uncovered = next(
            st.code
            for st in reversed(self.producing)
            if st.code not in thin and not st.spec.bottleneck
        )
        levels: dict[tuple, int] = {}
        stations = list(self.producing)
        for crew in range(crews):
            members = [o for o in self.operators if o.crew == crew and o.active]
            rng.shuffle(stations)
            for k, st in enumerate(stations):
                primary = members[k % len(members)]
                levels[(primary.id, st.code)] = max(
                    levels.get((primary.id, st.code), 0), 3 if rng.random() < 0.3 else 2
                )
                second = members[(k + 3) % len(members)]
                levels.setdefault((second.id, st.code), 2)
                if rng.random() < 0.4:
                    third = members[(k + 7) % len(members)]
                    levels.setdefault((third.id, st.code), 2 if rng.random() < 0.6 else 1)
        for floater in [o for o in self.operators if o.crew is None and o.active]:
            for st in rng.sample(self.producing, k=min(4, len(self.producing))):
                levels.setdefault((floater.id, st.code), 2)
        # Thin-coverage stations: exactly one competent operator plant-wide.
        for code in thin:
            holders = [
                key
                for key, level in levels.items()
                if key[1] == code and level >= COMPETENT
            ]
            for key in holders[1:]:
                levels[key] = 1
        # A newly installed machine nobody is certified on yet — trainees only.
        for key in [key for key in levels if key[1] == uncovered]:
            levels[key] = 1
        for crew in range(crews):
            member = next(o for o in self.operators if o.crew == crew and o.active)
            levels[(member.id, uncovered)] = 1
        # Everyone has at least one trainee somewhere.
        for st in self.producing:
            trainee = rng.choice([o for o in self.operators if o.active])
            levels.setdefault((trainee.id, st.code), 1)

        by_id = {o.id: o for o in self.operators}
        for (operator_id, code), level in levels.items():
            certified_on = date(2023, 1, 1) + timedelta(
                days=rng.randint(0, (self.onboard_day - date(2023, 1, 1)).days)
            )
            row = self.s.add(
                "operator_skills",
                at=created,
                plant_id=self.pid,
                operator_id=operator_id,
                station_id=self.stations[code].id,
                level=level,
                certified_on=certified_on if level >= COMPETENT else None,
                expires_on=certified_on + timedelta(days=730)
                if level >= COMPETENT
                else None,
                note=rng.choice(
                    [
                        None,
                        None,
                        None,
                        "Certified after 3 supervised shifts",
                        "Needs refresher on first-off gauging",
                        "Can train others",
                    ]
                )
                if level
                else None,
            )
            self.skills[(operator_id, code)] = row
        self._refresh_certified(created)
        self.by_operator = by_id

    def _refresh_certified(self, when: datetime) -> None:
        for operator in self.operators:
            ids = [
                self.stations[code].id
                for (oid, code), row in self.skills.items()
                if oid == operator.id and row["level"] >= COMPETENT
            ]
            operator.row["certified_station_ids"] = ids or None
            operator.row["updated_at"] = max(operator.row["updated_at"], when)

    def _coverage(self, code: str) -> int:
        return sum(
            1
            for (oid, c), row in self.skills.items()
            if c == code and row["level"] >= COMPETENT and self.by_operator[oid].active
        )

    def _targets(self) -> None:
        for st in self.producing:
            cycles = [
                op.ct
                for part in self.parts.values()
                if part.active
                for op in part.ops
                if op.family == st.family
            ]
            cycle = sum(cycles) / len(cycles)
            for sh in self.shifts:
                nominal = int(Decimal(sh.planned) / cycle * Decimal("0.85"))
                target = max(10, round(nominal, -1 if nominal < 5000 else -2))
                self.target_rows.append(
                    self.s.add(
                        "production_targets",
                        at=self.onboard_at,
                        plant_id=self.pid,
                        line=st.spec.line,
                        station_id=st.id,
                        shift_id=sh.id,
                        target_parts=target,
                        effective_from=self.spec.go_live,
                    )
                )
                if self.spec.code == "KUMARAN":
                    revised = date(2026, 8, 1)
                    self.target_rows.append(
                        self.s.add(
                            "production_targets",
                            at=at(revised - timedelta(days=3), "12:00"),
                            plant_id=self.pid,
                            line=st.spec.line,
                            station_id=st.id,
                            shift_id=sh.id,
                            target_parts=int(round(target * 1.05, -1)),
                            effective_from=revised,
                        )
                    )

    # ======================================================================
    # onboarding: T1-T7 forms, uploads, clauses, imported memory
    # ======================================================================

    def _forms_and_uploads(self) -> None:
        spec, rng = self.spec, self.rng
        forms: dict[str, list[dict]] = {}
        forms["T1"] = [
            {
                "Station ID": st.code,
                "Station name": st.spec.name,
                "Type / family": st.spec.kind,
                "Section": st.section.title(),
                "Bottleneck?": "Y" if st.spec.bottleneck else "N",
                "Cost family": st.family,
                "Line": st.spec.line or "",
                "Zone": st.spec.line or st.section.title(),
            }
            for st in self.stations.values()
        ]
        forms["T2"] = [
            {
                "Part number": p.number,
                "Description": p.spec.description,
                "Customer name": p.spec.customer,
                "Part family": p.spec.family,
                "Routing": " > ".join(f"{op.code} {op.family}" for op in p.ops),
                "Contribution": float(Decimal(p.spec.margin)) if p.spec.margin else "",
                "Margin band": p.spec.band or "",
                "Material cost": float(Decimal(p.spec.material)),
            }
            for p in self.parts.values()
            if p.active
        ]
        forms["T3"] = [
            {
                "Part number": p.number,
                "Operation": op.code,
                "Station family": op.family,
                "Cycle time (min/part)": float(op.ct),
                "Source": op.row["cycle_time_source"],
            }
            for p in self.parts.values()
            if p.active
            for op in p.ops
        ]
        forms["T4"] = []
        for rate in self.spec.rates:
            for kind, value in (
                ("machine", rate.machine),
                ("labour", rate.labour),
                ("overhead", rate.overhead),
            ):
                if Decimal(value):
                    forms["T4"].append(
                        {
                            "Scope": rate.scope,
                            "Reference": rate.ref,
                            "Rate type": kind,
                            "Value": float(Decimal(value)),
                            "Source": rate.source,
                            "Effective from": (
                                rate.effective_from or self.onboard_day
                            ).isoformat(),
                        }
                    )
        forms["T5"] = [
            {
                "Name": p.name,
                "Kind": "alert_recipient",
                "Role tag": p.role_tag,
                "WhatsApp number": self.recipients[p.role_tag][0]["whatsapp_number"]
                if p.role_tag in ("owner",)
                else (p.login if "@" not in p.login else ""),
                "Escalation level": p.level,
                "Employee code": "",
            }
            for p in spec.people
        ] + [
            {
                "Name": o.name,
                "Kind": "operator",
                "Role tag": "",
                "WhatsApp number": "",
                "Escalation level": "",
                "Employee code": o.row["employee_code"],
            }
            for o in self.operators
            if o.active
        ]
        forms["T6"] = [
            {
                "Kind": "downtime",
                "Code": r.code,
                "Label": r.label,
                "Recoverability": r.rs,
                "OEE treatment": r.oee,
                "Is breakdown": "Y" if r.breakdown else "N",
                "Escalation route": r.route,
                "Safety flag": "Y" if r.safety else "N",
                "Part family": "",
                "Label (Tamil)": r.row["label_ta"] or "",
            }
            for r in self.reasons.values()
            if r.row["is_active"]
        ] + [
            {
                "Kind": "defect",
                "Code": code,
                "Label": label,
                "Recoverability": "",
                "OEE treatment": "",
                "Is breakdown": "",
                "Escalation route": "",
                "Safety flag": "",
                "Part family": family or "",
                "Label (Tamil)": DEFECT_TAMIL.get(key, ""),
            }
            for code, label, family, key in spec.defects
        ]
        forms["T7"] = [
            {
                "Kind": "shift",
                "Name": sh.name,
                "Start time": sh.start.strftime("%H:%M"),
                "End time": sh.row["end_time"].strftime("%H:%M"),
                "Break minutes": sh.break_minutes,
                "Weekly off": "Sun",
                "Line": "",
                "Target parts": "",
            }
            for sh in self.shifts
        ] + [
            {
                "Kind": "target",
                "Name": st.code,
                "Start time": "",
                "End time": "",
                "Break minutes": "",
                "Weekly off": "",
                "Line": st.spec.line or "",
                "Target parts": t["target_parts"],
            }
            for st in self.producing
            for t in self.s.rows["production_targets"]
            if t["station_id"] == st.id
            and t["shift_id"] == self.shifts[0].id
            and t["effective_from"] == spec.go_live
        ]
        self.g.forms[spec.code] = forms

        from app.ingest.templates import TEMPLATES

        consultant = self.platform[0][0]
        for number, code in enumerate(["T1", "T2", "T3", "T4", "T5", "T6", "T7"]):
            name = TEMPLATES[code].name.replace(" & ", "_").replace(" ", "_")
            filename = f"{spec.code}_{code}_{name}.xlsx"
            uploaded = at(
                self.onboard_day - timedelta(days=3 - number // 3),
                f"{10 + number}:{rng.randint(0, 59):02d}",
            )
            rows = forms[code]
            if code == "T2" and spec.code != "KOVAI":
                # The commonest onboarding failure: the plant's own layout, not ours.
                missing = "Description"
                self.s.add(
                    "template_uploads",
                    at=uploaded - timedelta(hours=2),
                    plant_id=self.pid,
                    template=code,
                    filename=f"{spec.code}_part_list_from_tally.xlsx",
                    storage_url=f"uploads/onboarding/{spec.code}/{spec.code}_part_list_from_tally.xlsx",
                    uploaded_by_id=consultant["id"],
                    status="failed",
                    rows_found=0,
                    rows_imported=0,
                    preview=None,
                    errors=[
                        {
                            "row": 1,
                            "column": missing,
                            "message": f"{TEMPLATES[code].name} needs these columns: {missing}. Expected header row: {', '.join(TEMPLATES[code].columns)}",
                        }
                    ],
                    confirmed_at=None,
                )
            errors = None
            if code == "T3" and spec.code == "SAKTHI":
                errors = [
                    {
                        "row": 4,
                        "column": "Cycle time (min/part)",
                        "message": "'0,04' is not a number",
                    }
                ]
            upload = self.s.add(
                "template_uploads",
                at=uploaded,
                plant_id=self.pid,
                template=code,
                filename=filename,
                storage_url=f"uploads/onboarding/{spec.code}/{filename}",
                uploaded_by_id=consultant["id"],
                status="confirmed",
                rows_found=len(rows) + (1 if errors else 0),
                rows_imported=len(rows),
                preview=rows[:5],
                errors=errors,
                confirmed_at=uploaded + timedelta(minutes=rng.randint(8, 50)),
            )
            upload["updated_at"] = upload["confirmed_at"]
            self._audit(
                upload["confirmed_at"],
                consultant,
                "template_upload",
                upload["id"],
                "update",
                {"status": "parsed"},
                {"status": "confirmed", "rows_imported": len(rows)},
                f"Found: {len(rows)} {TEMPLATES[code].noun}. Confirmed.",
            )

        created = at(
            spec.go_live
            + timedelta(days=min(14, max(0, (self.as_of - spec.go_live).days - 1))),
            "16:00",
        )
        for entry in CLAUSE_LIBRARY:
            self.clause_rows.append(
                self.s.add(
                    "compliance_clauses",
                    at=created,
                    plant_id=self.pid,
                    standard=entry["standard"].value,
                    clause_ref=entry["clause_ref"],
                    title=entry["title"],
                    requirement=entry.get("requirement"),
                    evidence_source=entry["evidence_source"],
                    status="gap",
                    evidence_count=0,
                    last_evidence_at=None,
                    gap_note=None,
                    also_satisfies=entry.get("also_satisfies"),
                    display_order=entry.get("display_order", 100),
                )
            )
        # A customer-specific clause the scorer cannot evaluate: its status is the auditor's, not the system's.
        customer = spec.parts[0].customer
        self.s.add(
            "compliance_clauses",
            at=created,
            plant_id=self.pid,
            standard="customer",
            clause_ref="SQA-4.2",
            title=f"{customer} supplier quality manual — monthly PPM report",
            requirement="Submit the monthly PPM and top-3 defect Pareto by the 5th of each month.",
            evidence_source="manual_customer_report",
            status="partial" if spec.code != "SAKTHI" else "gap",
            evidence_count=0,
            last_evidence_at=None,
            gap_note="Report template agreed; two of the last three months submitted late.",
            also_satisfies=["iso_9001:9.1.2"],
            display_order=10,
        )
        if spec.code == "KOVAI":
            self.s.add(
                "compliance_clauses",
                at=created,
                plant_id=self.pid,
                standard="customer",
                clause_ref="PED-QA-7",
                title="Pressure Equipment Directive traceability (export valves)",
                requirement="Heat-number traceability to the finished valve body.",
                evidence_source="manual_customer_report",
                status="not_applicable",
                evidence_count=0,
                last_evidence_at=None,
                gap_note="No export orders this year.",
                also_satisfies=None,
                display_order=20,
            )

    def _historical_memory(self) -> None:
        """Entries typed in from the old maintenance register at onboarding."""
        rng = self.rng
        maintenance = self._person("maintenance")[0]
        for _ in range(rng.randint(6, 10)):
            chronic = rng.choice(self.spec.chronic)
            code = chronic.reason if chronic.reason in texts.FIXES else "MACHINE_BREAKDOWN"
            root, fix, replaced, check, _whys = rng.choice(texts.FIXES[code])
            st = self.stations[chronic.station]
            occurred = self.spec.go_live - timedelta(days=rng.randint(40, 420))
            self.memory.append(
                self.s.add(
                    "memory_entries",
                    at=self.onboard_at + timedelta(days=rng.randint(1, 5)),
                    plant_id=self.pid,
                    action_id=None,
                    station_id=st.id,
                    part_id=None,
                    reason_code_id=self.reasons[code].id if code in self.reasons else None,
                    what_fixed_it=fix.format(station=st.code),
                    what_was_replaced=replaced,
                    photo_url=None,
                    check_first_next_time=check.format(station=st.code),
                    problem_summary=f"{self.reasons[code].label} on {st.code} ({root.format(station=st.code).lower()})",
                    tags=[
                        "imported",
                        "maintenance-register",
                        code.lower(),
                        st.family.lower(),
                    ],
                    fixed_by_name=maintenance,
                    occurred_on=occurred,
                    embedding_status="done",
                )
            )

    # ======================================================================
    # the daily loop
    # ======================================================================

    def _simulate(self) -> None:
        spec = self.spec
        live_days = max(1, (self.as_of - spec.go_live).days)
        self.kaizen_rate = spec.kaizen_ideas / live_days
        for day in daterange(spec.go_live, self.as_of):
            if not working_day(day):
                continue
            self._finish_jobs(day)
            self._coach(day)
            for sh in self.shifts:
                start = at(day, sh.start)
                if start >= self.now:
                    continue
                self._shift(day, sh, start, start + timedelta(minutes=sh.duration))
            self._advance_actions(day)
            self._worker(day)
            self._kaizen(day)
            self._manual_actions(day)
            self._copilot(day)
            self._periodic(day)

    # ----------------------------------------------------------------------
    # costing
    # ----------------------------------------------------------------------

    def _rate(self, scope: str, ref: str, day: date, column: str) -> int:
        best = None
        for rate in self.rate_rows:
            if (
                rate.scope == scope
                and rate.ref == ref
                and rate.eff <= day
                and (best is None or rate.eff > best.eff)
            ):
                best = rate
        return getattr(best, column) if best else 0

    def _rates(self, family_id, section: str, day: date) -> CostRates:
        return CostRates(
            machine_rate=self._rate("cost_family", str(family_id), day, "machine"),
            labour_rate=self._rate("section", section, day, "labour"),
            overhead_rate=self._rate("plant", "plant", day, "overhead"),
        )

    def _snapshot(self, st, part, op, day: date) -> CostSnapshot:
        """``services.costing.resolve_snapshot`` over in-memory rows."""
        key = (st.code, part.number if part else None, op.code if op else None, day)
        if key not in self.snapshot_cache:
            cycle = Decimal(0)
            if op is not None and op.ct:
                cycle = op.ct
            elif part is not None:
                match = next(
                    (o for o in part.ops if o.family_id == st.family_id), part.ops[0]
                )
                cycle = match.ct
            self.snapshot_cache[key] = CostSnapshot(
                rates=self._rates(st.family_id, st.section, day),
                part=PartEconomics(
                    cycle_time_min=cycle,
                    contribution_margin=part.margin if part else 0,
                    material_cost=part.material if part else 0,
                ),
                bottleneck_factor=st.bf,
                costing_mode=CostingMode(self.spec.costing_mode),
                formula_version=FORMULA_VERSION,
            )
        return self.snapshot_cache[key]

    @staticmethod
    def _apply_snapshot(row: dict, snapshot: CostSnapshot) -> None:
        row["snapshot_machine_rate"] = snapshot.rates.machine_rate
        row["snapshot_labour_rate"] = snapshot.rates.labour_rate
        row["snapshot_overhead_rate"] = snapshot.rates.overhead_rate
        row["snapshot_cycle_time_min"] = snapshot.part.cycle_time_min or None
        row["snapshot_contribution_margin"] = snapshot.part.contribution_margin
        row["snapshot_bottleneck_factor"] = snapshot.bottleneck_factor
        row["snapshot_costing_mode"] = snapshot.costing_mode.value
        row["formula_version"] = snapshot.formula_version

    def _completed_ops(self, part, st, day: date) -> list[tuple[Decimal, CostRates]]:
        completed = []
        for op in part.ops:
            if op.ct:
                completed.append((op.ct, self._rates(op.family_id, op.section, day)))
            if op.family_id == st.family_id:
                break
        return completed

    # ----------------------------------------------------------------------
    # jobs and lots
    # ----------------------------------------------------------------------

    def _new_job(self, st, when: datetime, *, urgent: bool = False) -> Job | None:
        rng = self.rng
        from_stock = st.family not in self.first_op_families
        choices = []
        for part in self.parts.values():
            if not part.active or not part.spec.demand:
                continue
            for op in part.ops:
                if op.family == st.family and (op.index == 0 or from_stock):
                    choices.append((part, op.index))
                    break
        if not choices:
            return None
        part, index = rng.choices(choices, weights=[p.spec.demand for p, _ in choices])[0]
        low, high = part.spec.lot_size
        # Lots sized so a job runs for days, not hours: most stations change job
        # once or twice a week, which is what a tier-2 job shop actually does.
        scale = 1 if self.spec.code == "SAKTHI" else 3
        quantity = rng.randint(low, high) * scale // (2 if from_stock else 1)
        self.job_seq += 1
        number = {
            "KUMARAN": f"{2200 + self.job_seq}",
            "SRIVARI": f"SVP-{5100 + self.job_seq}",
            "KOVAI": f"JO-26-{self.job_seq:04d}",
            "SAKTHI": f"W{3300 + self.job_seq}",
        }.get(self.spec.code, str(self.job_seq))
        created = max(self.onboard_at, when - timedelta(hours=rng.randint(6, 72)))
        notes = None
        if from_stock:
            notes = (
                f"{part.ops[0].code} done before go-live; machined stock drawn from stores."
            )
        elif urgent:
            notes = rng.choice(
                [
                    "Urgent — line stopper at customer.",
                    "Customer pulled the schedule in by two days.",
                    "Owner priority: advance shipment.",
                ]
            )
        elif rng.random() < 0.08:
            notes = rng.choice(
                [
                    "Material: heat no. on mill cert",
                    "Split across two machines if CNC-3 is down",
                    "Customer audit lot — 100% inspection",
                ]
            )
        row = self.s.add(
            "job_orders",
            at=created,
            plant_id=self.pid,
            job_number=number,
            part_id=part.id,
            customer_name=part.spec.customer,
            planned_quantity=quantity,
            status="running",
            due_date=(when + timedelta(days=rng.randint(2, 10))).date(),
            opened_at=when,
            completed_at=None,
            notes=notes,
        )
        job = Job(
            row=row,
            part=part,
            op_index=index,
            target=quantity,
            ready_at=when,
            start_index=index,
            imported=from_stock,
        )
        if self.spec.lot_granularity == "job_lot":
            # Lot quantity is what physically enters the routing, not the plan:
            # it grows with the first stage's output.
            job.lot = self.s.add(
                "lots",
                at=when,
                plant_id=self.pid,
                lot_code=number,
                job_order_id=row["id"],
                granularity="job_lot",
                quantity=0,
                opened_at=when,
                closed_at=None,
            )
        self.jobs.append(job)
        return job

    def _next_job(self, st, when: datetime) -> Job | None:
        for job in self.queue[st.family]:
            if job.ready_at <= when:
                self.queue[st.family].remove(job)
                job.paused = False
                return job
        if st.family in self.first_op_families:
            return None if self.rng.random() < 0.03 else self._new_job(st, when)
        return self._new_job(st, when) if self.rng.random() < 0.55 else None

    def _lot_for(
        self, job: Job, st, sh, day: date, shift_start: datetime, shift_end: datetime
    ) -> dict:
        granularity = self.spec.lot_granularity
        if granularity == "job_lot":
            return job.lot
        if granularity == "shift_batch":
            key = (st.line_code, day, sh.letter)
            code = f"{st.line_code}-{day:%d%m%y}-{sh.letter}"
            opened = shift_start
        else:
            key = (st.line_code, day)
            code = f"{st.line_code}-{day:%d%m%y}"
            opened = at(day, self.shifts[0].start)
        if key not in self.batch_lots:
            self.batch_lots[key] = self.s.add(
                "lots",
                at=opened,
                plant_id=self.pid,
                lot_code=code,
                job_order_id=None,
                granularity=granularity,
                quantity=0,
                opened_at=opened,
                closed_at=None,  # set at despatch, as traceability.record_dispatch does
            )
        return self.batch_lots[key]

    def _complete_op(self, job: Job, when: datetime) -> None:
        good = max(0, job.done - job.scrap)
        passed = [[lot, qty] for lot, qty in job.output.values() if qty > 0]
        assert sum(q for _, q in passed) == good, (job.row["job_number"], good, passed)
        job.output = {}
        nxt = job.op_index + 1
        ops = job.part.ops
        if good <= 0:
            return
        if nxt < len(ops) and ops[nxt].producing:
            job.op_index, job.target, job.done, job.scrap = nxt, good, 0, 0
            job.ledger = passed
            job.ready_at = when + timedelta(minutes=30)
            self.queue[ops[nxt].family].append(job)
        else:
            job.finish_good, job.finish_from, job.finish_lots = good, nxt, passed
            job.ready_day = next_working_day(when.date())
            self.finishing.append(job)

    def _finish_jobs(self, day: date) -> None:
        """QC and dispatch operations for jobs whose last machine op is done."""
        rng = self.rng
        for job in list(self.finishing):
            if job.ready_day > day:
                continue
            clock = at(day, "09:30") + timedelta(minutes=rng.randint(0, 240))
            if clock + timedelta(hours=4 + 2 * len(job.finish_lots)) > self.now:
                continue
            ops = job.part.ops[job.finish_from :]
            last = ops[-1]
            for lot, quantity in job.finish_lots:
                for op in ops[:-1]:
                    rejected = int(quantity * rng.uniform(0, 0.004))
                    self.s.add(
                        "stage_completions",
                        at=clock,
                        plant_id=self.pid,
                        lot_id=lot["id"],
                        routing_operation_id=op.id,
                        station_id=self.qc_station.id,
                        business_date=clock.date(),
                        quantity_completed=quantity - rejected,
                        quantity_rejected=rejected,
                        started_at=clock
                        - timedelta(minutes=int(op.ct * quantity) % 400 + 20),
                        completed_at=clock,
                        source="manual",
                    )
                    quantity -= rejected
                    clock += timedelta(minutes=rng.randint(20, 60))
                splits = (
                    [quantity]
                    if quantity < 400 or rng.random() < 0.6
                    else [quantity // 2, quantity - quantity // 2]
                )
                for split in splits:
                    if split <= 0:
                        continue
                    vehicle = f"{VEHICLE_PREFIX.get(self.spec.code, 'TN 01')} {rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')} {rng.randint(1000, 9999)}"
                    self.s.add(
                        "stage_completions",
                        at=clock,
                        plant_id=self.pid,
                        lot_id=lot["id"],
                        routing_operation_id=last.id,
                        station_id=self.dispatch_station.id,
                        business_date=clock.date(),
                        quantity_completed=split,
                        quantity_rejected=0,
                        started_at=clock - timedelta(minutes=25),
                        completed_at=clock,
                        source="manual",
                    )
                    self.s.add(
                        "dispatch_records",
                        at=clock,
                        plant_id=self.pid,
                        lot_id=lot["id"],
                        business_date=clock.date(),
                        quantity=split,
                        vehicle_reference=vehicle,
                        despatched_at=clock,
                        source="manual",
                    )
                    # ``traceability.record_dispatch`` closes the lot at every despatch.
                    lot.update(closed_at=clock, updated_at=clock)
                    despatched = clock
                    clock += timedelta(minutes=rng.randint(30, 150))
            job.row.update(
                status="completed", completed_at=despatched, updated_at=despatched
            )
            self.finishing.remove(job)

    # ----------------------------------------------------------------------
    # one shift
    # ----------------------------------------------------------------------

    def _shift(self, day: date, sh, start: datetime, end: datetime) -> None:
        rng = self.rng
        is_open = end > self.now
        supervisor = self.supervisor_for.get(sh.name) or next(
            iter(self.supervisor_for.values())
        )
        inspector = self.inspector_for.get(sh.name) or self.default_inspector
        tapped = start + timedelta(seconds=rng.randint(-300, 420))
        row = self.s.add(
            "shift_sessions",
            at=tapped,
            plant_id=self.pid,
            shift_id=sh.id,
            business_date=day,
            started_at=tapped,
            ended_at=None,
            supervisor_id=supervisor[1]["id"],
            status="open",
            locked_at=None,
            closed_by_id=None,
            override_reason=None,
            override_by_id=None,
            planned_minutes=sh.planned,
            summary=None,
        )
        self.shift_rows.append(row)
        self.first_bd = self.first_bd or day
        break_start = start + timedelta(minutes=min(240, sh.duration // 2))
        break_window = (break_start, break_start + timedelta(minutes=sh.break_minutes))

        power = None
        if rng.random() < (0.04 if self.spec.code == "SAKTHI" else 0.02):
            length = rng.randint(5 * 60, (40 if self.spec.code == "SAKTHI" else 25) * 60)
            begin = start + timedelta(minutes=rng.randint(40, max(41, sh.duration - 80)))
            if (
                begin + timedelta(seconds=length) < min(end, self.now)
                and overlap_s(begin, begin + timedelta(seconds=length), *break_window) == 0
            ):
                power = (begin, begin + timedelta(seconds=length))

        # The live shift at the data cut-off: two machines stopped right now (one
        # still without a reason), and one closed stop with no reason code — the
        # Shift Closure Lock's blocker.
        ctx = SimpleNamespace(
            day=day,
            sh=sh,
            row=row,
            start=start,
            end=end,
            is_open=is_open,
            supervisor=supervisor,
            inspector=inspector,
            break_window=break_window,
            power=power,
            used=set(),
            events=[],
            stations=[],
            open_left=2 if is_open else 0,
            blocker_left=1 if is_open else 0,
        )
        order = (
            rng.sample(self.producing, k=len(self.producing)) if is_open else self.producing
        )
        for st in order:
            self._station_shift(ctx, st)

        if is_open:
            return

        closed_at = end + timedelta(minutes=rng.randint(4, 25))
        row.update(
            status="closed",
            ended_at=closed_at,
            locked_at=closed_at,
            closed_by_id=supervisor[1]["id"],
            updated_at=closed_at,
        )
        if rng.random() < 0.02:
            reason = rng.choice(
                [
                    "Last hour's count not entered on Assy — supervisor left for a family emergency. Owner approved close.",
                    "Tablet battery died at 23:40; counts reconstructed from the register tomorrow.",
                    "Reason code disputed between maintenance and production; owner closed and raised it for the meeting.",
                ]
            )
            row.update(
                override_reason=reason,
                override_by_id=self.owner_user["id"],
                closed_by_id=self.owner_user["id"],
            )
            self._audit(
                closed_at - timedelta(minutes=1),
                self.owner_user,
                "shift_session",
                row["id"],
                "override",
                None,
                {
                    "blockers": [
                        {
                            "kind": "missing_production",
                            "detail": f"{rng.choice(self.producing).code}: no production count entered",
                        }
                    ]
                },
                reason,
            )
        self.closed_shift_times.append(closed_at)

        for session in ctx.stations:
            if session.row["ended_at"] is None:
                session.row["ended_at"] = closed_at
                session.row["updated_at"] = closed_at

        created = self._evaluate_actions(day, row, closed_at)
        row["summary"] = self._summary(ctx, len(created))

    def _pick_operator(self, ctx, st):
        rng, day = self.rng, ctx.day
        if day not in self.absent:
            self.absent[day] = {o.id for o in self.operators if rng.random() < 0.05}
        absent = self.absent[day]

        def ok(operator, minimum):
            skill = self.skills.get((operator.id, st.code))
            return (
                operator.active
                and operator.id not in ctx.used
                and operator.id not in absent
                and skill
                and skill["level"] >= minimum
            )

        tiers = [
            [o for o in self.operators if o.crew == ctx.sh.index and ok(o, COMPETENT)],
            [o for o in self.operators if o.crew is None and ok(o, COMPETENT)],
            [o for o in self.operators if rng.random() < 0.15 and ok(o, COMPETENT)],
            [o for o in self.operators if o.crew == ctx.sh.index and ok(o, 1)],
        ]
        for tier in tiers:
            if tier:
                operator = rng.choice(tier)
                ctx.used.add(operator.id)
                return operator
        return None

    def _draw_events(self, ctx, st, operator) -> list[dict]:
        """Where this station's shift will lose time. Placed without overlaps."""
        rng, spec = self.rng, self.spec
        limit = min(ctx.end, self.now - timedelta(minutes=2)) - timedelta(minutes=3)
        blocked = [ctx.break_window]
        events: list[dict] = []

        def place(
            reason: str,
            low: int,
            high: int,
            *,
            at_start: bool = False,
            source: str = "manual",
        ) -> None:
            length = rng.randint(low * 60, high * 60)
            for _ in range(10):
                if at_start:
                    begin = ctx.start + timedelta(seconds=rng.randint(0, 240))
                else:
                    span = int((limit - ctx.start).total_seconds()) - length - 600
                    if span <= 0:
                        return
                    begin = ctx.start + timedelta(seconds=600 + rng.randint(0, span))
                finish = begin + timedelta(seconds=length)
                if finish > limit:
                    finish = limit
                if finish <= begin:
                    return
                if all(overlap_s(begin, finish, b0, b1) == 0 for b0, b1 in blocked):
                    blocked.append((begin, finish))
                    events.append(
                        {"reason": reason, "start": begin, "end": finish, "source": source}
                    )
                    return
                if at_start:
                    return

        def suppressed(reason: str) -> bool:
            effect = self.fix_effect.get((st.code, reason))
            return bool(effect and effect[0] <= ctx.day and rng.random() > effect[1])

        if ctx.power:
            blocked.append(ctx.power)
            events.append(
                {
                    "reason": "POWER_CUT",
                    "start": ctx.power[0],
                    "end": ctx.power[1],
                    "source": "manual",
                }
            )
        if operator is None and not suppressed("NO_OPERATOR"):
            place("NO_OPERATOR", 12, 40, at_start=True)
        if rng.random() < 0.015 and not suppressed("SHIFT_START_WARMUP"):
            place("SHIFT_START_WARMUP", 4, 12, at_start=True)

        profile = [
            (code, weight * spec.event_weights.get(code, 1.0), low, high)
            for code, weight, low, high in BASE_EVENT_PROFILE
        ]
        for code, _, _, _, _, _, _, _, _ in spec.extra_reasons:
            if code in EXTRA_DURATIONS:
                weight = spec.event_weights.get(code, 0.3 if code == "DIE_CRACK" else 1.0)
                if code == "HT_LOAD_WAIT" and st.family != "HT":
                    continue
                if (
                    code in ("DIE_CHANGE", "COIL_CHANGE", "DIE_CRACK")
                    and "PRESS" not in st.family
                    and st.family not in ("HEAD", "FORGE")
                ):
                    continue
                if code == "CMM_QUEUE" and st.section != "assembly":
                    continue
                profile.append((code, weight, *EXTRA_DURATIONS[code]))
        if st.family not in self.first_op_families:
            profile.append(("WIP_STARVED", 3.0, *EXTRA_DURATIONS["WIP_STARVED"]))
        weights = [w for _, w, _, _ in profile]
        count = rng.choices([0, 1, 2, 3], weights=spec.events_per_shift)[0]
        for _ in range(count):
            code, _, low, high = rng.choices(profile, weights=weights)[0]
            if not suppressed(code):
                place(code, low, high)

        for chronic in spec.chronic:
            if (
                chronic.station == st.code
                and rng.random() < chronic.per_shift
                and not suppressed(chronic.reason)
            ):
                place(chronic.reason, *chronic.minutes)
        return events

    def _station_shift(self, ctx, st) -> None:
        rng, day = self.rng, ctx.day
        job = self.active.get(st.code)
        if job is not None and job.done >= job.target:
            job = None
        if job is None:
            job = self._next_job(st, ctx.start)
        elif st.family in self.first_op_families and rng.random() < 0.025:
            # §14.3 — an urgent job pauses the running one; it never deletes it.
            job.paused = True
            job.ready_at = ctx.start
            self.queue[st.family].insert(0, job)
            job = self._new_job(st, ctx.start, urgent=True)
        self.active[st.code] = job

        operator = self._pick_operator(ctx, st) if job else None
        events = self._draw_events(ctx, st, operator) if job else []
        if job is None:
            if (
                st.family not in self.first_op_families
                and rng.random() < 0.3
                and not ctx.is_open
            ):
                length = rng.randint(10 * 60, 28 * 60)
                begin = ctx.start + timedelta(minutes=rng.randint(30, 200))
                self._record_events(
                    ctx,
                    st,
                    [
                        {
                            "reason": "WIP_STARVED",
                            "start": begin,
                            "end": begin + timedelta(seconds=length),
                            "source": "manual",
                        }
                    ],
                    [],
                )
            return
        if operator is None:
            operator = next(
                (
                    o
                    for o in self.operators
                    if o.crew is None and o.active and o.id not in ctx.used
                ),
                None,
            )
            if operator:
                ctx.used.add(operator.id)

        padded = rng.random() < 0.03
        perf = Decimal(
            str(
                round(
                    rng.uniform(0.72, 0.92)
                    if rng.random() > 0.02
                    else rng.uniform(1.01, 1.05),
                    3,
                )
            )
        )
        sessions: list[Session] = []
        session = self._open_session(
            ctx,
            st,
            job,
            operator,
            ctx.start + timedelta(seconds=rng.randint(60, 420)),
            None,
        )
        sessions.append(session)

        slot_start = ctx.start
        sensor = (
            self.spec.code == "SAKTHI"
            and st.code in ("HDR-1", "HDR-2")
            and day >= date(2026, 9, 1)
        )
        while slot_start < ctx.end:
            slot_end = min(slot_start + timedelta(hours=1), ctx.end)
            if slot_end > self.now:
                break
            blocked = overlap_s(slot_start, slot_end, *ctx.break_window) + sum(
                overlap_s(slot_start, slot_end, e["start"], e["end"]) for e in events
            )
            run = max(0, int((slot_end - slot_start).total_seconds()) - blocked)
            made = int(Decimal(run) / Decimal(60) / session.op.ct * perf)
            remaining = max(0, job.target - job.done)
            made = min(made, remaining)
            found = min(made, round(made * st.reject * rng.uniform(0.2, 1.8)))
            if session.quality_hold:
                found = min(made, found + made // 100)
            # Reworked or concession parts carry on down the routing; only a
            # scrap disposition removes them from the lot.
            scrap = 0 if session.inspect and session.disposition != "scrap" else found
            reported = made + (
                math.ceil(made * 0.09)
                if padded and slot_end > ctx.start + timedelta(hours=4)
                else 0
            )
            recorded = slot_end + timedelta(seconds=rng.randint(30, 540))
            if recorded <= self.now:
                count_row = self.s.add(
                    "production_counts",
                    at=recorded,
                    plant_id=self.pid,
                    station_id=st.id,
                    job_session_id=session.row["id"],
                    shift_session_id=ctx.row["id"],
                    business_date=day,
                    count=reported,
                    recorded_at=recorded,
                    covers_from=slot_start,
                    source="sensor" if sensor else "manual",
                    source_ref=f"pulse-counter/{st.code}" if sensor else None,
                    logged_by_id=None if sensor else ctx.supervisor[1]["id"],
                )
                edited = recorded + timedelta(minutes=rng.randint(2, 50))
                if not sensor and rng.random() < 0.012 and edited <= self.now:
                    typo = (
                        reported + rng.choice([-10, 10, 100, -1, 1])
                        if reported > 100
                        else reported + 1
                    )
                    count_row["updated_at"] = edited
                    self._audit(
                        edited,
                        ctx.supervisor[1],
                        "production_count",
                        count_row["id"],
                        "update",
                        {"count": typo},
                        {"count": reported},
                        "Corrected before shift close",
                    )
            session.true_count += made
            session.reported += reported
            session.scrap += scrap
            session.found += found
            job.done += made
            job.scrap += scrap
            job.worked = True
            self._consume(job, session, made, scrap)
            if job.done >= job.target:
                self._end_session(session, slot_end)
                self._complete_op(job, slot_end)
                self.active[st.code] = None
                if slot_end >= ctx.end or slot_end + timedelta(minutes=30) > min(
                    ctx.end, self.now
                ):
                    break
                new_job = self._next_job(st, slot_end)
                if new_job is None:
                    break
                session.row["ended_by_change_job"] = True
                length = rng.randint(8 * 60, 30 * 60)
                running = [e["end"] for e in events if e["start"] < slot_end < e["end"]]
                change = {
                    "reason": "SETUP_CHANGEOVER",
                    "start": max([slot_end, *running])
                    + timedelta(seconds=rng.randint(5, 90)),
                    "source": "system",
                }
                change["end"] = change["start"] + timedelta(seconds=length)
                events[:] = [
                    e
                    for e in events
                    if overlap_s(e["start"], e["end"], change["start"], change["end"]) == 0
                    or e["start"] < slot_end
                ]
                if change["end"] < ctx.end:
                    events.append(change)
                job = new_job
                self.active[st.code] = job
                session = self._open_session(ctx, st, job, operator, slot_end, sessions[-1])
                sessions.append(session)
            slot_start = slot_end

        if not ctx.is_open and session.row["ended_at"] is None:
            self._end_session(session, ctx.end)

        if ctx.is_open:
            if ctx.open_left:
                begin = self.now - timedelta(seconds=rng.randint(4 * 60, 38 * 60))
                if overlap_s(begin, self.now, *ctx.break_window):
                    # Stopped since before the break and still not running.
                    begin = ctx.break_window[0] - timedelta(
                        seconds=rng.randint(5 * 60, 25 * 60)
                    )
                if all(
                    overlap_s(begin, self.now, e["start"], e["end"]) == 0 for e in events
                ) and begin >= ctx.start + timedelta(minutes=10):
                    reason = (
                        None
                        if ctx.open_left == 2
                        else rng.choice(
                            ["MACHINE_BREAKDOWN", "MATERIAL_WAIT", "TOOL_BREAKAGE"]
                        )
                    )
                    events.append(
                        {"reason": reason, "start": begin, "end": None, "source": "manual"}
                    )
                    ctx.open_left -= 1
            elif ctx.blocker_left:
                closed = [
                    e
                    for e in events
                    if e["end"] is not None
                    and e["source"] == "manual"
                    and e["reason"] != "POWER_CUT"
                ]
                if not closed:
                    begin = ctx.start + timedelta(minutes=rng.randint(30, 150))
                    finish = begin + timedelta(seconds=rng.randint(4 * 60, 9 * 60))
                    if (
                        finish < self.now
                        and all(
                            overlap_s(begin, finish, e["start"], e["end"] or self.now) == 0
                            for e in events
                        )
                        and overlap_s(begin, finish, *ctx.break_window) == 0
                    ):
                        closed = [
                            {
                                "reason": "MINOR_ADJUSTMENT",
                                "start": begin,
                                "end": finish,
                                "source": "manual",
                            }
                        ]
                        events.append(closed[0])
                if closed:
                    closed[-1]["reason"] = (
                        None  # Screen 6 blocker: a stop with no reason code
                    )
                    ctx.blocker_left -= 1

        self._record_events(ctx, st, events, sessions)
        for session in sessions:
            self._close_out_session(ctx, st, session)
        ctx.stations.extend(sessions)
        ctx.events.append((st, sessions, events, perf))

    def _open_session(
        self, ctx, st, job: Job, operator, start: datetime, previous: Session | None
    ) -> Session:
        if job.ledger and not job.imported:
            lot = next((lot for lot, qty in job.ledger if qty > 0), job.ledger[0][0])
        else:
            lot = self._lot_for(job, st, ctx.sh, ctx.day, ctx.start, ctx.end)
        row = self.s.add(
            "job_sessions",
            at=start,
            plant_id=self.pid,
            job_order_id=job.row["id"],
            shift_session_id=ctx.row["id"],
            station_id=st.id,
            lot_id=lot["id"],
            routing_operation_id=job.op.id,
            operator_id=operator.id if operator else None,
            started_at=start,
            ended_at=None,
            ended_by_change_job=False,
            previous_session_id=previous.row["id"] if previous else None,
        )
        self.last_part_at[st.code] = job.part
        self.session_part[row["id"]] = job.part

        if job.imported:
            # Operations done before go-live, backfilled from the plant's register
            # so the lot's flow starts at Op10 rather than appearing mid-routing.
            job.imported = False
            done_at = job.row["created_at"]
            for op in job.part.ops[: job.op_index]:
                self.s.add(
                    "stage_completions",
                    at=done_at,
                    plant_id=self.pid,
                    lot_id=lot["id"],
                    routing_operation_id=op.id,
                    station_id=None,
                    business_date=done_at.date(),
                    quantity_completed=job.target,
                    quantity_rejected=0,
                    started_at=None,
                    completed_at=done_at,
                    source="import",
                )
            job.start_index = 0
            lot["quantity"] += job.target
            job.ledger = [[lot, job.target]]

        rng = self.rng
        session = Session(row=row, job=job, op=job.op, start=start, lot=lot)
        session.inspect = rng.random() <= (0.5 if self.spec.code == "SAKTHI" else 0.68)
        roll = rng.random()
        session.disposition = (
            "scrap" if roll < 0.8 else "rework" if roll < 0.92 else "use_as_is"
        )
        session.quality_hold = any(
            c.station == st.code and c.reason == "QUALITY_HOLD" for c in self.spec.chronic
        )
        return session

    @staticmethod
    def _consume(job: Job, session: Session, made: int, scrap: int) -> None:
        """Book one slot's output against the lots the parts belong to."""
        if session.op.index == job.start_index:
            # First stage: parts enter the routing, into this session's lot.
            session.lot["quantity"] += made
            takes = [(session.lot, made)]
        else:
            takes, remaining = [], made
            for entry in job.ledger:
                take = min(entry[1], remaining)
                if take:
                    entry[1] -= take
                    remaining -= take
                    takes.append((entry[0], take))
                if not remaining:
                    break
            assert remaining == 0, (job.row["job_number"], made, job.ledger)
        for lot, take in reversed(takes):
            scrapped = min(take, scrap)
            scrap -= scrapped
            booked = session.alloc.setdefault(lot["id"], [lot, 0, 0])
            booked[1] += take
            booked[2] += scrapped
            out = job.output.setdefault(lot["id"], [lot, 0])
            out[1] += take - scrapped

    @staticmethod
    def _end_session(session: Session, when: datetime) -> None:
        session.end = when
        session.row["ended_at"] = when
        session.row["updated_at"] = when

    def _session_at(self, sessions: list[Session], when: datetime) -> Session | None:
        for session in sessions:
            if session.start <= when and (session.end is None or when < session.end):
                return session
        return sessions[-1] if sessions and when >= sessions[-1].start else None

    def _record_events(self, ctx, st, events: list[dict], sessions: list[Session]) -> None:
        rng = self.rng
        for proto in sorted(events, key=lambda e: e["start"]):
            session = self._session_at(sessions, proto["start"])
            part = session.job.part if session else None
            op = session.op if session else None
            snapshot = self._snapshot(st, part, op, ctx.day)
            reason = self.reasons.get(proto["reason"]) if proto["reason"] else None
            logged = proto["start"] + timedelta(seconds=rng.randint(10, 150))
            row = self.s.add(
                "downtime_events",
                at=logged,
                plant_id=self.pid,
                station_id=st.id,
                job_session_id=session.row["id"] if session else None,
                shift_session_id=ctx.row["id"],
                business_date=ctx.day,
                reason_code_id=reason.id if reason else None,
                started_at=proto["start"],
                ended_at=proto["end"],
                duration_minutes=None,
                status="open",
                note=rng.choice(EVENT_NOTES[proto["reason"]])
                if proto["reason"] in EVENT_NOTES and rng.random() < 0.18
                else None,
                source=proto["source"],
                source_ref="changeover-gap" if proto["source"] == "system" else None,
                logged_by_id=None
                if proto["source"] == "system"
                else ctx.supervisor[1]["id"],
                escalation_level=0,
            )
            self._apply_snapshot(row, snapshot)
            if proto["end"] is not None:
                seconds = int((proto["end"] - proto["start"]).total_seconds())
                minutes = Decimal(seconds) / Decimal(60)
                cost = downtime_event_cost(minutes, snapshot)
                row.update(
                    status="closed",
                    duration_minutes=q4(minutes),
                    cost_total=cost.total,
                    updated_at=proto["end"],
                    cost_breakdown={
                        "machine": cost.machine_cost,
                        "labour": cost.labour_cost,
                        "overhead": cost.overhead_cost,
                        "lost_contribution": cost.lost_contribution,
                        "parts_not_made": cost.parts_not_made,
                    },
                )
            event = Ev(
                row=row,
                station=st,
                reason=reason,
                bd=ctx.day,
                start=proto["start"],
                end=proto["end"],
            )
            row["escalation_level"] = self._escalations(event, snapshot, session)

            if (
                reason
                and proto["end"] is not None
                and proto["source"] == "manual"
                and rng.random() < 0.02
            ):
                wrong = rng.choice(
                    [
                        r
                        for r in self.reasons.values()
                        if r.route == reason.route
                        and r.code != reason.code
                        and r.row["is_active"]
                    ]
                    or [reason]
                )
                edited = proto["end"] + timedelta(minutes=rng.randint(3, 40))
                if wrong is not reason and edited < ctx.end:
                    self._audit(
                        edited,
                        ctx.supervisor[1],
                        "downtime_event",
                        row["id"],
                        "update",
                        {"reason_code": wrong.code},
                        {"reason_code": reason.code},
                        "Reason corrected after maintenance feedback",
                    )
                    row["updated_at"] = edited

            if proto["end"] is not None:
                self.closed_event_count += 1
                if reason is not None:
                    self.events_by_date[ctx.day].append(event)
                    self.pair_events[(st.code, reason.code)].append(event)

    def _escalations(
        self, event: Ev, snapshot: CostSnapshot, session: Session | None
    ) -> int:
        """The 5/15/30 ladder, exactly as ``schedule_escalations`` + the worker leave it."""
        rng = self.rng
        route = event.reason.route if event.reason else "production"
        if route == "none":
            return 0
        burden = (
            snapshot.rates.machine_rate
            + snapshot.rates.labour_rate
            + snapshot.rates.overhead_rate
        )
        duration = (event.end - event.start).total_seconds() if event.end else None
        fired = 0
        for level, delay in ESCALATION_LADDER:
            lvl = int(level)
            due = event.start + delay
            recipient = (
                None
                if lvl == 1
                else (
                    self.recipients["plant_head"][0]
                    if lvl >= 3
                    else (self.recipients.get(route) or self.recipients["production"])[0]
                )
            )
            channel = (
                "push"
                if lvl == 1
                else ("whatsapp" if self.spec.whatsapp_approved else "sms")
            )
            base = {
                "plant_id": self.pid,
                "downtime_event_id": event.row["id"],
                "level": lvl,
                "due_at": due,
                "recipient_name": recipient["name"] if recipient else None,
                "recipient_number": recipient["whatsapp_number"] if recipient else None,
            }
            reached = (duration is not None and duration > delay.total_seconds()) or (
                duration is None and due <= self.now
            )
            if reached:
                elapsed = Decimal(delay.total_seconds()) / Decimal(60)
                cost = int(elapsed * Decimal(burden) / Decimal(60))
                message = escalation_message(
                    station_code=event.station.code,
                    reason_label=event.reason.label
                    if event.reason
                    else "an unlogged reason",
                    job_number=session.job.row["job_number"] if session else None,
                    minutes=int(elapsed),
                    cost=cost,
                )
                sent = due + timedelta(seconds=rng.randint(2, 40))
                failed = channel == "whatsapp" and rng.random() < 0.03
                ack = sent + timedelta(minutes=rng.randint(1, 9))
                if lvl == 1 or failed or rng.random() < 0.1 or ack > self.now:
                    ack = None
                response = (
                    {"delivered_devices": rng.randint(1, 2)}
                    if channel == "push"
                    else {
                        "result": not failed,
                        "info": "Template message queued"
                        if not failed
                        else "Session expired: template not approved for this number",
                        "id": self.s.uuid().hex[:24],
                    }
                    if channel == "whatsapp"
                    else {
                        "provider": "sms_fallback",
                        "status": "DELIVERED",
                        "request_id": self.s.uuid().hex[:16],
                        "reason": "WATI approval pending",
                    }
                )
                self.s.add(
                    "escalations",
                    at=due,
                    status="failed" if failed else "sent",
                    sent_at=None if failed else sent,
                    channel=channel,
                    message=message,
                    cost_at_send=cost,
                    provider_response=response,
                    acknowledged_at=ack,
                    **base,
                )
                if failed:
                    self.s.add(
                        "escalations",
                        at=sent + timedelta(seconds=30),
                        status="sent",
                        sent_at=sent + timedelta(seconds=35),
                        channel="sms",
                        message=message,
                        cost_at_send=cost,
                        provider_response={
                            "provider": "sms_fallback",
                            "status": "DELIVERED",
                            "request_id": self.s.uuid().hex[:16],
                            "reason": "WhatsApp send failed",
                        },
                        acknowledged_at=None,
                        **base,
                    )
                fired = lvl
            elif event.end is not None:
                row = self.s.add(
                    "escalations",
                    at=event.start,
                    status="cancelled",
                    sent_at=None,
                    channel=channel,
                    message=None,
                    cost_at_send=0,
                    provider_response=None,
                    acknowledged_at=None,
                    **base,
                )
                row["updated_at"] = event.end
            else:
                self.s.add(
                    "escalations",
                    at=event.start,
                    status="pending",
                    sent_at=None,
                    channel=channel,
                    message=None,
                    cost_at_send=0,
                    provider_response=None,
                    acknowledged_at=None,
                    **base,
                )
        return fired

    def _close_out_session(self, ctx, st, session: Session) -> None:
        """Stage completion and the inspector's independent quality entry."""
        rng = self.rng
        if session.end is None or session.true_count <= 0:
            return
        for lot, consumed, scrapped in session.alloc.values():
            self.s.add(
                "stage_completions",
                at=session.end,
                plant_id=self.pid,
                lot_id=lot["id"],
                routing_operation_id=session.op.id,
                station_id=st.id,
                business_date=ctx.day,
                # lot_flow semantics: completed = passed onward, rejected = scrapped here.
                quantity_completed=consumed - scrapped,
                quantity_rejected=scrapped,
                started_at=session.start,
                completed_at=session.end,
                source="manual",
            )
        if not session.inspect:
            return
        recorded = session.end + timedelta(minutes=rng.randint(8, 45))
        if recorded > self.now:
            return
        inspected = max(0, session.true_count + rng.randint(-2, 2))
        found = min(inspected, session.found)
        disposition = session.disposition if found else "scrap"
        reject = found if disposition in ("scrap", "use_as_is") else 0
        rework = found if disposition == "rework" else 0
        good = inspected - reject
        family = session.job.part.spec.family
        codes = self.defects_by_family.get(family, []) + self.defects_by_family.get(
            None, []
        )
        defects = []
        if found and codes:
            picks = rng.sample(codes, k=min(len(codes), rng.randint(1, 3)))
            split = [found // len(picks)] * len(picks)
            split[0] += found - sum(split)
            defects = [
                {"code": c, "count": n} for c, n in zip(picks, split, strict=True) if n
            ]
        snapshot = self._snapshot(st, session.job.part, session.op, ctx.day)
        cost = scrap_cost(
            quantity=reject if disposition == "scrap" else 0,
            snapshot=snapshot,
            completed_operations=self._completed_ops(session.job.part, st, ctx.day),
        )
        flagged, gap, _ = count_reconciliation(
            supervisor_count=session.reported,
            inspector_good=good,
            inspector_rejected=reject,
            tolerance_parts=self.spec.tolerance_parts,
            tolerance_fraction=Decimal(self.spec.tolerance_bp) / Decimal(10_000),
        )
        row = self.s.add(
            "quality_records",
            at=recorded,
            plant_id=self.pid,
            station_id=st.id,
            job_session_id=session.row["id"],
            shift_session_id=ctx.row["id"],
            lot_id=session.lot["id"],
            part_id=session.job.part.id,
            business_date=ctx.day,
            good_count=good,
            reject_count=reject,
            rework_count=rework,
            rework_minutes=q4(
                Decimal(rework)
                * session.op.ct
                * Decimal(str(round(rng.uniform(0.6, 1.5), 2)))
            ).quantize(Decimal("0.01"))
            if rework
            else None,
            disposition=disposition,
            defects=defects,
            recorded_at=recorded,
            source="manual",
            inspector_id=ctx.inspector[1]["id"],
            reconciliation_gap=gap,
            reconciliation_flagged=flagged,
        )
        self._apply_snapshot(row, snapshot)
        row["cost_total"] = cost.total
        row["cost_breakdown"] = {
            "material": cost.material_cost,
            "processing": cost.processing_cost,
            "lost_contribution": cost.lost_contribution,
            "quantity": cost.quantity,
        }
        self.quality_rows.append(row)

    def _summary(self, ctx, actions_created: int) -> dict:
        """The frozen KPI snapshot written at close.

        The top-level figures are exactly what ``services.reports.shift_kpis``
        returns for this shift, so the snapshot and the live KPI screen agree.
        ``per_station`` is each station's own OEE from the same aggregation.
        """
        shift_id = ctx.row["id"]
        events = [
            e
            for e in self.events_by_date.get(ctx.day, [])
            if e.row["shift_session_id"] == shift_id
        ]
        slices = [
            DowntimeSlice(
                minutes=Decimal(e.row["duration_minutes"] or 0),
                recoverability=Recoverability(e.reason.rs),
                oee_treatment=OeeTreatment(e.reason.oee),
                is_breakdown=e.reason.breakdown,
                cost=e.row["cost_total"],
                reason_code=e.reason.label,
                station_id=e.station.code,
            )
            for e in events
        ]
        records = [q for q in self.quality_rows if q["shift_session_id"] == shift_id]
        good = sum(q["good_count"] for q in records)
        rejected = sum(q["reject_count"] for q in records)
        reworked = sum(q["rework_count"] for q in records)
        total = sum(s.reported for _, sessions, _, _ in ctx.events for s in sessions)
        # ``reports.station_runs`` + ``kernel.aggregate_oee``: one run per station
        # that ran, each against its own planned time, cycle time weighted by
        # what was counted on each routing step, good = produced − rejects.
        rejects_by_station = defaultdict(int)
        for record in records:
            rejects_by_station[record["station_id"]] += record["reject_count"]
        runs = []
        for st, sessions, _events, _perf in sorted(
            (item for item in ctx.events if item[1]), key=lambda item: str(item[0].id)
        ):
            station_total = sum(sess.reported for sess in sessions)
            weighted = sum(
                (Decimal(sess.reported) * sess.op.ct for sess in sessions), Decimal(0)
            )
            runs.append(
                StationRun(
                    station=st.code,
                    shift_minutes=Decimal(ctx.sh.planned),
                    downtime=[sl for sl in slices if sl.station_id == st.code],
                    cycle_time_min=weighted / Decimal(station_total)
                    if station_total
                    else Decimal(0),
                    total_count=station_total,
                    good_count=max(0, station_total - rejects_by_station.get(st.id, 0)),
                )
            )
        oee, station_results = aggregate_oee(runs)
        targets = {}
        for row in self.target_rows:
            if row["effective_from"] <= ctx.day and row["station_id"] is not None:
                targets[row["station_id"]] = row["target_parts"]
        target_total = sum(targets.values())
        breakdowns = count_breakdowns(slices)
        per_station = [
            {
                "station": run.station,
                "oee": ratio(result.oee),
                "availability": ratio(result.availability),
                "performance": ratio(result.performance),
                "quality": ratio(result.quality),
                "total_count": run.total_count,
                "good_count": run.good_count,
                "flags": list(result.flags),
            }
            for run, result in zip(runs, station_results, strict=True)
        ]

        return {
            "shift_session_id": str(shift_id),
            "business_date": ctx.day.isoformat(),
            "shift_name": ctx.sh.name,
            "oee": float(oee.oee),
            "availability": float(oee.availability),
            "performance": float(oee.performance),
            "quality": float(oee.quality),
            "planned_minutes": float(oee.planned_minutes),
            "downtime_minutes": float(
                oee.unplanned_downtime_minutes + oee.planned_downtime_minutes
            ),
            "recoverable_downtime_minutes": float(
                sum(
                    (
                        s.minutes
                        for s in slices
                        if s.recoverability is Recoverability.RECOVERABLE
                    ),
                    Decimal(0),
                )
            ),
            "total_count": total,
            "good_count": good,
            "reject_count": rejected,
            "target": target_total,
            "schedule_adherence": float(
                schedule_adherence(actual_output=total, planned_output=target_total)
            ),
            "ppm": float(ppm(rejected_parts=rejected, total_inspected=good + rejected)),
            "first_pass_yield": float(
                first_pass_yield(
                    good_first_time=max(0, good - reworked), parts_started=good + rejected
                )
            ),
            "mtbf_minutes": float(
                mtbf(total_run_minutes=oee.run_minutes, breakdown_count=breakdowns)
            ),
            "mttr_minutes": float(
                mttr(
                    total_breakdown_minutes=breakdown_minutes(slices),
                    breakdown_count=breakdowns,
                )
            ),
            "breakdown_count": breakdowns,
            "recoverable_loss": sum(
                s.cost for s in slices if s.recoverability is Recoverability.RECOVERABLE
            ),
            "structural_loss": sum(
                s.cost for s in slices if s.recoverability is Recoverability.STRUCTURAL
            ),
            "flags": list(oee.flags),
            "per_station": per_station,
            "actions_created": actions_created,
            "formula_version": FORMULA_VERSION,
        }

    # ======================================================================
    # Actions & PDCA (§5)
    # ======================================================================

    def _evaluate_actions(
        self, day: date, shift_row: dict, closed_at: datetime
    ) -> list[Plan]:
        """``services.actions.evaluate_shift_close`` over in-memory rows."""
        window_start = day - timedelta(days=self.limits.recurrence_window_shifts)
        candidates, by_id = [], {}
        for offset in range((day - window_start).days + 1):
            for event in self.events_by_date.get(window_start + timedelta(days=offset), []):
                event_id = str(event.row["id"])
                by_id[event_id] = event
                candidates.append(
                    EventForTrigger(
                        event_id=event_id,
                        station_id=event.station.code,
                        reason_code=event.reason.label,
                        minutes=Decimal(event.row["duration_minutes"] or 0),
                        cost=event.row["cost_total"],
                        recoverability=Recoverability(event.reason.rs),
                        shift_index=(event.bd - window_start).days,
                        safety_or_quality_escape=event.reason.safety,
                    )
                )
        created = []
        for candidate in evaluate_action_triggers(candidates, self.limits):
            if any(
                plan.action["state"] not in ("verified", "cancelled")
                for eid in candidate.event_ids
                for plan in self.event_actions.get(eid, [])
            ):
                continue
            first = by_id[candidate.event_ids[0]]
            verdict = route_by_severity(
                next(c for c in candidates if c.event_id == candidate.event_ids[0]),
                self.limits,
            )
            plan = self._create_action(
                when=closed_at,
                day=day,
                title=candidate.title,
                description=candidate.rationale,
                trigger=candidate.trigger.value,
                rationale=candidate.rationale,
                station=first.station,
                reason=first.reason,
                linked_cost=candidate.total_cost,
                occurrences=candidate.occurrence_count,
                rca=verdict.escalate_to_rca,
                severity=list(verdict.reasons),
            )
            for event_id in candidate.event_ids:
                self.s.add(
                    "action_events",
                    at=closed_at,
                    plant_id=self.pid,
                    action_id=plan.action["id"],
                    downtime_event_id=by_id[event_id].row["id"],
                )
                self.event_actions[event_id].append(plan)
            if plan.rca is not None:
                plan.rca["prefilled_context"] = {
                    "occurrences": candidate.occurrence_count,
                    "total_cost_paise": candidate.total_cost,
                    "reason": candidate.reason_code,
                    "station": candidate.station_id,
                    "event_dates": sorted(
                        {by_id[e].bd.isoformat() for e in candidate.event_ids}
                    ),
                }
            part = self.session_part.get(
                by_id[candidate.event_ids[-1]].row["job_session_id"]
            )
            plan.part_id = part.id if part else None
            created.append(plan)
        return created

    def _weekly_loss(self, station, reason, start: date, end: date) -> int:
        if station is None or reason is None:
            return 0
        return sum(
            e.row["cost_total"]
            for e in self.pair_events.get((station.code, reason.code), [])
            if start <= e.bd < end
        )

    def _create_action(
        self,
        *,
        when,
        day,
        title,
        description,
        trigger,
        rationale,
        station,
        reason,
        linked_cost,
        occurrences=1,
        rca=False,
        severity=None,
        owner=None,
        due=None,
    ) -> Plan:
        self.action_seq += 1
        weeks = [
            WeeklyLoss(
                day - timedelta(weeks=k),
                self._weekly_loss(
                    station,
                    reason,
                    day - timedelta(weeks=k),
                    day - timedelta(weeks=k) + timedelta(days=7),
                ),
            )
            for k in range(4, 0, -1)
        ]
        row = self.s.add(
            "actions",
            at=when,
            plant_id=self.pid,
            reference=f"ACT-{self.action_seq:04d}",
            title=title,
            description=description,
            trigger=trigger,
            trigger_rationale=rationale,
            state="open",
            station_id=station.id if station else None,
            reason_code_id=reason.id if reason else None,
            linked_cost=linked_cost,
            occurrence_count=occurrences,
            routed_to_rca=rca,
            severity_reasons=severity or [],
            owner_id=owner[1]["id"] if owner and owner[1] else None,
            owner_name=owner[0] if owner else None,
            due_date=due,
            root_cause=None,
            countermeasure=None,
            evidence_photo_url=None,
            closed_at=None,
            verified_at=None,
            reopened_at=None,
            reopen_count=0,
            baseline_weekly_loss=freeze_baseline(weeks) if reason else 0,
            baseline_frozen_at=when,
            recovered_profit=0,
            sustained_weeks=0,
            surfaced_in_meeting_on=day,
            meeting_status=None,
            delay_reason=None,
        )
        rca_row = None
        if rca:
            rca_row = self.s.add(
                "root_cause_analyses",
                at=when,
                plant_id=self.pid,
                action_id=row["id"],
                assigned_to_id=None,
                assigned_team=None,
                due_date=day + timedelta(days=3),
                five_whys=[{"why": "", "because": ""} for _ in range(5)],
                prefilled_context={},
                ai_suggested_causes=None,
                ai_suggestion_accepted=None,
                conclusion=None,
                completed_at=None,
            )
        plan = Plan(action=row, reason=reason, station=station, rca=rca_row, owner=owner)
        self._schedule(plan, day)
        self.actions.append(plan)
        return plan

    def _schedule(self, plan: Plan, day: date, *, reclose: bool = False) -> None:
        rng = self.rng
        meeting = next_working_day(day)
        if not reclose:
            plan.steps.append((meeting, "assign"))
            if plan.reason is not None and plan.reason.rs == "S" and rng.random() < 0.75:
                plan.steps.append((meeting, "cancel"))
                return
            if (
                plan.station is not None
                and plan.reason is not None
                and rng.random() < 0.3
                and any(
                    p is not plan
                    and p.station is plan.station
                    and p.reason is plan.reason
                    and p.action["state"] in ACTIVE_STATES
                    for p in self.actions
                )
            ):
                plan.steps.append((meeting, "cancel_duplicate"))
                return
        stall = rng.random() < 0.12
        delay = rng.random() < 0.2
        clock = meeting
        sequence = (["root_cause"] if plan.rca is not None and not reclose else []) + [
            "planned",
            "in_progress",
        ]
        if stall:
            sequence = sequence[: rng.randint(0, len(sequence) - 1)]
        for state in sequence:
            clock = clock + timedelta(days=rng.randint(0, 2))
            plan.steps.append((clock, state))
        if delay or stall:
            plan.steps.append((meeting + timedelta(days=rng.randint(2, 5)), "delay"))
        if not stall:
            clock = clock + timedelta(
                days=rng.randint(2, 6) + (rng.randint(3, 7) if delay else 0)
            )
            plan.steps.append((clock, "close"))

    def _advance_actions(self, day: date) -> None:
        rng = self.rng
        for plan in self.actions:
            row = plan.action
            for index, (when, step) in enumerate(plan.steps):
                if index in plan.done or when > day:
                    continue
                moment = at(day, f"{rng.randint(9, 17):02d}:{rng.randint(0, 59):02d}")
                if moment > self.now:
                    continue
                plan.done.add(index)
                if row["state"] in ("cancelled",):
                    continue
                if step == "assign":
                    if plan.owner is None:
                        plan.owner = self._person(
                            plan.reason.route if plan.reason else "production"
                        )
                    row.update(
                        owner_name=plan.owner[0],
                        owner_id=plan.owner[1]["id"] if plan.owner[1] else None,
                        due_date=row["due_date"] or day + timedelta(days=rng.randint(3, 8)),
                        updated_at=moment,
                    )
                elif step in ("cancel", "cancel_duplicate"):
                    note = texts.CANCEL_REASONS["S" if step == "cancel" else "dup"]
                    self._audit(
                        moment,
                        self.owner_user,
                        "action",
                        row["id"],
                        "update",
                        {"state": row["state"]},
                        {"state": "cancelled"},
                        note,
                    )
                    row.update(
                        state="cancelled",
                        description=f"{row['description'] or ''}\n\nCancelled: {note}".strip(),
                        meeting_status="done",
                        updated_at=moment,
                    )
                elif step == "delay":
                    if row["state"] in ACTIVE_STATES or row["state"] == "reopened":
                        row.update(
                            meeting_status="delayed",
                            delay_reason=rng.choice(texts.DELAY_REASONS),
                            updated_at=moment,
                        )
                elif step == "close":
                    if row["state"] in ACTIVE_STATES or row["state"] == "reopened":
                        self._close_action(plan, day, moment)
                else:
                    if row["state"] in ACTIVE_STATES:
                        row.update(state=step, updated_at=moment)
                        if step == "root_cause" and plan.rca is not None:
                            plan.rca.update(
                                assigned_to_id=row["owner_id"],
                                assigned_team=(
                                    plan.reason.route if plan.reason else "production"
                                ).title(),
                                updated_at=moment,
                            )

    def _close_action(self, plan: Plan, day: date, moment: datetime) -> None:
        rng, row = self.rng, plan.action
        station_code = plan.station.code if plan.station else "the plant"
        variants = texts.FIXES.get(plan.reason.code) if plan.reason else None
        root, fix, replaced, check, whys = (
            rng.choice(variants) if variants else texts.GENERIC_FIX
        )
        fmt = lambda text: text.format(station=station_code) if text else text  # noqa: E731
        owner = plan.owner or self._person("production")
        photo = (
            f"evidence/{self.spec.code}/{row['reference']}/after-{plan.closures + 1}.jpg"
            if rng.random() < 0.7
            else None
        )
        if row["state"] == "reopened":
            fix = f"{fmt(fix)} (second attempt: previous fix did not hold)"
        row.update(
            root_cause=fmt(root),
            countermeasure=fmt(fix),
            evidence_photo_url=photo,
            closed_at=moment,
            state="monitoring",
            meeting_status="done",
            updated_at=moment,
            owner_name=owner[0],
            owner_id=owner[1]["id"] if owner[1] else None,
        )
        if row["due_date"] is None:
            row["due_date"] = day
        previous = [
            m for m in self.memory if plan.reason and m["reason_code_id"] == plan.reason.id
        ]
        self.memory.append(
            self.s.add(
                "memory_entries",
                at=moment,
                plant_id=self.pid,
                action_id=row["id"],
                station_id=row["station_id"],
                part_id=plan.part_id,
                reason_code_id=row["reason_code_id"],
                what_fixed_it=fmt(fix),
                what_was_replaced=replaced,
                photo_url=photo,
                check_first_next_time=fmt(check),
                problem_summary=row["title"],
                tags=[
                    t
                    for t in [
                        plan.reason.code.lower() if plan.reason else row["trigger"],
                        plan.station.family.lower() if plan.station else None,
                        row["trigger"],
                    ]
                    if t
                ],
                fixed_by_name=owner[0],
                occurred_on=day,
                embedding_status="done" if (self.as_of - day).days > 1 else "pending",
            )
        )
        if plan.rca is not None and plan.rca["completed_at"] is None:
            suggestions = [
                {
                    "cause": m["problem_summary"],
                    "evidence": f"Fixed on {m['occurred_on']}: {m['what_fixed_it']}",
                    "source": "factory_memory",
                    "memory_entry_id": str(m["id"]),
                }
                for m in previous[-3:]
            ]
            completed = max(
                plan.rca["created_at"] + timedelta(hours=3),
                moment - timedelta(days=rng.randint(0, 2)),
            )
            plan.rca.update(
                five_whys=[{"why": fmt(w), "because": fmt(b)} for w, b in whys],
                conclusion=fmt(root),
                completed_at=completed,
                ai_suggested_causes=suggestions or None,
                ai_suggestion_accepted=(rng.random() < 0.6) if suggestions else None,
                assigned_to_id=plan.rca["assigned_to_id"] or row["owner_id"],
                updated_at=completed,
            )
        if plan.station and plan.reason:
            holds = rng.random() < (0.85 if plan.closures == 0 else 0.75)
            self.fix_effect[(plan.station.code, plan.reason.code)] = (
                day,
                0.06 if holds else 0.9,
            )
        plan.closures += 1
        plan.last_week_count = -1
        if plan.kaizen is not None:
            plan.kaizen.update(status="implemented", updated_at=moment)
        if plan.need is not None:
            plan.need.update(resolved=True, updated_at=moment)
            trainee = next(
                (
                    o
                    for o in self.operators
                    if o.active
                    and self.skills.get((o.id, plan.station.code), {}).get("level", 0)
                    < COMPETENT
                ),
                None,
            )
            if trainee:
                key = (trainee.id, plan.station.code)
                if key in self.skills:
                    self.skills[key].update(
                        level=2,
                        certified_on=day,
                        expires_on=day + timedelta(days=730),
                        note="Certified via skill-gap action",
                        updated_at=moment,
                    )
                else:
                    self.skills[key] = self.s.add(
                        "operator_skills",
                        at=moment,
                        plant_id=self.pid,
                        operator_id=trainee.id,
                        station_id=plan.station.id,
                        level=2,
                        certified_on=day,
                        expires_on=day + timedelta(days=730),
                        note="Certified via skill-gap action",
                    )
                self._refresh_certified(moment)

    def _worker(self, day: date) -> None:
        """The hourly worker: reopen failed fixes first, then accrue (§5.6)."""
        rng = self.rng
        as_of = min(day + timedelta(days=1), self.as_of)
        run_at = min(at(day, "23:05"), self.now)
        for plan in self.actions:
            row = plan.action
            if row["state"] not in ("monitoring", "verified") or row["closed_at"] is None:
                continue
            if plan.station and plan.reason:
                after = sorted(
                    (
                        e
                        for e in self.pair_events.get(
                            (plan.station.code, plan.reason.code), []
                        )
                        if e.start > row["closed_at"]
                    ),
                    key=lambda e: e.start,
                )
                if len(after) >= self.spec.recurrence_count:
                    reopened = min(
                        self.now,
                        after[self.spec.recurrence_count - 1].end
                        + timedelta(minutes=rng.randint(2, 58)),
                    )
                    row.update(
                        state="reopened",
                        reopened_at=reopened,
                        reopen_count=row["reopen_count"] + 1,
                        recovered_profit=0,
                        meeting_status=None,
                        updated_at=reopened,
                    )
                    self._audit(
                        reopened,
                        None,
                        "action",
                        row["id"],
                        "update",
                        {"state": "monitoring"},
                        {"state": "reopened"},
                        f"{len(after)} recurrences of {plan.reason.label} on {plan.station.code} since closure — the fix did not hold.",
                    )
                    if rng.random() < 0.55:
                        plan.done.update(
                            range(len(plan.steps))
                        )  # abandon the old plan's remaining steps
                        self._schedule(plan, day, reclose=True)
                    continue
            closed_on = row["closed_at"].date()
            weeks = []
            start = closed_on
            while start + timedelta(days=7) <= as_of:
                weeks.append(
                    WeeklyLoss(
                        start,
                        self._weekly_loss(
                            plan.station, plan.reason, start, start + timedelta(days=7)
                        ),
                    )
                )
                start += timedelta(days=7)
            if len(weeks) == plan.last_week_count:
                continue
            plan.last_week_count = len(weeks)
            result = recovered_profit(
                baseline_weekly_loss=row["baseline_weekly_loss"],
                weeks_after_closure=weeks,
                reopened=False,
            )
            for (week, saving), observed in zip(
                result.weekly_breakdown, weeks, strict=True
            ):
                key = (row["id"], week)
                entry = self.rp_rows.get(key)
                if entry is None:
                    entry = self.rp_rows[key] = self.s.add(
                        "recovered_profit_entries",
                        at=run_at,
                        plant_id=self.pid,
                        action_id=row["id"],
                        week_start=week,
                    )
                entry.update(
                    baseline_weekly_loss=row["baseline_weekly_loss"],
                    actual_weekly_loss=observed.loss,
                    saving=saving,
                    counted=result.sustained and saving > 0,
                    updated_at=run_at,
                    note=(
                        "Held below baseline."
                        if saving > 0
                        else "At or above baseline — nothing credited this week."
                    )
                    if result.sustained
                    else result.reason,
                )
            row.update(
                recovered_profit=result.recovered,
                sustained_weeks=result.weeks_counted,
                updated_at=run_at,
            )
            if result.sustained and row["state"] == "monitoring":
                row.update(state="verified", verified_at=run_at)

    def _manual_actions(self, day: date) -> None:
        rng = self.rng
        if rng.random() > 1 / 11 or day == self.spec.go_live:
            return
        st = rng.choice(self.producing)
        title = rng.choice(
            [
                f"Fix the compressed-air leak near {st.code}",
                f"Install a coil-end / bar-end sensor on {st.code}",
                f"Repaint aisle markings on {st.spec.line or 'the shop floor'}",
                "Calibrate all plug gauges before the customer audit",
                f"Add a spare-parts kit for {st.code} to the maintenance store",
                "Move the dispatch staging area away from the forklift route",
            ]
        )
        when = at(day, f"{rng.randint(8, 11):02d}:{rng.randint(0, 59):02d}")
        if when > self.now:
            return
        self._create_action(
            when=when,
            day=day,
            title=title,
            description="Raised by the owner in the morning meeting.",
            trigger="manual",
            rationale="Owner-raised in the morning meeting.",
            station=st,
            reason=None,
            linked_cost=0,
            owner=(self.head_person.name, self.head_user),
            due=day + timedelta(days=rng.randint(5, 14)),
        )

    # ======================================================================
    # Coach (§14.6) and Copilot (Layer 3)
    # ======================================================================

    def _sufficiency(self, moment: datetime, day: date) -> DataSufficiency:
        return DataSufficiency(
            complete_shifts=sum(1 for t in self.closed_shift_times if t <= moment),
            days_live=(day - self.first_bd).days if self.first_bd else 0,
            distinct_events=self.closed_event_count,
        )

    def _ranked(self, start: date, end: date) -> SimpleNamespace:
        groups: dict[tuple, list] = defaultdict(lambda: [Decimal(0), 0, 0])
        for offset in range((end - start).days + 1):
            for event in self.events_by_date.get(start + timedelta(days=offset), []):
                bucket = groups[(event.station.code, event.reason.label, event.reason.rs)]
                bucket[0] += Decimal(event.row["duration_minutes"])
                bucket[1] += event.row["cost_total"]
                bucket[2] += 1
        ranked = rank_losses(
            LossLine(
                station=k[0],
                reason=k[1],
                minutes=v[0],
                cost=v[1],
                recoverability=Recoverability(k[2]),
                event_count=v[2],
            )
            for k, v in groups.items()
        )

        def line(item: LossLine) -> dict:
            return {
                "station": item.station,
                "reason": item.reason,
                "minutes": float(item.minutes),
                "cost": item.cost,
                "cost_display": format_inr(item.cost),
                "recoverability": item.recoverability.value,
                "event_count": item.event_count,
            }

        return SimpleNamespace(
            recoverable=[line(i) for i in ranked.recoverable],
            structural=[line(i) for i in ranked.structural],
            recoverable_total=ranked.recoverable_total,
            structural_total=ranked.structural_total,
            total_minutes=ranked.total_minutes,
        )

    def _patterns(
        self, as_of: date, days: int = 28, station_code: str | None = None
    ) -> list[dict]:
        groups: dict[tuple, list] = defaultdict(lambda: [0, 0, Decimal(0)])
        for offset in range(days + 1):
            for event in self.events_by_date.get(as_of - timedelta(days=offset), []):
                if event.reason.rs != "R" or (
                    station_code and event.station.code != station_code
                ):
                    continue
                bucket = groups[(event.station.code, event.reason.label, event.reason.code)]
                bucket[0] += 1
                bucket[1] += event.row["cost_total"]
                bucket[2] += Decimal(event.row["duration_minutes"])
        rows = sorted(
            ((k, v) for k, v in groups.items() if v[0] >= 3),
            key=lambda kv: kv[1][1],
            reverse=True,
        )[:5]
        return [
            {
                "station": k[0],
                "reason": k[1],
                "reason_code": k[2],
                "occurrences": v[0],
                "total_cost": format_inr(v[1]),
                "average_minutes": round(float(v[2] / v[0]), 1),
                "interval_days": round(days / max(1, v[0]), 1),
            }
            for k, v in rows
        ]

    def _overdue(self, as_of: date) -> list[dict]:
        rows = sorted(
            (
                p.action
                for p in self.actions
                if p.action["due_date"]
                and p.action["due_date"] < as_of
                and p.action["state"] in ACTIVE_STATES
            ),
            key=lambda a: a["linked_cost"],
            reverse=True,
        )[:5]
        return [
            {
                "reference": a["reference"],
                "title": a["title"],
                "owner": a["owner_name"],
                "due": a["due_date"].isoformat(),
                "cost": format_inr(a["linked_cost"]),
            }
            for a in rows
        ]

    def _coach(self, day: date) -> None:
        rng = self.rng
        delivered = at(day, "05:45")
        if delivered > self.now:
            return
        sufficiency = self._sufficiency(delivered, day)
        tier = coach_tier(sufficiency)
        losses = self._ranked(day - timedelta(days=1), day - timedelta(days=1))
        bundle = FactBundle(
            plant_name=self.spec.name,
            as_of=day,
            tier=int(tier),
            facts={
                "yesterday_recoverable_loss": format_inr(losses.recoverable_total),
                "yesterday_structural_loss": format_inr(losses.structural_total),
                "yesterday_downtime_minutes": float(losses.total_minutes),
                "shifts_of_history": sufficiency.complete_shifts,
                "days_live": sufficiency.days_live,
            },
            evidence=[
                {
                    "rank": i + 1,
                    "station": item["station"],
                    "reason": item["reason"],
                    "minutes": item["minutes"],
                    "cost": item["cost_display"],
                    "occurrences": item["event_count"],
                }
                for i, item in enumerate(losses.recoverable[:5])
            ],
        )
        if tier >= CoachTier.PATTERN:
            bundle.facts["recurring_patterns"] = self._patterns(day)
        else:
            bundle.limits.append(
                "Not enough history for pattern analysis (needs about 4 weeks). Do not claim a machine 'usually' or 'always' does anything."
            )
        overdue = self._overdue(day)
        if overdue:
            bundle.facts["overdue_actions"] = overdue

        priorities: list[Priority] = []
        if tier is not CoachTier.SILENT:
            recipients = {tag: rows[0] for tag, rows in self.recipients.items()}
            candidates = []
            for pattern in bundle.facts.get("recurring_patterns", []) or []:
                owner = recipients.get(_route_for(pattern["reason"]))
                candidates.append(
                    Priority(
                        rank=0,
                        station=pattern["station"],
                        headline=f"High {pattern['reason'].lower()} risk today.",
                        evidence=f"{pattern['station']} averages a {pattern['reason'].lower()} every {pattern['interval_days']} days — {pattern['occurrences']} in the last 4 weeks, {pattern['total_cost']} total.",
                        action=_action_for(
                            pattern.get("reason_code", ""), pattern["station"]
                        ),
                        assignee=owner["name"] if owner else "Production Head",
                        deadline="before shift start",
                        value_paise=_paise(pattern["total_cost"]),
                    )
                )
            for line in bundle.evidence[:3]:
                owner = recipients.get(_route_for(line.get("reason", "")))
                candidates.append(
                    Priority(
                        rank=0,
                        station=line["station"],
                        headline=f"{line['reason']} cost {line['cost']} yesterday.",
                        evidence=f"{line['occurrences']} event(s), {line['minutes']} minutes, {line['cost']} on {line['station']}.",
                        action=_action_for(line.get("reason", ""), line["station"]),
                        assignee=owner["name"] if owner else "Production Head",
                        deadline="before shift start",
                        value_paise=_paise(line["cost"]),
                    )
                )
            for item in bundle.facts.get("overdue_actions", []) or []:
                candidates.append(
                    Priority(
                        rank=0,
                        station=item["reference"],
                        headline=f"{item['title']} is overdue.",
                        evidence=f"Due {item['due']}, {item['cost']} attributed so far.",
                        action="Close it out or record why it slipped.",
                        assignee=item["owner"] or "Action owner",
                        deadline="today",
                        value_paise=_paise(item["cost"]),
                    )
                )
            candidates.sort(key=lambda p: p.value_paise, reverse=True)
            seen = set()
            for candidate in candidates:
                if candidate.station in seen:
                    continue
                seen.add(candidate.station)
                candidate.rank = len(priorities) + 1
                priorities.append(candidate)
                if len(priorities) == 3:
                    break

        if tier is CoachTier.SILENT:
            text, by = (
                "Not enough history yet to coach. Escalations are running; briefings begin once about ten complete shifts are recorded.",
                "rules",
            )
        elif not priorities:
            text, by = "Nothing specific to flag this morning. Have a good shift.", "rules"
        else:
            text, by = "\n\n".join(p.as_text() for p in priorities), "rules"
            if (
                self.spec.code == "KUMARAN"
                and day >= date(2026, 8, 10)
                and rng.random() < 0.35
            ):
                text, by = f"Good morning. Three things for today.\n\n{text}", "llm"

        recipients = [
            (
                sh.id,
                (
                    self.supervisor_for.get(sh.name)
                    or next(iter(self.supervisor_for.values()))
                )[1],
            )
            for sh in self.shifts
        ] + [(None, self.owner_user)]
        for shift_id, user in recipients:
            opened = (
                delivered + timedelta(minutes=rng.randint(3, 140))
                if rng.random() < 0.72
                else None
            )
            self.s.add(
                "coach_briefings",
                at=delivered,
                plant_id=self.pid,
                business_date=day,
                shift_id=shift_id,
                recipient_id=user["id"],
                tier=int(tier),
                data_basis={
                    "shifts_of_history": sufficiency.complete_shifts,
                    "days_live": sufficiency.days_live,
                },
                priorities=[asdict(p) for p in priorities],
                fact_bundle={
                    "facts": bundle.facts,
                    "evidence": bundle.evidence,
                    "limits": bundle.limits,
                },
                rendered_text=text,
                generated_by=by,
                delivered_at=delivered + timedelta(seconds=rng.randint(1, 50)),
                opened_at=opened if opened and opened <= self.now else None,
            )

    @staticmethod
    def _classify(text: str) -> str:
        """``ai.facts.build_question_facts`` intent rules, in the same order."""
        if any(word in text for word in ("week", "month", "so far", "total")):
            return "period_loss"
        if any(
            p in text
            for p in (
                "ever",
                "before",
                "seen",
                "last time",
                "previously",
                "how do we fix",
                "what fixed",
            )
        ):
            return "memory"
        if any(
            word in text for word in ("defect", "reject", "quality", "ppm", "burr", "scrap")
        ):
            return "quality"
        if any(word in text for word in ("why", "keep", "always", "usually", "pattern")):
            return "pattern"
        return "today"

    def _copilot(self, day: date) -> None:
        rng = self.rng
        count = rng.randint(0, 3) if self.spec.code == "SAKTHI" else rng.randint(1, 6)
        askers = [self.owner_user, self.owner_user, self.head_user] + [
            u for _, u in self.supervisor_for.values()
        ]
        for _ in range(count):
            asked = at(day, f"{rng.randint(8, 21):02d}:{rng.randint(0, 59):02d}")
            if asked > self.now:
                continue
            desired = rng.choices(
                list(texts.COPILOT_QUESTIONS), weights=[30, 25, 15, 15, 15]
            )[0]
            chronic = rng.choice(self.spec.chronic)
            st = (
                self.stations[chronic.station]
                if rng.random() < 0.7
                else rng.choice(self.producing)
            )
            reason = (
                self.reasons[chronic.reason]
                if rng.random() < 0.7
                else self.reasons["MATERIAL_WAIT"]
            )
            part = rng.choice([p for p in self.parts.values() if p.active])
            question = rng.choice(texts.COPILOT_QUESTIONS[desired]).format(
                station=st.code, reason_lower=reason.label.lower(), part=part.number
            )
            text = question.lower()
            intent = self._classify(text)
            language = "ta" if any("஀" <= ch <= "௿" for ch in question) else "en"
            sufficiency = self._sufficiency(asked, day)
            tier = coach_tier(sufficiency)
            bundle = FactBundle(plant_name=self.spec.name, as_of=day, tier=int(tier))
            station_code = next(
                (
                    c
                    for c in sorted(self.stations, key=len, reverse=True)
                    if c.lower() in text
                ),
                None,
            )
            if station_code:
                bundle.facts["about_station"] = station_code
            if intent == "period_loss":
                start = day - timedelta(days=7)
                report, previous = (
                    self._ranked(start, day),
                    self._ranked(start - timedelta(days=7), start),
                )
                bundle.facts.update(
                    {
                        "period": f"{start.isoformat()} to {day.isoformat()}",
                        "recoverable_loss": format_inr(report.recoverable_total),
                        "previous_period_loss": format_inr(previous.recoverable_total),
                        "change": format_inr(
                            report.recoverable_total - previous.recoverable_total
                        ),
                    }
                )
                bundle.evidence = (
                    [
                        item
                        for item in report.recoverable
                        if item["station"] == station_code
                    ][:5]
                    if station_code
                    else report.recoverable[:5]
                )
            elif intent == "memory":
                terms = [w for w in text.split() if len(w) > 3][:6]
                matches = [
                    m
                    for m in self.memory
                    if m["occurred_on"]
                    and m["occurred_on"] <= day
                    and any(
                        t in (m["what_fixed_it"] or "").lower()
                        or t in (m["problem_summary"] or "").lower()
                        or t in (m["check_first_next_time"] or "").lower()
                        for t in terms
                    )
                ]
                matches.sort(key=lambda m: m["occurred_on"], reverse=True)
                bundle.evidence = [
                    {
                        "when": m["occurred_on"].isoformat(),
                        "problem": m["problem_summary"],
                        "what_fixed_it": m["what_fixed_it"],
                        "replaced": m["what_was_replaced"],
                        "check_first": m["check_first_next_time"],
                        "fixed_by": m["fixed_by_name"],
                    }
                    for m in matches[:5]
                ]
                if not bundle.evidence:
                    bundle.limits.append(
                        "Factory Memory has no recorded fix matching this. Say so plainly."
                    )
            elif intent == "quality":
                records = [
                    r
                    for r in self.quality_rows
                    if day - timedelta(days=21) <= r["business_date"] <= day
                ]
                good, rejected = (
                    sum(r["good_count"] for r in records),
                    sum(r["reject_count"] for r in records),
                )
                totals = defaultdict(int)
                for record in records:
                    for entry in record["defects"] or []:
                        totals[entry["code"]] += entry["count"]
                top = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:3]
                bundle.facts.update(
                    {
                        "period_days": 21,
                        "good_parts": good,
                        "rejected_parts": rejected,
                        "ppm": float(
                            ppm(rejected_parts=rejected, total_inspected=good + rejected)
                        ),
                        "top_defects": [
                            {"code": c, "label": self.defect_labels.get(c, c), "count": n}
                            for c, n in top
                        ],
                    }
                )
            elif intent == "pattern":
                if tier < CoachTier.PATTERN:
                    bundle.limits.append(
                        f"Only {sufficiency.complete_shifts} shifts of history exist. Pattern questions cannot be answered yet. Say: 'I've seen {sufficiency.distinct_events} events like this; I'll give you pattern analysis once I've seen more.'"
                    )
                else:
                    patterns = self._patterns(day, station_code=station_code)
                    bundle.facts["recurring_patterns"] = patterns
                    if station_code and not patterns:
                        bundle.limits.append(
                            f"{station_code} has no recurring pattern in the last 4 weeks (nothing repeated three or more times). Say that plainly rather than describing a different machine."
                        )
            else:
                report = self._ranked(day, day)
                bundle.facts.update(
                    {
                        "today_recoverable_loss": format_inr(report.recoverable_total),
                        "today_downtime_minutes": float(report.total_minutes),
                    }
                )
                bundle.evidence = report.recoverable[:5]
            answer = _deterministic_answer(bundle)
            llm = (
                self.spec.code == "KUMARAN"
                and day >= date(2026, 8, 10)
                and rng.random() < 0.3
            )
            self.s.add(
                "copilot_queries",
                at=asked,
                plant_id=self.pid,
                user_id=rng.choice(askers)["id"],
                channel="whatsapp"
                if self.spec.whatsapp_approved and rng.random() < 0.22
                else "web",
                question=question,
                language=language,
                intent=intent,
                tier_required="pattern" if intent == "pattern" else "factual",
                tier_available=not bundle.limits or intent != "pattern",
                fact_bundle={
                    "facts": bundle.facts,
                    "evidence": bundle.evidence,
                    "limits": bundle.limits,
                },
                answer=answer,
                answered_by="llm" if llm else "deterministic",
                latency_ms=rng.randint(900, 3800) if llm else rng.randint(28, 420),
            )

    # ======================================================================
    # Layer 5 plugins
    # ======================================================================

    def _kaizen(self, day: date) -> None:
        rng = self.rng
        if (
            day < self.spec.go_live + timedelta(days=5)
            or rng.random() > self.kaizen_rate * 1.15
        ):
            return
        submitted = at(day, f"{rng.randint(10, 18):02d}:{rng.randint(0, 59):02d}")
        if submitted > self.now:
            return
        title, description = rng.choice(texts.KAIZEN_TEMPLATES)
        operator = rng.choice([o for o in self.operators if o.active])
        chronic = rng.choice(self.spec.chronic)
        st = (
            self.stations[chronic.station]
            if rng.random() < 0.5
            else (rng.choice(self.producing) if rng.random() < 0.8 else None)
        )
        if st is None:
            named = next(
                (
                    c
                    for c in sorted(self.stations, key=len, reverse=True)
                    if c.lower() in f"{title} {description}".lower()
                ),
                None,
            )
            st = self.stations.get(named) if named else None
        reason_code = match_reason(title, description)
        observed = occurrences = 0
        scope = "the plant"
        since = day - timedelta(days=90)
        if reason_code:
            for offset in range(91):
                for event in self.events_by_date.get(since + timedelta(days=offset), []):
                    if (
                        event.reason.code == reason_code
                        and event.reason.rs == "R"
                        and (st is None or event.station is st)
                    ):
                        observed += event.row["cost_total"]
                        occurrences += 1
            if st:
                scope = st.code
        share = SCOPED_SHARE if st else UNSCOPED_SHARE
        annualised = int(observed * (365 / 90)) if observed else 0
        saving = int(annualised * share)
        impact = (
            5
            if saving >= 50_00_000
            else 4
            if saving >= 10_00_000
            else 3
            if saving >= 2_00_000
            else 2
            if saving > 0
            else 1
        )
        if reason_code and occurrences:
            rationale = (
                f"Matched to '{reason_code.replace('_', ' ').title()}' on {scope}: {occurrences} events costing {format_inr(observed)} in the last 90 days. Annualised that is {format_inr(annualised)}; the "
                + (
                    "estimate credits a quarter of it, because ideas usually reduce a loss rather than remove it."
                    if st
                    else "estimate credits a tenth of it, because the idea names no machine and cannot plausibly remove that loss everywhere. Naming the station would sharpen the estimate."
                )
            )
        elif reason_code:
            rationale = f"Matched to '{reason_code.replace('_', ' ').title()}', but no losses recorded against it in the last 90 days — so there is no measured saving to claim yet."
        else:
            rationale = "No downtime reason matched this idea, so it carries no estimated saving. Worth reviewing on its merits — safety, effort and quality improvements are real even when the system cannot price them."
        self.kaizen_seq += 1
        reason = self.reasons.get(reason_code) if reason_code else None
        idea = self.s.add(
            "kaizen_ideas",
            at=submitted,
            plant_id=self.pid,
            reference=f"KZN-{self.kaizen_seq:04d}",
            title=title,
            description=description,
            submitted_by_name=operator.name,
            submitted_by_operator_id=operator.id,
            station_id=st.id if st else None,
            reason_code_id=reason.id if reason else None,
            status="scored",
            estimated_annual_saving=saving,
            effort_score=3,
            impact_score=impact,
            score_rationale=rationale,
            linked_loss_paise=observed,
            action_id=None,
            decided_at=None,
            decision_note=None,
        )
        self.kaizen_rows.append(idea)
        decide = next_working_day(day, rng.randint(2, 8))
        roll = rng.random()
        self.kaizen_decisions[idea["reference"]] = (
            decide,
            "accept"
            if (roll < 0.42 and saving > 0) or roll < 0.15
            else "reject"
            if roll < 0.62
            else None,
            st,
            reason,
        )

    def _kaizen_decisions(self, day: date) -> None:
        rng = self.rng
        for idea in self.kaizen_rows:
            decision = self.kaizen_decisions.get(idea["reference"])
            if not decision or decision[0] > day or decision[1] is None:
                continue
            when = at(day, "09:20") + timedelta(minutes=rng.randint(0, 90))
            if when > self.now:
                continue
            _, verdict, st, reason = self.kaizen_decisions.pop(idea["reference"])
            if verdict == "reject":
                idea.update(
                    status="rejected",
                    decided_at=when,
                    decision_note=rng.choice(texts.KAIZEN_REJECT_NOTES),
                    effort_score=rng.randint(3, 5),
                    updated_at=when,
                )
                continue
            plan = self._create_action(
                when=when,
                day=day,
                title=idea["title"],
                description=idea["description"],
                trigger="kaizen",
                rationale=f"Kaizen idea {idea['reference']} from {idea['submitted_by_name']}",
                station=st,
                reason=reason,
                linked_cost=idea["linked_loss_paise"],
                owner=(idea["submitted_by_name"], None),
                due=day + timedelta(days=14),
            )
            plan.kaizen = idea
            idea.update(
                status="accepted",
                action_id=plan.action["id"],
                decided_at=when,
                decision_note=rng.choice(
                    [
                        None,
                        "Good one — do it this week.",
                        "Approved; maintenance to support.",
                    ]
                ),
                effort_score=rng.randint(1, 4),
                updated_at=when,
            )

    def _periodic(self, day: date) -> None:
        self._kaizen_decisions(day)
        live = (day - self.spec.go_live).days
        if live == 21 or (live in (22, 23) and not getattr(self, "_capa_done", False)):
            self._capa(day)
        if live == 28 or (live in (29, 30) and not getattr(self, "_skills_done", False)):
            self._skill_needs(day, raise_actions=True)

    def _capa(self, day: date) -> None:
        self._capa_done = True
        when = at(day, "11:00")
        scored = self._score_clauses(day, write=False)
        gaps = [(row, score) for row, score in scored if score[0] in ("gap", "partial")][:2]
        for row, (status, _count, _last, note) in gaps:
            self._create_action(
                when=when,
                day=day,
                title=f"CAPA: {row['title']} ({row['standard'].upper().replace('_', ' ')} {row['clause_ref']})",
                description=note,
                trigger="compliance_capa",
                rationale=f"Audit readiness scored this clause '{status}' on {day.isoformat()}.",
                station=None,
                reason=None,
                linked_cost=0,
                owner=(self.head_person.name, self.head_user),
                due=day + timedelta(days=21),
            )

    def _needs(self, day: date) -> list[dict]:
        """``services.skills.detect_needs`` as of ``day``."""
        window = 90
        since = day - timedelta(days=window)
        gaps: dict[str, list] = defaultdict(lambda: [0, 0, Decimal(0)])
        for offset in range(window + 1):
            for event in self.events_by_date.get(since + timedelta(days=offset), []):
                if event.reason.code == "NO_OPERATOR":
                    bucket = gaps[event.station.code]
                    bucket[0] += 1
                    bucket[1] += event.row["cost_total"]
                    bucket[2] += Decimal(event.row["duration_minutes"])
        needs = []
        for code, (count, cost, minutes) in sorted(
            gaps.items(), key=lambda kv: kv[1][1], reverse=True
        ):
            if count < MIN_GAP_EVENTS:
                continue
            needs.append(
                {
                    "station": self.stations[code],
                    "kind": "gap",
                    "annual": int(cost * (365 / window)),
                    "evidence": {
                        "events": count,
                        "observed_paise": cost,
                        "minutes": float(minutes),
                        "window_days": window,
                    },
                    "recommendation": f"{code} lost {format_inr(cost)} to 'No Operator' across {count} events in {window} days. Certify at least one more operator on {code}.",
                }
            )
        already = {n["station"].code for n in needs}
        shift_minutes = self.shifts[0].planned
        for st in sorted(self.producing, key=lambda s: (s.spec.line or "", s.code)):
            coverage = self._coverage(st.code)
            if coverage >= MIN_COVERAGE or st.code in already:
                continue
            part = self.last_part_at.get(st.code)
            op = (
                next((o for o in part.ops if o.family_id == st.family_id), None)
                if part
                else None
            )
            cost = downtime_event_cost(shift_minutes, self._snapshot(st, part, op, day))
            basis = {
                "shift_minutes": shift_minutes,
                "part": part.number if part else None,
                "time_cost_paise": cost.machine_cost
                + cost.labour_cost
                + cost.overhead_cost,
                "lost_contribution_paise": cost.lost_contribution,
                "bottleneck_factor": float(st.bf),
            }
            text = (
                f"{st.code} has {coverage} certified operator{'' if coverage == 1 else 's'}. "
                + (
                    "Nobody is certified, so it cannot run at all without supervision. "
                    if coverage == 0
                    else "One absence stops it"
                    + (
                        " — and it is a bottleneck, so it stops the line. "
                        if st.spec.bottleneck
                        else ". "
                    )
                )
                + f"Each shift it stands idle costs about {format_inr(cost.total)}. Train a second."
            )
            needs.append(
                {
                    "station": st,
                    "kind": "coverage",
                    "annual": 0,
                    "evidence": {
                        "certified_operators": coverage,
                        "minimum": MIN_COVERAGE,
                        "is_bottleneck": st.spec.bottleneck,
                        "exposure_per_day_paise": cost.total,
                        "basis": basis,
                    },
                    "recommendation": text,
                }
            )
        return needs

    def _skill_needs(self, day: date, *, raise_actions: bool) -> None:
        self._skills_done = True
        when = min(at(day, "07:00"), self.now)
        needs = self._needs(day)
        rows = []
        for need in needs:
            rows.append(
                self.s.add(
                    "training_needs",
                    at=when,
                    plant_id=self.pid,
                    station_id=need["station"].id,
                    operator_id=None,
                    kind=need["kind"],
                    annual_loss_paise=need["annual"],
                    evidence=need["evidence"],
                    recommendation=need["recommendation"],
                    action_id=None,
                    resolved=False,
                )
            )
        if not raise_actions:
            return
        for row, need in list(zip(rows, needs, strict=True))[:2]:
            evidence = need["evidence"]
            plan = self._create_action(
                when=when + timedelta(hours=3),
                day=day,
                title=f"Train a second operator on {need['station'].code}",
                description=need["recommendation"],
                trigger="skill_gap",
                rationale=f"Skill Matrix gap: {format_inr(need['annual'])}/year lost to 'No Operator'"
                if need["kind"] == "gap"
                else f"Skill Matrix coverage risk: {format_inr(int(evidence.get('exposure_per_day_paise', 0)))} per shift the station stands idle",
                station=need["station"],
                reason=None,
                linked_cost=need["annual"],
                owner=(self.head_person.name, self.head_user),
                due=day + timedelta(days=30),
            )
            plan.need = row
            row["action_id"] = plan.action["id"]

    def _score_clauses(self, as_of: date, *, write: bool) -> list[tuple[dict, tuple]]:
        """``services.compliance.score_readiness`` (90-day window) as of ``as_of``."""
        since = as_of - timedelta(days=90)
        moment = at(as_of, "23:59") if as_of < self.as_of else self.now
        events = [
            e for d, evs in self.events_by_date.items() if since <= d <= as_of for e in evs
        ]

        def verdict(count, last, ready_at, note):
            if count >= ready_at:
                return ("ready", count, last, None)
            if count > 0:
                return ("partial", count, last, note)
            return ("gap", 0, None, note)

        def latest(values):
            values = [v for v in values if v is not None]
            return max(values) if values else None

        breakdowns = [e for e in events if e.reason.breakdown]
        counts = [
            r
            for r in self.s.rows["production_counts"]
            if r["plant_id"] == self.pid and since <= r["business_date"] <= as_of
        ]
        lots = [
            r
            for r in self.s.rows["lots"]
            if r["plant_id"] == self.pid and r["opened_at"] and r["opened_at"] <= moment
        ]
        quality = [r for r in self.quality_rows if since <= r["business_date"] <= as_of]
        shifts = [
            r
            for r in self.shift_rows
            if r["status"] == "closed" and since <= r["business_date"] <= as_of
        ]
        closed_actions = [
            p.action
            for p in self.actions
            if p.action["closed_at"] and p.action["closed_at"] <= moment
        ]
        rcas = [
            p.rca
            for p in self.actions
            if p.rca and p.rca["completed_at"] and p.rca["completed_at"] <= moment
        ]
        skills = [r for r in self.skills.values() if r["created_at"] <= moment]
        ideas = [r for r in self.kaizen_rows if r["created_at"] <= moment]
        verified = [p.action for p in self.actions if p.action["state"] == "verified"]
        scorers = {
            "downtime_maintenance": lambda: verdict(
                len(breakdowns),
                latest(e.start for e in breakdowns),
                5,
                "Log breakdowns with a reason code so maintenance history builds.",
            ),
            "production_records": lambda: verdict(
                len(counts),
                latest(r["recorded_at"] for r in counts),
                100,
                "Production counts are thin for this period — an auditor will ask for daily records.",
            ),
            "lot_traceability": lambda: verdict(
                len(lots),
                latest(r["opened_at"] for r in lots),
                20,
                "Start lot identification — day-batch lots are enough to satisfy this clause.",
            ),
            "quality_records": lambda: verdict(
                len(quality),
                latest(r["recorded_at"] for r in quality),
                20,
                "Record rejections with defect codes at batch completion.",
            ),
            "kpi_history": lambda: verdict(
                len(shifts),
                latest(r["locked_at"] for r in shifts),
                30,
                "Close shifts consistently — locked shifts are what make the KPI history defensible.",
            ),
            "actions_closed": lambda: verdict(
                len(closed_actions),
                latest(a["closed_at"] for a in closed_actions),
                5,
                "Close actions with a root cause and countermeasure — that record is the corrective-action evidence.",
            ),
            "skill_matrix": lambda: verdict(
                len(skills),
                None,
                max(1, sum(1 for o in self.operators if o.active)),
                "Record which operator is certified on which station — the Skill Matrix is the competence evidence.",
            ),
            "maintenance_kpis": lambda: verdict(
                len(breakdowns),
                latest(e.start for e in breakdowns),
                10,
                "MTBF and MTTR need closed breakdown events with durations.",
            ),
            "oee_history": lambda: verdict(
                len(shifts),
                latest(r["locked_at"] for r in shifts),
                30,
                "Close shifts consistently — locked shifts are what make the KPI history defensible.",
            ),
            "rca_verified": lambda: verdict(
                len(rcas),
                latest(r["completed_at"] for r in rcas),
                3,
                "Complete the 5-Why on escalated actions — auditors ask to see root cause, not just the fix.",
            ),
            "kaizen_pipeline": lambda: verdict(
                len(ideas),
                latest(r["created_at"] for r in ideas),
                5,
                "Capture improvement ideas — a continual-improvement clause needs a visible pipeline.",
            ),
            "recovered_profit": lambda: verdict(
                len(verified),
                latest(a["verified_at"] for a in verified),
                3,
                "Verified actions with measured savings are the before/after benefit this clause asks for.",
            ),
        }
        out = []
        for row in self.clause_rows:
            score = scorers[row["evidence_source"]]()
            if write:
                row.update(
                    status=score[0],
                    evidence_count=score[1],
                    last_evidence_at=score[2],
                    gap_note=score[3],
                    updated_at=moment,
                )
            out.append((row, score))
        return out

    # ======================================================================
    # end state
    # ======================================================================

    def _finalise(self) -> None:
        rng, spec = self.rng, self.spec

        # Skill Matrix regenerated this morning: stale unactioned needs are replaced.
        stale = [
            r
            for r in self.s.rows["training_needs"]
            if r["plant_id"] == self.pid and not r["resolved"] and r["action_id"] is None
        ]
        for row in stale:
            self.s.rows["training_needs"].remove(row)
        self._skill_needs(self.as_of, raise_actions=False)

        self._score_clauses(self.as_of, write=True)
        for standard in ("iso_9001", "iatf_16949", "zed"):
            scoped = [r for r in self.clause_rows if r["standard"] == standard]
            clauses = [
                SimpleNamespace(status=ClauseStatus(r["status"]), standard=standard)
                for r in scoped
            ]
            score = readiness_score(clauses)
            generated = self.now - timedelta(
                days=rng.randint(0, 3), hours=rng.randint(1, 5)
            )
            self.s.add(
                "audit_packs",
                at=generated,
                plant_id=self.pid,
                standard=standard,
                period_start=max(spec.go_live, self.as_of - timedelta(days=90)),
                period_end=self.as_of,
                readiness_score=score,
                clause_summary=[
                    {
                        "clause_ref": r["clause_ref"],
                        "title": r["title"],
                        "status": r["status"],
                        "evidence_count": r["evidence_count"],
                        "gap_note": r["gap_note"],
                        "also_satisfies": r["also_satisfies"],
                    }
                    for r in scoped
                ],
                evidence={r["clause_ref"]: self._evidence(r) for r in scoped},
                generated_by_id=self.head_user["id"],
            )

        # A shift reopened by the owner to fix a reason code, not yet re-closed.
        if spec.code in ("KUMARAN", "SRIVARI"):
            recent = [
                r
                for r in self.shift_rows
                if r["status"] == "closed"
                and (self.as_of - r["business_date"]).days in range(1, 6)
            ]
            if recent:
                row = rng.choice(recent)
                when = row["locked_at"] + timedelta(hours=rng.randint(10, 30))
                if when < self.now:
                    row.update(
                        status="reopened",
                        locked_at=None,
                        override_reason="Owner reopened: Press stop was logged as Material Wait, maintenance confirms it was a Die Crack.",
                        override_by_id=self.owner_user["id"],
                        updated_at=when,
                    )
                    self._audit(
                        when,
                        self.owner_user,
                        "shift_session",
                        row["id"],
                        "update",
                        {"status": "closed"},
                        {"status": "reopened"},
                        row["override_reason"],
                    )

        # Job orders: paused and planned states, and a couple of cancellations.
        for job in self.jobs:
            if job.row["status"] == "running" and job.paused:
                job.row["status"] = "paused"
        queued = [
            j
            for family in self.queue.values()
            for j in family
            if j.row["status"] in ("running", "paused")
        ]
        for job in rng.sample(queued, k=min(2, len(queued))):
            when = self.now - timedelta(days=rng.randint(0, 4), hours=rng.randint(1, 6))
            job.row.update(
                status="cancelled",
                notes=f"{job.row['notes'] or ''} Customer cancelled the balance quantity.".strip(),
                updated_at=when,
            )
            self._audit(
                when,
                self.owner_user,
                "job_order",
                job.row["id"],
                "update",
                {"status": "running"},
                {"status": "cancelled"},
                "Customer schedule cut",
            )
        for _ in range(rng.randint(3, 6)):
            part = rng.choice([p for p in self.parts.values() if p.active])
            self.job_seq += 1
            number = {
                "KUMARAN": f"{2200 + self.job_seq}",
                "SRIVARI": f"SVP-{5100 + self.job_seq}",
                "KOVAI": f"JO-26-{self.job_seq:04d}",
                "SAKTHI": f"W{3300 + self.job_seq}",
            }[spec.code]
            low, high = part.spec.lot_size
            self.s.add(
                "job_orders",
                at=self.now - timedelta(hours=rng.randint(2, 40)),
                plant_id=self.pid,
                job_number=number,
                part_id=part.id,
                customer_name=part.spec.customer,
                planned_quantity=rng.randint(low, high),
                status="planned",
                due_date=self.as_of + timedelta(days=rng.randint(4, 14)),
                opened_at=None,
                completed_at=None,
                notes=None,
            )

        for lot in self.batch_lots.values():
            lot["updated_at"] = lot["closed_at"] or lot["opened_at"]

    def _evidence(self, clause: dict) -> list[dict]:
        source = clause["evidence_source"]
        if source in ("actions_closed", "rca_verified", "recovered_profit"):
            rows = sorted(
                (p.action for p in self.actions if p.action["closed_at"]),
                key=lambda a: a["closed_at"],
                reverse=True,
            )[:10]
            return [
                {
                    "reference": a["reference"],
                    "title": a["title"],
                    "root_cause": a["root_cause"],
                    "countermeasure": a["countermeasure"],
                    "closed": a["closed_at"].date().isoformat(),
                    "verified": a["state"] == "verified",
                    "owner": a["owner_name"],
                }
                for a in rows
            ]
        if source in ("downtime_maintenance", "maintenance_kpis"):
            rows = sorted(
                (
                    e
                    for evs in self.events_by_date.values()
                    for e in evs
                    if e.reason.breakdown
                ),
                key=lambda e: e.start,
                reverse=True,
            )[:10]
            return [
                {
                    "date": e.bd.isoformat(),
                    "station": e.station.code,
                    "reason": e.reason.label,
                    "minutes": float(e.row["duration_minutes"]),
                    "cost_paise": e.row["cost_total"],
                }
                for e in rows
            ]
        if source == "quality_records":
            return [
                {
                    "date": r["business_date"].isoformat(),
                    "good": r["good_count"],
                    "rejected": r["reject_count"],
                    "defects": r["defects"],
                    "disposition": r["disposition"],
                }
                for r in sorted(
                    self.quality_rows, key=lambda r: r["recorded_at"], reverse=True
                )[:10]
            ]
        if source == "skill_matrix":
            return [
                {
                    "operator": self.by_operator[r["operator_id"]].name,
                    "station": next(
                        s.code for s in self.stations.values() if s.id == r["station_id"]
                    ),
                    "level": r["level"],
                    "certified_on": r["certified_on"].isoformat()
                    if r["certified_on"]
                    else None,
                }
                for r in list(self.skills.values())[:10]
            ]
        return [
            {
                "note": f"{clause['evidence_count']} records in the last 90 days",
                "last": clause["last_evidence_at"].isoformat()
                if clause["last_evidence_at"]
                else None,
            }
        ]

    # ======================================================================

    def _audit(
        self,
        when: datetime,
        actor: dict | None,
        entity: str,
        entity_id,
        action: str,
        before,
        after,
        reason: str | None,
    ) -> None:
        self.s.add(
            "audit_log",
            at=when,
            plant_id=self.pid,
            actor_id=actor["id"] if actor else None,
            entity_type=entity,
            entity_id=entity_id,
            action=action,
            before=before,
            after=after,
            reason=reason,
            occurred_at=when,
        )
