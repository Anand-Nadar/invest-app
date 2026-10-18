"""Date ranges. India's financial year runs 1 April to 31 March (FY 2026-27 = Apr 2026 - Mar 2027)."""
import calendar
from datetime import date


def month_bounds(d: date) -> tuple[date, date]:
    return d.replace(day=1), d.replace(day=calendar.monthrange(d.year, d.month)[1])


def shift_month(d: date, n: int) -> date:
    """First day of the month that is `n` months from `d`'s month."""
    y, m = divmod(d.year * 12 + (d.month - 1) + n, 12)
    return date(y, m + 1, 1)


def fy_year_of(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def fy_bounds(fy: int) -> tuple[date, date]:
    return date(fy, 4, 1), date(fy + 1, 3, 31)


def fy_label(fy: int) -> str:
    return f"FY {fy}-{str(fy + 1)[-2:]}"


PRESETS = [
    ("this_month", "This month"),
    ("last_month", "Last month"),
    ("this_fy", "This financial year"),
    ("last_fy", "Last financial year"),
    ("all", "All time"),
    ("custom", "Custom dates"),
]


def resolve_range(preset: str, today: date, start: date | None = None, end: date | None = None):
    """Return (start, end, preset). None bounds mean open-ended."""
    if preset == "last_month":
        return (*month_bounds(shift_month(today, -1)), preset)
    if preset == "this_fy":
        return (*fy_bounds(fy_year_of(today)), preset)
    if preset == "last_fy":
        return (*fy_bounds(fy_year_of(today) - 1), preset)
    if preset == "all":
        return None, None, preset
    if preset == "custom":
        return start, end, preset
    return (*month_bounds(today), "this_month")
