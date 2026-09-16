"""§4.1 — onboarding. Upload first, type last.

"Onboarding is the single biggest adoption risk in this product. If setup
 takes three days of manual typing per plant, it does not scale ... never
 type what can be uploaded, never demand what can be estimated."
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.ingest import defaults as default_library
from app.ingest import readiness as readiness_service
from app.ingest import templates as template_service
from app.kernel import (
    blended_machine_rate,
    labour_cost_rate,
    machine_cost_rate,
    overhead_rate,
    rupees,
)
from app.models import (
    AlertRecipient,
    CostFamily,
    CostRate,
    DefectCode,
    EscalationRoute,
    MarginBand,
    MarginBandValue,
    OeeTreatment,
    Operator,
    Part,
    Plant,
    ProductionTarget,
    RateSource,
    ReadinessAssessment,
    ReasonCode,
    Recoverability,
    Role,
    RoutingOperation,
    Section,
    Shift,
    Station,
    TemplateUpload,
)
from app.schemas import ReadinessResult, ReadinessSubmission, UploadConfirm, UploadPreview

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

MAX_UPLOAD_BYTES = 5_000_000


# ---------------------------------------------------------------------------
# Step 0 — the readiness questionnaire (public: answered before we ever visit)
# ---------------------------------------------------------------------------


@router.get("/readiness/questions")
async def readiness_questions():
    return [
        {
            "key": q.key,
            "prompt": q.prompt,
            "section": q.section,
        }
        for q in readiness_service.QUESTIONS
    ]


@router.post("/readiness", response_model=ReadinessResult)
async def submit_readiness(
    payload: ReadinessSubmission, session: AsyncSession = Depends(get_session)
):
    """Scored on submit and returned immediately — it is a sales artefact."""
    outcome = readiness_service.score(payload.answers)

    session.add(
        ReadinessAssessment(
            plant_id=None,
            plant_name=payload.plant_name,
            respondent_name=payload.respondent_name,
            respondent_phone=payload.respondent_phone,
            answers=payload.answers,
            score=outcome.score,
            band=outcome.band,
            priority_gaps=outcome.priority_gaps,
            estimated_onboarding_hours=outcome.estimated_onboarding_hours,
            completed_at=datetime.now(UTC),
        )
    )

    return ReadinessResult(
        score=outcome.score,
        band=outcome.band,
        priority_gaps=outcome.priority_gaps,
        estimated_onboarding_hours=outcome.estimated_onboarding_hours,
        message=outcome.message,
    )


# ---------------------------------------------------------------------------
# Step 1 — upload the masters the plant already has
# ---------------------------------------------------------------------------


@router.get("/templates")
async def list_templates():
    return [
        {
            "code": spec.code,
            "name": spec.name,
            "required_columns": list(spec.required),
            "optional_columns": list(spec.optional),
            # §15: T1+T2 are the go-live minimum.
            "go_live_minimum": spec.code in ("T1", "T2"),
        }
        for spec in template_service.TEMPLATES.values()
    ]


@router.get("/templates/{code}/blank")
async def download_blank(code: str):
    """The exact file to fill in — with a worked example row already in it."""
    if code.upper() not in template_service.TEMPLATES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown template")
    content = template_service.build_blank(code)
    return Response(
        content=content,
        media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={
            "Content-Disposition": f'attachment; filename="FactoryManager_{code.upper()}.xlsx"'
        },
    )


@router.post(
    "/upload/{code}",
    response_model=UploadPreview,
    dependencies=[Depends(require(Role.CONSULTANT, Role.OWNER, Role.ADMIN))],
)
async def upload(
    code: str,
    file: UploadFile = File(...),
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Parse and preview. Nothing is written to the masters until confirmed.

    The confirm screen is the 30-60 minute verification that replaces days of
    typing: "Found: 132 part numbers, 19 machines, 41 operators, 3 shifts.
    Confirm?"
    """
    # Templates are a few hundred rows; anything this large is the wrong file.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Template files must be under {MAX_UPLOAD_BYTES // 1_000_000} MB",
        )
    if not content.startswith(b"PK"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Upload the template as an .xlsx file"
        )
    try:
        result = template_service.parse(code, content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Could not read that file as a {code.upper()} template: {exc}",
        ) from exc

    upload_row = TemplateUpload(
        plant_id=plant.id,
        template=result.template,
        filename=file.filename or "upload.xlsx",
        uploaded_by_id=identity.user.id,
        status="parsed" if result.ok else "errors",
        rows_found=len(result.rows),
        preview=[_jsonable(row) for row in result.rows],
        errors=[error.as_dict() for error in result.errors],
    )
    session.add(upload_row)
    await session.flush()

    return UploadPreview(
        upload_id=upload_row.id if result.ok else None,
        template=result.template,
        filename=upload_row.filename,
        rows_found=len(result.rows),
        columns=result.columns,
        preview=[_jsonable(row) for row in result.rows[:10]],
        errors=[error.as_dict() for error in result.errors],
        summary=result.summary() + (". Confirm?" if result.ok else ""),
    )


