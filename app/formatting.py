"""Display formatting: Indian digit grouping (12,34,567.89) and percentages."""
from decimal import ROUND_HALF_UP, Decimal


def format_inr(value, places: int = 2) -> str:
    if value is None:
        return "-"
    d = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = "-" if d < 0 else ""
    whole, _, frac = f"{abs(d):.{places}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return f"{sign}₹{whole}" + (f".{frac}" if places else "")


def format_pct(value) -> str:
    return "n/a" if value is None else f"{Decimal(str(value)):.1f}%"
