"""Declarative base and the mixins every table shares."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    """All models inherit from here.

    ``Paise`` columns are ``BigInteger`` throughout — never ``Numeric``, never
    ``Float``. Money is integer paise from the database to the presentation edge
    (ARCHITECTURE.md C2).
    """

    type_annotation_map = {int: BigInteger}


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PlantScopedMixin:
    """Every record is plant-scoped from day one.

    §9: "keep every record plant-scoped from day one so a 100-plant rollup is a
    screen later, not a migration."
    """

    @declared_attr
    def plant_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            PGUUID(as_uuid=True),
            ForeignKey("plants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )

    @declared_attr.directive
    def __table_args__(cls):
        return (Index(f"ix_{cls.__tablename__}_plant", "plant_id"),)