@router.post(
    "/confirm",
    dependencies=[Depends(require(Role.CONSULTANT, Role.OWNER, Role.ADMIN))],
)
async def confirm(
    payload: UploadConfirm,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Write a verified upload into the masters. Idempotent per natural key."""
    upload_row = await session.get(TemplateUpload, payload.upload_id)
    if upload_row is None or upload_row.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    if upload_row.confirmed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already confirmed")
    if upload_row.status == "errors":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Fix the errors and re-upload first"
        )

    importer = IMPORTERS[upload_row.template]
    imported = await importer(session, plant, upload_row.preview or [])

    upload_row.rows_imported = imported
    upload_row.confirmed_at = datetime.now(UTC)
    upload_row.status = "imported"

    return {"imported": imported, "template": upload_row.template}


@router.post(
    "/defaults",
    dependencies=[Depends(require(Role.CONSULTANT, Role.OWNER, Role.ADMIN))],
)
async def load_defaults(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """§15 T6 — load the default reason and defect codes, keeping any the plant has."""
    return await load_default_codes(session, plant)


async def load_default_codes(session: AsyncSession, plant: Plant) -> dict:
    have_reasons = set(
        (await session.scalars(select(ReasonCode.code).where(ReasonCode.plant_id == plant.id))).all()
    )
    have_defects = set(
        (await session.scalars(select(DefectCode.code).where(DefectCode.plant_id == plant.id))).all()
    )
    reasons_added = defects_added = 0
    for order, (code, label, recoverability, treatment, breakdown, route) in enumerate(
        default_library.DOWNTIME_CODES, start=1
    ):
        if code in have_reasons:
            continue
        session.add(
            ReasonCode(
                plant_id=plant.id,
                code=code,
                label=label,
                recoverability=Recoverability(recoverability),
                oee_treatment=OeeTreatment(treatment),
                is_breakdown=breakdown,
                escalation_route=EscalationRoute(route),
                display_order=order,
            )
        )
        reasons_added += 1
    for order, (code, label) in enumerate(default_library.DEFECT_CODES, start=1):
        if code in have_defects:
            continue
        session.add(DefectCode(plant_id=plant.id, code=code, label=label, display_order=order))
        defects_added += 1
    await session.flush()
    return {"reason_codes_added": reasons_added, "defect_codes_added": defects_added}


# ---------------------------------------------------------------------------
# importers — one per template
# ---------------------------------------------------------------------------


async def _import_t1(session: AsyncSession, plant: Plant, rows: list[dict]) -> int:
    count = 0
    for row in rows:
        family = None
        if row.get("Cost family"):
            family = await _get_or_create_family(session, plant, row["Cost family"])

        station = await session.scalar(
            select(Station).where(
                Station.plant_id == plant.id, Station.code == row["Station ID"]
            )
        )
        if station is None:
            station = Station(plant_id=plant.id, code=row["Station ID"])
            session.add(station)

        station.name = row["Station name"]
        station.station_type = row.get("Type / family")
        station.section = _section(row.get("Section"))
        station.line = row.get("Line")
        station.zone = row.get("Zone")
        station.is_bottleneck = bool(row.get("Bottleneck?"))
        # §8.2 — BF is set per machine on the floor walk: 1.0 for the bottleneck,
        # 0-0.5 for a machine whose stops a downstream buffer absorbs, and 1.0
        # where unknown, which keeps the number honest-by-overstatement rather
        # than silently discounting a real loss.
        factor = row.get("Bottleneck factor")
        station.bottleneck_factor = (
            Decimal("1.00")
            if factor is None or station.is_bottleneck
            else Decimal(str(factor)).quantize(Decimal("0.01"))
        )
        station.cost_family_id = family.id if family else None
        count += 1
    await session.flush()
    return count


async def _import_t2(session: AsyncSession, plant: Plant, rows: list[dict]) -> int:
    count = 0
    for row in rows:
        part = await session.scalar(
            select(Part).where(
                Part.plant_id == plant.id, Part.part_number == row["Part number"]
            )
        )
        if part is None:
            part = Part(plant_id=plant.id, part_number=row["Part number"])
            session.add(part)

        part.description = row.get("Description")
        part.customer_name = row.get("Customer name")
        part.part_family = row.get("Part family")
        if row.get("Contribution") is not None:
            part.contribution_margin = rupees(row["Contribution"])
        if row.get("Margin band"):
            part.margin_band = MarginBand(str(row["Margin band"]).upper()[:1])
        if row.get("Material cost") is not None:
            part.material_cost = rupees(row["Material cost"])
        await session.flush()

        if row.get("Routing"):
            await _import_routing(session, plant, part, str(row["Routing"]))
        count += 1

    await session.flush()
    return count


async def _import_routing(
    session: AsyncSession, plant: Plant, part: Part, routing: str
) -> None:
    """'Op10 CNC-family > Op20 Assembly-L1 > Op30 QC > Op40 Dispatch'.

    The routing is parsed into real operation rows from day one even when the
    plant is single-operation — §9 is explicit that retrofitting a routing later
    is a rebuild.
    """
    steps = [step.strip() for step in routing.replace("→", ">").split(">") if step.strip()]
    for position, step in enumerate(steps, start=1):
        parts = step.split(None, 1)
        operation_code = parts[0]
        family_name = parts[1].strip() if len(parts) > 1 else None
        family = (
            await _get_or_create_family(session, plant, family_name)
            if family_name
            else None
        )

        existing = await session.scalar(
            select(RoutingOperation).where(
                RoutingOperation.plant_id == plant.id,
                RoutingOperation.part_id == part.id,
                RoutingOperation.sequence == position * 10,
            )
        )
        if existing is None:
            existing = RoutingOperation(
                plant_id=plant.id, part_id=part.id, sequence=position * 10
            )
            session.add(existing)

        existing.operation_code = operation_code
        existing.name = family_name
        existing.cost_family_id = family.id if family else None
        existing.section = _section_for_operation(family_name or operation_code)
    await session.flush()


async def _import_t3(session: AsyncSession, plant: Plant, rows: list[dict]) -> int:
    count = 0
    for row in rows:
        part = await session.scalar(
            select(Part).where(
                Part.plant_id == plant.id, Part.part_number == row["Part number"]
            )
        )
        if part is None:
            continue

        operation = await session.scalar(
            select(RoutingOperation).where(
                RoutingOperation.plant_id == plant.id,
                RoutingOperation.part_id == part.id,
                RoutingOperation.operation_code == row["Operation"],
            )
        )
        if operation is None:
            continue

        operation.cycle_time_min = Decimal(str(row["Cycle time (min/part)"]))
        # §14.4's three-rung ladder: store *which* rung, so '±30%' can be said
        # honestly instead of implying precision the input never had.
        operation.cycle_time_source = _rate_source(row.get("Source"))
        count += 1
    await session.flush()
    return count


async def _import_t4(session: AsyncSession, plant: Plant, rows: list[dict]) -> int:
    """§15 T4 — cost rates, entered directly or derived with §8.1's formulas.

    Rates are effective-dated: an upload adds rates from its effective date and
    never rewrites what past events were costed with (C3).
    """
    count = 0
    for row in rows:
        scope = str(row["Scope"]).strip().lower()
        reference = str(row["Reference"]).strip()
        effective = _effective_date(row.get("Effective from"))

        if scope == "margin_band":
            for band in ("L", "M", "H"):
                value = row.get(f"Band {band}")
                if value is None:
                    continue
                existing = await session.scalar(
                    select(MarginBandValue).where(
                        MarginBandValue.plant_id == plant.id,
                        MarginBandValue.part_family == reference,
                        MarginBandValue.band == band,
                    )
                )
                if existing is None:
                    existing = MarginBandValue(plant_id=plant.id, part_family=reference, band=band)
                    session.add(existing)
                existing.value = rupees(value)
            count += 1
            continue

        rate_type, value, derivation = _t4_rate(scope, row)
        scope_ref = reference
        if scope == "cost_family":
            scope_ref = str((await _get_or_create_family(session, plant, reference)).id)
        elif scope == "section":
            scope_ref = reference.lower()
        else:
            scope_ref = "plant"

        existing = await session.scalar(
            select(CostRate).where(
                CostRate.plant_id == plant.id,
                CostRate.scope == scope,
                CostRate.scope_ref == scope_ref,
                CostRate.effective_from == effective,
            )
        )
        if existing is None:
            existing = CostRate(
                plant_id=plant.id, scope=scope, scope_ref=scope_ref, effective_from=effective
            )
            session.add(existing)

        setattr(existing, f"{rate_type}_rate", value)
        existing.source = _rate_source(row.get("Source") or ("derived" if derivation else None))
        if derivation:
            existing.derivation = {**(existing.derivation or {}), rate_type: derivation}
        count += 1
    await session.flush()
    return count


def _t4_rate(scope: str, row: dict) -> tuple[str, int, dict | None]:
    """Which rate a T4 row sets, its value in paise, and how it was derived."""
    if row.get("Value") is not None:
        text = str(row.get("Rate type") or "").strip().lower()
        rate_type = (
            "machine"
            if text.startswith("machine")
            else "labour"
            if text.startswith(("labour", "labor"))
            else "overhead"
        )
        return rate_type, rupees(row["Value"]), None

    money = {
        column: rupees(value)
        for column, value in row.items()
        if value is not None and (column.startswith("Monthly") or column == "Supervisor share")
    }
    if scope == "cost_family":
        hours = Decimal(str(row["Available hours"]))
        if any(row.get(column) is not None for column in ("Monthly operator wages", "Monthly overhead share")):
            # Basic mode (§4.2): one blended rate — depreciation, power, wages,
            # maintenance and overhead share over available hours.
            value = blended_machine_rate(
                monthly_depreciation=money.get("Monthly depreciation", 0),
                monthly_power=money.get("Monthly power", 0),
                monthly_operator_wages=money.get("Monthly operator wages", 0),
                monthly_maintenance=money.get("Monthly maintenance", 0),
                monthly_overhead_share=money.get("Monthly overhead share", 0),
                monthly_available_hours=hours,
            )
            formula = "blended_machine_rate"
        else:
            value = machine_cost_rate(
                depreciation=money.get("Monthly depreciation", 0),
                power=money.get("Monthly power", 0),
                tooling=money.get("Monthly tooling", 0),
                maintenance=money.get("Monthly maintenance", 0),
                available_hours=hours,
            )
            formula = "machine_cost_rate"
        return "machine", value, {"formula": formula, "inputs": _plain(row), "hours": float(hours)}
    if scope == "section":
        value = labour_cost_rate(
            operator_wages=money["Monthly wage bill"],
            supervisor_share=money.get("Supervisor share", 0),
            worked_hours=Decimal(str(row["Worked hours"])),
        )
        return "labour", value, {"formula": "labour_cost_rate", "inputs": _plain(row)}
    value = overhead_rate(
        total_factory_overhead=money["Monthly overhead"],
        total_available_machine_hours=Decimal(str(row["Available hours"])),
    )
    return "overhead", value, {"formula": "overhead_rate", "inputs": _plain(row)}


def _plain(row: dict) -> dict:
    return {key: value for key, value in row.items() if value is not None}


def _effective_date(value) -> date:
    if not value:
        return date.today()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return date.today()


async def _import_t5(session: AsyncSession, plant: Plant, rows: list[dict]) -> int:
    count = 0
    for row in rows:
        kind = str(row["Kind"]).strip().lower()
        if kind.startswith("operator"):
            existing = await session.scalar(
                select(Operator).where(
                    Operator.plant_id == plant.id, Operator.name == row["Name"]
                )
            )
            if existing is None:
                session.add(
                    Operator(
                        plant_id=plant.id,
                        name=row["Name"],
                        employee_code=row.get("Employee code"),
                    )
                )
        else:
            existing = await session.scalar(
                select(AlertRecipient).where(
                    AlertRecipient.plant_id == plant.id,
                    AlertRecipient.name == row["Name"],
                )
            )
            if existing is None:
                existing = AlertRecipient(plant_id=plant.id, name=row["Name"])
                session.add(existing)
            existing.role_tag = (row.get("Role tag") or "production").lower()
            existing.whatsapp_number = row.get("WhatsApp number")
            existing.escalation_level = int(row.get("Escalation level") or 2)
        count += 1
    await session.flush()
    return count


async def _import_t6(session: AsyncSession, plant: Plant, rows: list[dict]) -> int:
    count = 0
    for row in rows:
        kind = str(row["Kind"]).strip().lower()
        if kind.startswith("defect"):
            existing = await session.scalar(
                select(DefectCode).where(
                    DefectCode.plant_id == plant.id, DefectCode.code == row["Code"]
                )
            )
            if existing is None:
                existing = DefectCode(plant_id=plant.id, code=row["Code"])
                session.add(existing)
            existing.label = row["Label"]
            existing.label_ta = row.get("Label (Tamil)")
            existing.part_family = row.get("Part family")
        else:
            existing = await session.scalar(
                select(ReasonCode).where(
                    ReasonCode.plant_id == plant.id, ReasonCode.code == row["Code"]
                )
            )
            if existing is None:
                existing = ReasonCode(plant_id=plant.id, code=row["Code"])
                session.add(existing)
            existing.label = row["Label"]
            existing.label_ta = row.get("Label (Tamil)")
            existing.recoverability = Recoverability(
                str(row.get("Recoverability") or "R").upper()[:1]
            )
            existing.oee_treatment = OeeTreatment(
                str(row.get("OEE treatment") or "unplanned").lower()
            )
            existing.is_breakdown = bool(row.get("Is breakdown"))
            existing.safety_flag = bool(row.get("Safety flag"))
            existing.escalation_route = EscalationRoute(
                str(row.get("Escalation route") or "production").lower()
            )
        count += 1
    await session.flush()
    return count


async def _import_t7(session: AsyncSession, plant: Plant, rows: list[dict]) -> int:
    count = 0
    for position, row in enumerate(rows, start=1):
        kind = str(row["Kind"]).strip().lower()
        if kind.startswith("shift"):
            existing = await session.scalar(
                select(Shift).where(Shift.plant_id == plant.id, Shift.name == row["Name"])
            )
            if existing is None:
                existing = Shift(plant_id=plant.id, name=row["Name"])
                session.add(existing)
            existing.start_time = _time(row.get("Start time"))
            existing.end_time = _time(row.get("End time"))
            existing.break_minutes = int(row.get("Break minutes") or 0)
            existing.weekly_off = row.get("Weekly off")
            existing.display_order = position
            await session.flush()
        else:
            station = await session.scalar(
                select(Station).where(Station.plant_id == plant.id, Station.code == row["Name"])
            )
            shift = (
                await session.scalar(
                    select(Shift).where(Shift.plant_id == plant.id, Shift.name == row["Shift"])
                )
                if row.get("Shift")
                else None
            )
            line = None if station else (row.get("Line") or row["Name"])
            # One target per station-or-line, per shift, per day: re-uploading
            # the template replaces today's figure instead of stacking another.
            target = await session.scalar(
                select(ProductionTarget).where(
                    ProductionTarget.plant_id == plant.id,
                    ProductionTarget.effective_from == date.today(),
                    ProductionTarget.station_id == station.id
                    if station
                    else ProductionTarget.station_id.is_(None),
                    ProductionTarget.line == line if line else ProductionTarget.line.is_(None),
                    ProductionTarget.shift_id == shift.id
                    if shift
                    else ProductionTarget.shift_id.is_(None),
                )
            )
            if target is None:
                target = ProductionTarget(
                    plant_id=plant.id,
                    station_id=station.id if station else None,
                    line=line,
                    shift_id=shift.id if shift else None,
                    effective_from=date.today(),
                )
                session.add(target)
            target.target_parts = int(row.get("Target parts") or 0)
        count += 1
    await session.flush()
    return count


IMPORTERS = {
    "T1": _import_t1,
    "T2": _import_t2,
    "T3": _import_t3,
    "T4": _import_t4,
    "T5": _import_t5,
    "T6": _import_t6,
    "T7": _import_t7,
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _get_or_create_family(
    session: AsyncSession, plant: Plant, name: str
) -> CostFamily:
    code = name.strip()
    family = await session.scalar(
        select(CostFamily).where(CostFamily.plant_id == plant.id, CostFamily.code == code)
    )
    if family is None:
        family = CostFamily(plant_id=plant.id, code=code, name=code)
        session.add(family)
        await session.flush()
    return family


def _section(value: str | None) -> Section:
    text = (value or "machining").strip().lower()
    for section in Section:
        if section.value.startswith(text[:4]):
            return section
    return Section.MACHINING


def _section_for_operation(label: str) -> Section:
    text = label.lower()
    if "assembly" in text:
        return Section.ASSEMBLY
    if "qc" in text or "quality" in text:
        return Section.QUALITY
    if "dispatch" in text or "despatch" in text:
        return Section.DISPATCH
    return Section.MACHINING


def _rate_source(value: str | None) -> RateSource:
    text = (value or "default").strip().lower()
    if text.startswith("meas") or "route card" in text or "study" in text:
        return RateSource.MEASURED
    if text.startswith("deriv"):
        return RateSource.DERIVED
    return RateSource.DEFAULT


def _time(value):
    from datetime import time as time_type

    if isinstance(value, time_type):
        return value
    text = str(value or "07:00").strip()
    hours, _, minutes = text.partition(":")
    return time_type(int(hours), int(minutes or 0))


def _jsonable(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            out[key] = float(value)
        elif hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out
