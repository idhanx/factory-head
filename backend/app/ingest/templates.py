"""The T1-T7 ingestion contract (§15).

    "This is the ingestion contract for the development team: seven Excel
     templates, every column defined. If it is not in these templates, the
     system does not need it at onboarding."

Parsers are **strict-first**: exact columns, clear error messages. Tolerant
parsing of messy files is an enhancement, not a blocker (§9) — and a strict
parser that says "column 'Cycle time (min/part)' is missing" onboards a plant
faster than a clever one that guesses wrong.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import Workbook, load_workbook


@dataclass(slots=True)
class ParseError:
    row: int
    column: str
    message: str

    def as_dict(self) -> dict:
        return {"row": self.row, "column": self.column, "message": self.message}


@dataclass(slots=True)
class ParseResult:
    template: str
    columns: list[str]
    rows: list[dict]
    errors: list[ParseError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        noun = TEMPLATES[self.template].noun
        return f"Found: {len(self.rows)} {noun}"


@dataclass(slots=True)
class TemplateSpec:
    code: str
    name: str
    noun: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    coercions: dict[str, Callable[[Any], Any]] = field(default_factory=dict)
    #: Checks that span columns; returns (column, message) pairs for one row.
    validate: Callable[[dict], list[tuple[str, str]]] | None = None

    @property
    def columns(self) -> tuple[str, ...]:
        return self.required + self.optional


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"{value!r} is not a number") from exc


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError as exc:
        raise ValueError(f"{value!r} is not a whole number") from exc


def _yesno(value: Any) -> bool:
    return str(value).strip().lower() in {"y", "yes", "true", "1"}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


TEMPLATES: dict[str, TemplateSpec] = {
    # T1+T2 are the go-live minimum: a plant that hands over these two can be
    # live the same day in Basic costing mode (§15 developer note).
    "T1": TemplateSpec(
        code="T1",
        name="Machines & Stations",
        noun="machines/stations",
        required=("Station ID", "Station name", "Type / family", "Section"),
        optional=("Bottleneck?", "Bottleneck factor", "Cost family", "Line", "Zone"),
        coercions={"Bottleneck?": _yesno, "Bottleneck factor": _decimal},
        validate=lambda row: _validate_t1(row),
    ),
    "T2": TemplateSpec(
        code="T2",
        name="Part Master & Routing",
        noun="part numbers",
        required=("Part number", "Description"),
        optional=(
            "Customer name",
            "Part family",
            "Routing",
            "Contribution",
            "Margin band",
            "Material cost",
        ),
        coercions={"Contribution": _decimal, "Material cost": _decimal},
    ),
    "T3": TemplateSpec(
        code="T3",
        name="Cycle Times",
        noun="part-operation cycle times",
        required=("Part number", "Operation", "Station family", "Cycle time (min/part)"),
        optional=("Source",),
        coercions={"Cycle time (min/part)": _decimal},
    ),
    "T4": TemplateSpec(
        code="T4",
        name="Cost Inputs",
        noun="cost rows",
        # §15 T4: A. per cost family, B. per section, C. plant, D. margin bands.
        # A rate can be entered directly (Rate type + Value) or derived from the
        # monthly figures the owner actually knows — the formulas of §8.1.
        required=("Scope", "Reference"),
        optional=(
            "Rate type",
            "Value",
            "Monthly depreciation",
            "Monthly power",
            "Monthly tooling",
            "Monthly maintenance",
            "Monthly operator wages",
            "Monthly overhead share",
            "Available hours",
            "Monthly wage bill",
            "Supervisor share",
            "Worked hours",
            "Monthly overhead",
            "Band L",
            "Band M",
            "Band H",
            "Source",
            "Effective from",
        ),
        coercions=dict.fromkeys(("Value", "Monthly depreciation", "Monthly power", "Monthly tooling", "Monthly maintenance", "Monthly operator wages", "Monthly overhead share", "Available hours", "Monthly wage bill", "Supervisor share", "Worked hours", "Monthly overhead", "Band L", "Band M", "Band H"), _decimal),
        validate=lambda row: _validate_t4(row),
    ),
    "T5": TemplateSpec(
        code="T5",
        name="People",
        noun="people",
        required=("Name", "Kind"),
        optional=("Role tag", "WhatsApp number", "Escalation level", "Employee code"),
        coercions={"Escalation level": _int},
    ),
    "T6": TemplateSpec(
        code="T6",
        name="Reason & Defect Codes",
        noun="codes",
        required=("Kind", "Code", "Label"),
        # The extra column ARCHITECTURE.md §2.3 argued for: R/S alone cannot
        # drive both the money report and Availability.
        optional=(
            "Recoverability",
            "OEE treatment",
            "Is breakdown",
            "Escalation route",
            "Safety flag",
            "Part family",
            "Label (Tamil)",
        ),
        coercions={"Is breakdown": _yesno, "Safety flag": _yesno},
    ),
    "T7": TemplateSpec(
        code="T7",
        name="Shifts & Targets",
        noun="shifts and targets",
        required=("Kind", "Name"),
        optional=(
            "Start time",
            "End time",
            "Break minutes",
            "Weekly off",
            "Line",
            "Shift",
            "Target parts",
        ),
        coercions={"Break minutes": _int, "Target parts": _int},
    ),
}


def _validate_t1(row: dict) -> list[tuple[str, str]]:
    factor = row.get("Bottleneck factor")
    if factor is not None and not (Decimal(0) <= factor <= Decimal(1)):
        return [("Bottleneck factor", "Bottleneck factor must be between 0 and 1")]
    return []


T4_SCOPES = {"cost_family", "section", "plant", "margin_band"}
MACHINE_INPUTS = ("Monthly depreciation", "Monthly power", "Monthly tooling", "Monthly maintenance")
BLENDED_INPUTS = ("Monthly operator wages", "Monthly overhead share")


def _validate_t4(row: dict) -> list[tuple[str, str]]:
    scope = str(row.get("Scope") or "").strip().lower()
    if scope not in T4_SCOPES:
        return [("Scope", "Scope must be cost_family, section, plant or margin_band")]
    direct = row.get("Value") is not None
    if direct and not row.get("Rate type") and scope != "margin_band":
        return [("Rate type", "Give the Rate type (machine, labour or overhead) for a Value")]
    for column, value in row.items():
        if isinstance(value, Decimal) and value < 0:
            return [(column, f"{column} cannot be negative")]

    if scope == "margin_band":
        if not any(row.get(band) is not None for band in ("Band L", "Band M", "Band H")):
            return [("Band M", "A margin band row needs at least one of Band L, Band M, Band H")]
        return []
    if direct:
        return []
    if scope == "cost_family":
        if not any(row.get(column) is not None for column in MACHINE_INPUTS + BLENDED_INPUTS):
            return [("Value", "Give a machine rate Value, or the monthly figures to derive one")]
        if not row.get("Available hours"):
            return [("Available hours", "Available hours are needed to turn monthly costs into a rate")]
    if scope == "section" and not (row.get("Monthly wage bill") and row.get("Worked hours")):
        return [("Monthly wage bill", "Give a labour rate Value, or Monthly wage bill and Worked hours")]
    if scope == "plant" and not (row.get("Monthly overhead") and row.get("Available hours")):
        return [("Monthly overhead", "Give an overhead rate Value, or Monthly overhead and Available hours")]
    return []


def parse(template_code: str, content: bytes) -> ParseResult:
    """Parse one uploaded workbook against its template spec."""
    spec = TEMPLATES.get(template_code.upper())
    if spec is None:
        raise ValueError(f"Unknown template {template_code!r}")

    workbook = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    sheet = workbook.active

    header_cells = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    headers = [str(cell).strip() if cell is not None else "" for cell in header_cells]

    result = ParseResult(template=spec.code, columns=headers, rows=[])

    missing = [column for column in spec.required if column not in headers]
    if missing:
        result.errors.append(
            ParseError(
                row=1,
                column=", ".join(missing),
                message=(
                    f"{spec.name} needs these columns: {', '.join(missing)}. "
                    f"Expected header row: {', '.join(spec.columns)}"
                ),
            )
        )
        return result

    index = {name: position for position, name in enumerate(headers)}

    for number, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if values is None or all(cell in (None, "") for cell in values):
            continue

        row: dict[str, Any] = {}
        row_errors: list[ParseError] = []

        for column in spec.columns:
            if column not in index:
                continue
            position = index[column]
            raw = values[position] if position < len(values) else None
            coerce = spec.coercions.get(column, _text)
            try:
                row[column] = coerce(raw)
            except ValueError as exc:
                row_errors.append(ParseError(number, column, str(exc)))

        for column in spec.required:
            if row.get(column) in (None, ""):
                row_errors.append(ParseError(number, column, f"{column} is required"))
        if not row_errors and spec.validate is not None:
            row_errors.extend(
                ParseError(number, column, message) for column, message in spec.validate(row)
            )

        if row_errors:
            result.errors.extend(row_errors)
        else:
            result.rows.append(row)

    return result


def build_blank(template_code: str) -> bytes:
    """Generate the empty template a plant fills in.

    Shipping the exact file removes the commonest onboarding failure: a plant
    sending us their own spreadsheet layout and someone spending a day
    reshaping it.
    """
    spec = TEMPLATES[template_code.upper()]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = spec.code

    sheet.append(list(spec.columns))
    for cell in sheet[1]:
        cell.font = cell.font.copy(bold=True)
    for position, column in enumerate(spec.columns, start=1):
        sheet.column_dimensions[
            sheet.cell(row=1, column=position).column_letter
        ].width = max(16, len(column) + 4)

    for example in EXAMPLES.get(spec.code, []):
        sheet.append([example.get(column, "") for column in spec.columns])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# One example row per template, taken from §15's own worked examples so a plant
# can see exactly what is wanted.
EXAMPLES: dict[str, list[dict]] = {
    "T1": [
        {
            "Station ID": "CNC-3",
            "Station name": "CNC Turning 3",
            "Type / family": "CNC turning",
            "Section": "Machining",
            "Bottleneck?": "Y",
            "Bottleneck factor": 1.0,
            "Cost family": "CNC-family",
        }
    ],
    "T2": [
        {
            "Part number": "AK-447",
            "Description": "Bracket Assembly LH",
            "Part family": "Brackets",
            "Routing": "Op10 CNC-family > Op20 Assembly-L1 > Op30 QC > Op40 Dispatch",
            "Contribution": 38,
        }
    ],
    "T3": [
        {
            "Part number": "AK-447",
            "Operation": "Op10",
            "Station family": "CNC-family",
            "Cycle time (min/part)": 2.4,
            "Source": "route card",
        }
    ],
    "T4": [
        {
            "Scope": "cost_family",
            "Reference": "CNC-family",
            "Rate type": "machine",
            "Value": 420,
            "Source": "derived",
        },
        {
            "Scope": "cost_family",
            "Reference": "VMC-family",
            "Monthly depreciation": 29000,
            "Monthly power": 18000,
            "Monthly tooling": 6000,
            "Monthly maintenance": 10000,
            "Available hours": 400,
            "Source": "derived",
        },
        {
            "Scope": "section",
            "Reference": "machining",
            "Monthly wage bill": 250000,
            "Supervisor share": 38000,
            "Worked hours": 1600,
        },
        {"Scope": "plant", "Reference": "plant", "Monthly overhead": 420000, "Available hours": 2000},
        {"Scope": "margin_band", "Reference": "Brackets", "Band L": 15, "Band M": 35, "Band H": 60},
    ],
    "T5": [
        {
            "Name": "Suresh K.",
            "Kind": "alert_recipient",
            "Role tag": "maintenance",
            "WhatsApp number": "+919000000001",
            "Escalation level": 2,
        }
    ],
    "T6": [
        {
            "Kind": "downtime",
            "Code": "TOOL_BREAKAGE",
            "Label": "Tool Breakage",
            "Recoverability": "R",
            "OEE treatment": "unplanned",
            "Escalation route": "maintenance",
        }
    ],
    "T7": [
        {
            "Kind": "shift",
            "Name": "Shift A",
            "Start time": "07:00",
            "End time": "15:30",
            "Break minutes": 45,
            "Weekly off": "Sun",
        },
        {"Kind": "target", "Name": "Line 2", "Line": "Line 2", "Shift": "Shift A", "Target parts": 380},
    ],
}
