"""An in-memory row store that validates against the real schema.

Rows are plain dicts keyed by column name, created through :meth:`Store.add`,
which checks every key against ``app.models`` metadata and fills model-level
defaults. Rows stay mutable until export, because most of the product's
records change after they are born (an Action moves through PDCA, a shift
locks, a job order completes).
"""

from __future__ import annotations

import random
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import Table

from app.models import Base

IGNORED_DEFAULT_CALLABLES = {"uuid4"}


class Store:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.tables: dict[str, Table] = {t.name: t for t in Base.metadata.sorted_tables}
        self.order: list[str] = [t.name for t in Base.metadata.sorted_tables]
        self.rows: dict[str, list[dict]] = {name: [] for name in self.tables}

    # -- identity -----------------------------------------------------------

    def uuid(self) -> uuid.UUID:
        """Deterministic v4 UUIDs, so a regenerated dataset is byte-identical."""
        return uuid.UUID(int=self.rng.getrandbits(128), version=4)

    # -- writes -------------------------------------------------------------

    def add(self, table: str, *, at: datetime, **values) -> dict:
        """Create one row. ``at`` sets created_at/updated_at (when the table has them)."""
        spec = self.tables[table]
        columns = spec.columns

        unknown = set(values) - set(columns.keys())
        if unknown:
            raise KeyError(f"{table}: unknown column(s) {sorted(unknown)}")

        row: dict = {}
        for column in columns:
            name = column.name
            if name in values:
                row[name] = values[name]
            elif name == "id":
                row[name] = self.uuid()
            elif name in ("created_at", "updated_at"):
                row[name] = at
            elif column.default is not None and column.default.is_scalar:
                row[name] = column.default.arg
            else:
                row[name] = None
        self.rows[table].append(row)
        return row

    # -- validation ---------------------------------------------------------

    def validate(self) -> None:
        """NOT NULL, type sanity and per-table uniqueness, before anything is written."""
        for name, rows in self.rows.items():
            table = self.tables[name]
            required = [
                c.name for c in table.columns if not c.nullable and c.server_default is None
            ]
            ids: set = set()
            for row in rows:
                for column in required:
                    if row[column] is None:
                        raise ValueError(f"{name}.{column} is NULL in row {row.get('id')}")
                if "id" in row:
                    if row["id"] in ids:
                        raise ValueError(f"{name}: duplicate id {row['id']}")
                    ids.add(row["id"])
                for key, value in row.items():
                    if isinstance(value, float):
                        raise TypeError(
                            f"{name}.{key} is a float ({value}); use int paise or Decimal"
                        )

            for constraint in table.constraints:
                cols = [c.name for c in getattr(constraint, "columns", [])]
                if constraint.__class__.__name__ != "UniqueConstraint" or not cols:
                    continue
                seen: set = set()
                for row in rows:
                    key = tuple(_plain(row[c]) for c in cols)
                    if any(k is None for k in key):
                        continue
                    if key in seen:
                        raise ValueError(f"{name}: duplicate {cols} = {key}")
                    seen.add(key)

    def counts(self) -> dict[str, int]:
        return {name: len(self.rows[name]) for name in self.order}


def _plain(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (uuid.UUID, date, datetime, Decimal)):
        return str(value)
    return value
