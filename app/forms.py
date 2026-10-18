"""Parsing and cleaning of HTML form input. Every function raises ValidationError with a
message that names the field and says how to fix it."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.services.errors import ValidationError

MAX_AMOUNT = Decimal("10000000000000")  # 1e13


def _a(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def clean_text(value: str | None, label: str, max_len: int = 100, required: bool = False) -> str | None:
    v = (value or "").strip()
    if not v:
        if required:
            raise ValidationError(f"{label} is required.")
        return None
    if len(v) > max_len:
        raise ValidationError(f"{label} must be {max_len} characters or fewer.")
    return v


def parse_date(value: str | None, label: str) -> date:
    v = (value or "").strip()
    if not v:
        raise ValidationError(f"{label} is required.")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ValidationError(f"{label} must be a valid date (YYYY-MM-DD).") from None


def parse_money(
    value: str | None, label: str, *, allow_negative: bool = False, allow_zero: bool = False
) -> Decimal:
    raw = (value or "").strip()
    cleaned = raw.replace(",", "").replace("₹", "").replace(" ", "")
    if not cleaned:
        raise ValidationError(f"{label} is required.")
    try:
        d = Decimal(cleaned)
    except InvalidOperation:
        raise ValidationError(f"{label} must be a number, for example 1250.50.") from None
    if not d.is_finite():
        raise ValidationError(f"{label} must be a number, for example 1250.50.")
    d = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if abs(d) >= MAX_AMOUNT:
        raise ValidationError(f"{label} is too large.")
    if d < 0 and not allow_negative:
        raise ValidationError(f"{label} cannot be negative.")
    if d == 0 and not allow_zero:
        raise ValidationError(f"{label} must be greater than zero.")
    return d


def parse_rate(value: str | None, label: str) -> Decimal | None:
    raw = (value or "").strip().replace("%", "")
    if not raw:
        return None
    try:
        d = Decimal(raw)
    except InvalidOperation:
        raise ValidationError(f"{label} must be a number, for example 8.5.") from None
    if not d.is_finite() or d < 0 or d > 100:
        raise ValidationError(f"{label} must be between 0 and 100.")
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_choice(value: str | None, label: str, choices) -> str:
    keys = [c[0] if isinstance(c, tuple) else c for c in choices]
    v = (value or "").strip()
    if v not in keys:
        raise ValidationError(f"Choose a valid {label.lower()}.")
    return v


def parse_optional_int(value: str | None, label: str, required: bool = False) -> int | None:
    v = (value or "").strip()
    if not v:
        if required:
            raise ValidationError(f"Choose {_a(label)} {label.lower()}.")
        return None
    try:
        return int(v)
    except ValueError:
        raise ValidationError(f"Choose a valid {label.lower()}.") from None


def parse_quantity(value: str | None, label: str = "Quantity") -> Decimal:
    cleaned = (value or "").strip().replace(",", "")
    if not cleaned:
        raise ValidationError(f"{label} is required.")
    try:
        d = Decimal(cleaned)
    except InvalidOperation:
        raise ValidationError(f"{label} must be a number, for example 25 or 10.523.") from None
    if not d.is_finite() or d <= 0:
        raise ValidationError(f"{label} must be greater than zero.")
    if d >= Decimal("1000000000000"):
        raise ValidationError(f"{label} is too large.")
    return d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def parse_price(value: str | None, label: str = "Price") -> Decimal:
    cleaned = (value or "").strip().replace(",", "").replace("₹", "").replace(" ", "")
    if not cleaned:
        raise ValidationError(f"{label} is required.")
    try:
        d = Decimal(cleaned)
    except InvalidOperation:
        raise ValidationError(f"{label} must be a number, for example 2450.75.") from None
    if not d.is_finite() or d <= 0:
        raise ValidationError(f"{label} must be greater than zero.")
    if d >= MAX_AMOUNT:
        raise ValidationError(f"{label} is too large.")
    return d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def parse_positive_int(value: str | None, label: str) -> int:
    v = (value or "").strip()
    try:
        n = int(v)
    except ValueError:
        raise ValidationError(f"{label} must be a whole number greater than zero.") from None
    if n <= 0 or n > 1_000_000:
        raise ValidationError(f"{label} must be a whole number greater than zero.")
    return n
