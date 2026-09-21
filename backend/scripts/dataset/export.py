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


#: Largest data file we will write. GitHub refuses a file over 100 MB outright
#: and warns above 50 MB, and this dataset is meant to live in the repository —
#: so the load is split into parts rather than shrunk.
MAX_PART_BYTES = 40 * 1024 * 1024


def _ident(name: str) -> str:
    """A column or table name, always quoted.

    ``kaizen_ideas.similar`` is the reason this exists: SIMILAR is reserved in
    PostgreSQL, so an unquoted column list is a syntax error. Quoting every
    identifier costs nothing and means a new column can be called whatever the
    model calls it.
    """
    return '"' + name.replace('"', '""') + '"'


def _literal(value, column) -> str:
    text = _plain(value, column)
    if text is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, Decimal)) and not isinstance(column.type, (JSONB, ARRAY)):
        return text
    return "'" + text.replace("'", "''") + "'"


def write_sql(store: Store, path: Path, header: str) -> list[str]:
    r"""Write the data as one transaction, split across numbered part files.

    ``path`` becomes a small entry point that ``\i``-includes each part in
    foreign-key order, so loading is still a single psql command. The parts
    themselves carry no BEGIN/COMMIT: the entry point owns the transaction, and
    a part that is loaded on its own would otherwise commit a half-loaded
    schema.

    Returns the part file names, for the manifest.
    """
    parts_dir = path.parent / "data"
    for stale in parts_dir.glob("*.sql"):
        stale.unlink()
    parts_dir.mkdir(parents=True, exist_ok=True)

    parts: list[str] = []
    handle = None
    written = 0

    def close() -> None:
        nonlocal handle
        if handle is not None:
            handle.close()
            handle = None

    def part_for(size: int):
        """The open part file with room for ``size`` more bytes."""
        nonlocal handle, written
        if handle is not None and written + size > MAX_PART_BYTES:
            close()
        if handle is None:
            name = f"{len(parts):03d}_data.sql"
            parts.append(name)
            handle = (parts_dir / name).open("w", encoding="utf-8")
            handle.write(
                f"-- FactoryManager AI dataset, part {len(parts)}.\n"
                "-- Included by ../factorymanager_data.sql, which owns the transaction.\n"
            )
            written = 0
        return handle

    for name in store.order:
        rows = store.rows[name]
        if not rows:
            continue
        columns = list(store.tables[name].columns)
        names = ", ".join(_ident(c.name) for c in columns)
        for start in range(0, len(rows), BATCH):
            chunk = rows[start : start + BATCH]
            body = ",\n".join(
                "(" + ", ".join(_literal(row[c.name], c) for c in columns) + ")"
                for row in chunk
            )
            statement = (
                f"\n-- {name}: rows {start + 1}-{start + len(chunk)} of {len(rows)}\n"
                f"INSERT INTO {_ident(name)} ({names}) VALUES\n{body};\n"
            )
            encoded = len(statement.encode("utf-8"))
            out = part_for(encoded)
            out.write(statement)
            written += encoded
    close()

    includes = "\n".join(f"\\i data/{name}" for name in parts)
    path.write_text(
        header
        + "\n-- The data itself is in data/*.sql, split so that no single file\n"
        "-- exceeds GitHub's file-size limit. Load this file, not the parts:\n"
        "--   psql \"$DATABASE_URL\" -v ON_ERROR_STOP=1 -f factorymanager_data.sql\n"
        "-- psql resolves \\i relative to the file it is reading, so run it from\n"
        "-- anywhere.\n"
        "\nSET client_encoding = 'UTF8';\n"
        "SET standard_conforming_strings = on;\n"
        "SET search_path = public;\n"
        "\nBEGIN;\n"
        f"{includes}\n"
        "\nCOMMIT;\n",
        encoding="utf-8",
    )
    return parts


def write_reset_sql(store: Store, path: Path) -> None:
    tables = ", ".join(_ident(t) for t in reversed(store.order))
    path.write_text(
        "-- DESTRUCTIVE: empties every FactoryManager AI table before loading the dataset.\n"
        "-- Run only against a database you intend to overwrite (not production).\n"
        f"TRUNCATE {tables} RESTART IDENTITY CASCADE;\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def write_csv(store: Store, folder: Path) -> dict[str, list[str]]:
    r"""One CSV per table, plus a ``\copy`` loader.

    A table whose CSV would pass :data:`MAX_PART_BYTES` is split into
    ``<table>.001.csv``, ``<table>.002.csv`` … — same reason as the SQL parts,
    and the loader copies each in turn, so nothing about loading changes.

    Returns {table: [file names]} for the manifest.
    """
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob("*.csv"):
        stale.unlink()

    lines = [
        "-- psql script: run from inside the csv/ folder with",
        '--   psql "$DATABASE_URL" -f load_csv.sql',
        "\\set ON_ERROR_STOP on",
        "BEGIN;",
    ]
    written: dict[str, list[str]] = {}

    for name in store.order:
        columns = list(store.tables[name].columns)
        header = [c.name for c in columns]
        rows = store.rows[name]

        # Measure while writing rather than guessing: one pass, and the split
        # lands on a row boundary because that is the only place it can.
        files: list[str] = []
        handle = writer = None
        size = 0

        def close():
            nonlocal handle
            if handle is not None:
                handle.close()
                handle = None

        for row in rows:
            values = []
            for column in columns:
                text = _plain(row[column.name], column)
                if text == "":
                    raise ValueError(
                        f"{name}.{column.name}: empty string would load as NULL"
                    )
                values.append(text)
            line = io.StringIO()
            csv.writer(line).writerow(values)
            encoded = len(line.getvalue().encode("utf-8"))
            if handle is not None and size + encoded > MAX_PART_BYTES:
                close()
            if handle is None:
                part = f"{name}.csv" if not files else None
                if part is None:
                    # Growing past one file: the first one gets renumbered too,
                    # so the set reads as a set rather than a file plus scraps.
                    if len(files) == 1 and files[0] == f"{name}.csv":
                        (folder / files[0]).rename(folder / f"{name}.001.csv")
                        files[0] = f"{name}.001.csv"
                    part = f"{name}.{len(files) + 1:03d}.csv"
                files.append(part)
                handle = (folder / part).open("w", encoding="utf-8", newline="")
                writer = csv.writer(handle)
                writer.writerow(header)
                size = 0
            handle.write(line.getvalue())
            size += encoded
        close()
        if not files:  # an empty table still gets a header-only file
            files = [f"{name}.csv"]
            with (folder / files[0]).open("w", encoding="utf-8", newline="") as empty:
                csv.writer(empty).writerow(header)

        written[name] = files
        cols = ", ".join(_ident(c) for c in header)
        for part in files:
            lines.append(
                f"\\copy {_ident(name)} ({cols}) FROM '{part}' WITH (FORMAT csv, HEADER true)"
            )

    lines.append("COMMIT;")
    (folder / "load_csv.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return written


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
