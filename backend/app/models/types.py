"""Custom column types."""

from __future__ import annotations

from enum import Enum
from typing import Any, TypeVar

from sqlalchemy import String, TypeDecorator

E = TypeVar("E", bound=Enum)


class EnumString(TypeDecorator):
    """Store a Python ``str`` Enum as a VARCHAR and read it back as the Enum.

    Plain ``String`` columns round-trip enums as bare strings, which leaves
    every call site writing ``getattr(value, "value", value)`` and makes an
    ``is`` comparison silently false. A native Postgres ENUM would fix the
    round-trip but turns every added reason-code attribute or PDCA state into a
    migration with a type alteration — and these enums change as the product
    grows.

    So: VARCHAR on disk, real Enum in Python, coercion in one place.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[E], length: int = 32, **kwargs: Any) -> None:
        self.enum_class = enum_class
        super().__init__(length=length, **kwargs)

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, self.enum_class):
            return value.value
        # Accept a raw string so fixtures and imports stay ergonomic, but
        # validate it: an unknown value must fail here, not three screens later.
        return self.enum_class(value).value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return self.enum_class(value)
