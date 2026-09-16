"""Money primitives.

Spec commitment (ARCHITECTURE.md C2): money is *integer paise*, never a float.
`Recovered Profit` is an accounting ledger an owner uses to decide whether to
renew; floating-point drift in a renewal argument is unacceptable.

All monetary values in this codebase are `Paise` (a plain ``int``).
Formatting to ``₹`` happens only at the presentation edge.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

#: Integer paise. 100 paise = ₹1.
Paise = int

Numeric = int | float | Decimal | str

RUPEE = 100
LAKH = 100_000


def D(value: Numeric) -> Decimal:
    """Coerce to Decimal without ever going through binary float."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # str() first: Decimal(0.1) is 0.1000000000000000055511151231257827
        return Decimal(str(value))
    return Decimal(value)


def round_paise(value: Numeric) -> Paise:
    """Round to whole paise, half-up.

    Rounding happens *once*, at the end of each component's calculation
    (ARCHITECTURE.md §2.2). Never round an intermediate.
    """
    return int(D(value).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def floor_int(value: Numeric) -> int:
    """Integer floor. The spec's ``⌊ ⌋`` notation is explicit: round down."""
    return int(D(value).quantize(Decimal(1), rounding=ROUND_DOWN))


def rupees(amount: Numeric) -> Paise:
    """Convenience for fixtures/seed data: ``rupees("420.50") -> 42050``."""
    return round_paise(D(amount) * RUPEE)


def to_rupees(paise: Paise) -> Decimal:
    """Exact rupee value of a paise amount, for serialisation."""
    return (D(paise) / RUPEE).quantize(Decimal("0.01"))


def format_inr(paise: Paise, *, compact: bool = False) -> str:
    """Indian-format a paise amount.

    ``compact`` renders the lakh/crore scale the owner's screens use:
    ``₹1.02L``, ``₹28.4L``, ``₹1.4Cr``.
    """
    negative = paise < 0
    value = abs(D(paise) / RUPEE)

    if compact:
        if value >= 10_000_000:
            body = f"₹{_trim(value / 10_000_000)}Cr"
        elif value >= LAKH:
            body = f"₹{_trim(value / LAKH)}L"
        elif value >= 1000:
            body = f"₹{_group_indian(int(value))}"
        else:
            body = f"₹{int(value)}"
        return f"-{body}" if negative else body

    whole = int(value)
    body = f"₹{_group_indian(whole)}"
    return f"-{body}" if negative else body


def _trim(value: Decimal) -> str:
    """Two decimals, trailing zeros removed: 1.02 -> '1.02', 28.40 -> '28.4'."""
    text = str(value.quantize(Decimal("0.01")))
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _group_indian(value: int) -> str:
    """1234567 -> '12,34,567' (Indian digit grouping)."""
    s = str(value)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail
