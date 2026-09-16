"""Write the store out as PostgreSQL, CSV, Excel and the onboarding forms."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.ingest import readiness
from app.ingest.templates import TEMPLATES, parse
from app.models import Base

from .store import Store

BATCH = 1000
EXCEL_CELL_LIMIT = 32_767


def _descriptions() -> dict[str, str]:
    out = {}
    for mapper in Base.registry.mappers:
        doc = (mapper.class_.__doc__ or "").strip().splitlines()
        out[mapper.local_table.name] = doc[0].strip() if doc else ""
    return out


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _array_literal(values) -> str:
    items = []
    for item in values:
        text = str(item).replace("\\", "\\\\").replace('"', '\\"')
        items.append(f'"{text}"')
    return "{" + ",".join(items) + "}"


def _plain(value, column):
    """A Python value as the text Postgres should receive, or None for NULL."""
    if value is None:
        return None
    if isinstance(column.type, JSONB):
        return _json(value)
    if isinstance(column.type, ARRAY):
        return _array_literal(value)
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------


def write_sql(store: Store, path: Path, header: str) -> None:
    def literal(value, column) -> str:
        text = _plain(value, column)
        if text is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, Decimal)) and not isinstance(
            column.type, (JSONB, ARRAY)
        ):
            return text
        return "'" + text.replace("'", "''") + "'"

    with path.open("w", encoding="utf-8") as out:
        out.write(header)
        out.write(
            "\nSET client_encoding = 'UTF8';\nSET standard_conforming_strings = on;\nSET search_path = public;\n\nBEGIN;\n"
        )
        for name in store.order:
            rows = store.rows[name]
            if not rows:
                continue
            columns = list(store.tables[name].columns)
            names = ", ".join(c.name for c in columns)
            out.write(f"\n-- {name}: {len(rows)} rows\n")
            for start in range(0, len(rows), BATCH):
                chunk = rows[start : start + BATCH]
                out.write(f"INSERT INTO {name} ({names}) VALUES\n")
                out.write(
                    ",\n".join(
                        "(" + ", ".join(literal(row[c.name], c) for c in columns) + ")"
                        for row in chunk
                    )
                )
                out.write(";\n")
        out.write("\nCOMMIT;\n")


def write_reset_sql(store: Store, path: Path) -> None:
    tables = ", ".join(reversed(store.order))
    path.write_text(
        "-- DESTRUCTIVE: empties every FactoryManager AI table before loading the dataset.\n"
        "-- Run only against a database you intend to overwrite (not production).\n"
        f"TRUNCATE {tables} RESTART IDENTITY CASCADE;\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def write_csv(store: Store, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        "-- psql script: run from inside the csv/ folder with",
        '--   psql "$DATABASE_URL" -f load_csv.sql',
        "\\set ON_ERROR_STOP on",
        "BEGIN;",
    ]
    for name in store.order:
        columns = list(store.tables[name].columns)
        with (folder / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([c.name for c in columns])
            for row in store.rows[name]:
                values = []
                for column in columns:
                    text = _plain(row[column.name], column)
                    if text == "":
                        raise ValueError(
                            f"{name}.{column.name}: empty string would load as NULL"
                        )
                    values.append(text)
                writer.writerow(values)
        cols = ", ".join(c.name for c in columns)
        lines.append(
            f"\\copy {name} ({cols}) FROM '{name}.csv' WITH (FORMAT csv, HEADER true)"
        )
    lines.append("COMMIT;")
    (folder / "load_csv.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------


def _excel_value(value, column):
    if value is None:
        return None
    if isinstance(column.type, JSONB):
        text = _json(value)
        return (
            text
            if len(text) <= EXCEL_CELL_LIMIT
            else text[: EXCEL_CELL_LIMIT - 20] + "…[truncated]"
        )
    if isinstance(column.type, ARRAY):
        return _array_literal(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)  # Excel has no time zones; every timestamp is IST
    if isinstance(value, Decimal):
        return float(value)
    return value


def write_excel(store: Store, path: Path, summary_rows: list[list]) -> None:
    workbook = Workbook(write_only=True)
    bold = Font(bold=True)
    descriptions = _descriptions()

    readme = workbook.create_sheet("README")
    for line in summary_rows:
        readme.append(line)
    readme.append([])
    header = []
    for text in ("Sheet / table", "Rows", "What it is"):
        cell = WriteOnlyCell(readme, value=text)
        cell.font = bold
        header.append(cell)
    readme.append(header)
    for name in store.order:
        readme.append([name, len(store.rows[name]), descriptions.get(name, "")])

    for name in store.order:
        columns = list(store.tables[name].columns)
        sheet = workbook.create_sheet(name[:31])
        sheet.freeze_panes = "A2"
        for index, column in enumerate(columns):
            letter = _letter(index)
            sheet.column_dimensions[letter].width = (
                38
                if column.name == "id" or column.name.endswith("_id")
                else max(12, min(48, len(column.name) + 6))
            )
        header = []
        for column in columns:
            cell = WriteOnlyCell(sheet, value=column.name)
            cell.font = bold
            header.append(cell)
        sheet.append(header)
        for row in store.rows[name]:
            values = []
            for column in columns:
                value = _excel_value(row[column.name], column)
                if isinstance(value, datetime):
                    cell = WriteOnlyCell(sheet, value=value)
                    cell.number_format = "yyyy-mm-dd hh:mm:ss"
                    values.append(cell)
                else:
                    values.append(value)
            sheet.append(values)
    workbook.save(path)


def _letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


# ---------------------------------------------------------------------------
# Onboarding forms (T1-T7) and the readiness questionnaire
# ---------------------------------------------------------------------------


def write_forms(
    forms: dict[str, dict[str, list[dict]]], store: Store, folder: Path
) -> dict[str, dict[str, int]]:
    """Filled templates in the exact layout ``app.ingest.templates`` parses.

    Each file is round-tripped through the product's own strict parser; a form
    the onboarding screen would reject fails generation instead.
    """
    report: dict[str, dict[str, int]] = {}
    bold = Font(bold=True)
    for plant, templates in forms.items():
        target = folder / plant
        target.mkdir(parents=True, exist_ok=True)
        report[plant] = {}
        for code, rows in templates.items():
            spec = TEMPLATES[code]
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = code
            sheet.append(list(spec.columns))
            for cell in sheet[1]:
                cell.font = bold
            for index, column in enumerate(spec.columns, start=1):
                sheet.column_dimensions[
                    sheet.cell(row=1, column=index).column_letter
                ].width = max(16, len(column) + 4)
            for row in rows:
                sheet.append([row.get(column, "") for column in spec.columns])
            buffer = io.BytesIO()
            workbook.save(buffer)
            content = buffer.getvalue()
            result = parse(code, content)
            if not result.ok or len(result.rows) != len(rows):
                raise ValueError(
                    f"{plant} {code} does not parse cleanly: {[e.as_dict() for e in result.errors][:3]}"
                )
            filename = f"{code}_{spec.name.replace(' & ', '_').replace(' ', '_')}.xlsx"
            (target / filename).write_bytes(content)
            report[plant][code] = len(result.rows)

    workbook = Workbook()
    questions = workbook.active
    questions.title = "Questions"
    questions.append(["Key", "Section", "Question", "Weight", "Gap shown when answered No"])
    for q in readiness.QUESTIONS:
        questions.append([q.key, q.section, q.prompt, q.weight, q.gap_if_missing or ""])
    responses = workbook.create_sheet("Responses")
    keys = [q.key for q in readiness.QUESTIONS]
    responses.append(
        [
            "Plant / prospect",
            "Respondent",
            "Phone",
            "Completed at (IST)",
            "Score",
            "Band",
            "Onboarding hours",
            *keys,
        ]
    )
    for row in store.rows["readiness_assessments"]:
        answers = row["answers"] or {}
        responses.append(
            [
                row["plant_name"],
                row["respondent_name"],
                row["respondent_phone"],
                row["completed_at"].replace(tzinfo=None) if row["completed_at"] else None,
                row["score"] if row["completed_at"] else None,
                row["band"],
                row["estimated_onboarding_hours"],
                *[
                    (
                        "Yes"
                        if str(answers[k]).lower() in ("yes", "true", "1", "y")
                        else "No"
                    )
                    if k in answers
                    else ""
                    for k in keys
                ],
            ]
        )
    for sheet in (questions, responses):
        for cell in sheet[1]:
            cell.font = bold
    workbook.save(folder / "Readiness_Questionnaire_Responses.xlsx")
    return report
