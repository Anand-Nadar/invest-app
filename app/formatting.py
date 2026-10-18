"""Display formatting: Indian digit grouping (12,34,567.89), quantities, percentages."""
from decimal import ROUND_HALF_UP, Decimal


def _group_indian(whole: str) -> str:
    if len(whole) <= 3:
        return whole
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def format_inr(value, places: int = 2) -> str:
    if value is None:
        return "-"
    d = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = "-" if d < 0 else ""
    whole, _, frac = f"{abs(d):.{places}f}".partition(".")
    return f"{sign}₹{_group_indian(whole)}" + (f".{frac}" if places else "")


def format_inr_signed(value, places: int = 2) -> str:
    """Like format_inr but with an explicit + for gains."""
    if value is None:
        return "-"
    s = format_inr(value, places)
    return s if s.startswith("-") or Decimal(str(value)) == 0 else "+" + s


def _one_dp(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def format_pct(value) -> str:
    return "n/a" if value is None else f"{_one_dp(value)}%"


def format_pct_signed(value) -> str:
    if value is None:
        return "n/a"
    d = _one_dp(value)
    return f"{d:+}%" if d != 0 else "0.0%"


def format_qty(value) -> str:
    """10.000000 -> 10, 12.345000 -> 12.345, 1234567 -> 12,34,567."""
    if value is None:
        return "-"
    d = Decimal(str(value)).normalize()
    sign = "-" if d < 0 else ""
    whole, _, frac = format(abs(d), "f").partition(".")
    return f"{sign}{_group_indian(whole)}" + (f".{frac}" if frac else "")


def format_price(value) -> str:
    """Unit price: 2 decimals normally, 4 when the value really has them (mutual fund NAVs)."""
    if value is None:
        return "-"
    d = Decimal(str(value))
    return format_inr(d, 2 if d == d.quantize(Decimal("0.01")) else 4)
