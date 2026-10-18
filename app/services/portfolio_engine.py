"""Pure portfolio maths (no database). Everything is replayed from the list of events, so edits and
deletions can be validated by simply replaying the new list.

Cost-basis method: WEIGHTED AVERAGE cost, per instrument, across all accounts.
  * buy      cost basis += quantity x price + fees (fees are part of acquisition cost)
  * sell     cost removed = average cost x quantity sold; realised P&L = (gross - fees) - cost removed
  * split    quantity x new/old, cost basis unchanged (so average cost falls)
  * bonus    + floor(quantity x new/old) free units for shares (exact for mutual funds), cost basis unchanged
  * dividend cash received; tracked separately from realised P&L
  * fee      account-level charge (DP charges, AMC...), cash only, not part of any cost basis

All money values are rounded to paise (2 dp) at each step, so totals always add up exactly.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

ZERO = Decimal("0.00")
QTY_STEP = Decimal("0.000001")

BUY, SELL, DIVIDEND, FEE, SPLIT, BONUS = "buy", "sell", "dividend", "fee", "split", "bonus"
KINDS = (BUY, SELL, DIVIDEND, FEE, SPLIT, BONUS)
# Same-day ordering: corporate actions first (prices on the ex-date are post-action), then buys before sells.
_ORDER = {SPLIT: 0, BONUS: 0, BUY: 1, SELL: 2, DIVIDEND: 3, FEE: 4}


class EngineError(ValueError):
    """The event history is impossible (e.g. selling units that are not held)."""


def money(x) -> Decimal:
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fmt_qty(q: Decimal) -> str:
    return format(q.normalize(), "f")


@dataclass(frozen=True)
class Event:
    id: int
    date: date
    kind: str
    instrument_id: int | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    fees: Decimal = ZERO
    amount: Decimal | None = None
    ratio_new: int | None = None
    ratio_old: int | None = None
    whole_units: bool = True  # shares/ETFs trade in whole units; mutual funds do not


def cash_effect(kind, quantity=None, price=None, fees=None, amount=None) -> Decimal:
    """Effect of an event on the cash balance of its account."""
    fees = Decimal(fees) if fees else ZERO
    if kind == BUY:
        return -(money(Decimal(quantity) * Decimal(price)) + fees)
    if kind == SELL:
        return money(Decimal(quantity) * Decimal(price)) - fees
    if kind == DIVIDEND:
        return Decimal(amount)
    if kind == FEE:
        return -Decimal(amount)
    return ZERO


@dataclass
class Position:
    instrument_id: int | None
    quantity: Decimal = Decimal("0")
    cost_basis: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    dividends: Decimal = ZERO
    total_bought: Decimal = ZERO

    @property
    def avg_cost(self) -> Decimal | None:
        if self.quantity <= 0:
            return None
        return (self.cost_basis / self.quantity).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Sale:
    event_id: int
    date: date
    instrument_id: int
    quantity: Decimal
    proceeds: Decimal
    cost: Decimal

    @property
    def pnl(self) -> Decimal:
        return self.proceeds - self.cost


@dataclass(frozen=True)
class Flow:
    date: date
    amount: Decimal  # negative = money put in, positive = money received
    instrument_id: int | None = None


@dataclass
class ReplayResult:
    positions: dict[int, Position] = field(default_factory=dict)
    sales: list[Sale] = field(default_factory=list)
    flows: list[Flow] = field(default_factory=list)
    account_charges: Decimal = ZERO


def sort_key(e: Event):
    return (e.date, _ORDER[e.kind], e.id)


def replay(events, labels: dict[int, str] | None = None) -> ReplayResult:
    labels = labels or {}
    res = ReplayResult()
    for e in sorted(events, key=sort_key):
        if e.kind == FEE:
            res.account_charges += e.amount
            continue
        pos = res.positions.setdefault(e.instrument_id, Position(e.instrument_id))
        name = labels.get(e.instrument_id, f"instrument {e.instrument_id}")

        if e.kind == BUY:
            cost = money(e.quantity * e.price) + e.fees
            pos.quantity += e.quantity
            pos.cost_basis += cost
            pos.total_bought += cost
            res.flows.append(Flow(e.date, -cost, e.instrument_id))

        elif e.kind == SELL:
            if e.quantity > pos.quantity:
                raise EngineError(
                    f"Can't sell {_fmt_qty(e.quantity)} of {name} on {e.date:%d %b %Y}: "
                    f"only {_fmt_qty(pos.quantity)} held at that point."
                )
            proceeds = money(e.quantity * e.price) - e.fees
            if e.quantity == pos.quantity:
                cost_removed = pos.cost_basis
            else:
                cost_removed = money(pos.cost_basis * e.quantity / pos.quantity)
            pos.quantity -= e.quantity
            pos.cost_basis -= cost_removed
            pos.realized_pnl += proceeds - cost_removed
            res.sales.append(Sale(e.id, e.date, e.instrument_id, e.quantity, proceeds, cost_removed))
            res.flows.append(Flow(e.date, proceeds, e.instrument_id))

        elif e.kind == DIVIDEND:
            pos.dividends += e.amount
            res.flows.append(Flow(e.date, e.amount, e.instrument_id))

        elif e.kind == SPLIT:
            pos.quantity = (pos.quantity * e.ratio_new / e.ratio_old).quantize(QTY_STEP)

        elif e.kind == BONUS:
            gained = pos.quantity * e.ratio_new / e.ratio_old
            gained = gained.to_integral_value(ROUND_FLOOR) if e.whole_units else gained.quantize(QTY_STEP)
            pos.quantity += gained
    return res


# ------------------------------------------------------------------------------ performance
def xirr(flows, min_days: int = 30) -> float | None:
    """Annualised money-weighted return. None when it is not meaningful (too short, one-sided)."""
    pts = [(d, float(a)) for d, a in flows if a != 0]
    if len(pts) < 2 or not any(a < 0 for _d, a in pts) or not any(a > 0 for _d, a in pts):
        return None
    t0 = min(d for d, _a in pts)
    if (max(d for d, _a in pts) - t0).days < min_days:
        return None

    def npv(r: float) -> float:
        return sum(a / (1 + r) ** ((d - t0).days / 365) for d, a in pts)

    lo, hi = -0.99, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if f_lo * f_mid <= 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


@dataclass
class HoldingRow:
    instrument_id: int
    quantity: Decimal
    avg_cost: Decimal | None
    cost_basis: Decimal
    price: Decimal | None
    price_date: date | None
    market_value: Decimal
    unrealized: Decimal
    unrealized_pct: Decimal | None
    realized_pnl: Decimal
    dividends: Decimal
    valued_at_cost: bool  # no price on file, so market value = cost basis
    stale: bool
    instrument: Any = None  # attached by the service layer


@dataclass
class Totals:
    cost_basis: Decimal = ZERO
    market_value: Decimal = ZERO
    unrealized: Decimal = ZERO
    realized: Decimal = ZERO
    dividends: Decimal = ZERO
    unpriced: int = 0
    stale: int = 0
    xirr_pct: Decimal | None = None

    @property
    def total_return(self) -> Decimal:
        return self.unrealized + self.realized + self.dividends

    @property
    def unrealized_pct(self) -> Decimal | None:
        if self.cost_basis <= 0:
            return None
        return (self.unrealized / self.cost_basis * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def summarise(result: ReplayResult, prices: dict, as_of: date, stale_days: int = 7):
    """prices: {instrument_id: (price, price_date)}. Returns (open_rows, closed_rows, totals)."""
    open_rows, closed_rows, t = [], [], Totals()
    for iid, pos in result.positions.items():
        t.realized += pos.realized_pnl
        t.dividends += pos.dividends
        if pos.quantity <= 0:
            if pos.realized_pnl or pos.dividends or pos.total_bought:
                closed_rows.append(HoldingRow(iid, Decimal("0"), None, ZERO, None, None, ZERO, ZERO, None,
                                              pos.realized_pnl, pos.dividends, False, False))
            continue
        price, price_date = prices.get(iid, (None, None))
        if price is None:
            mv, at_cost, stale = pos.cost_basis, True, False
            t.unpriced += 1
        else:
            mv, at_cost = money(pos.quantity * Decimal(price)), False
            stale = (as_of - price_date).days > stale_days
            t.stale += 1 if stale else 0
        unreal = mv - pos.cost_basis
        pct = (unreal / pos.cost_basis * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) \
            if pos.cost_basis > 0 and not at_cost else None
        open_rows.append(HoldingRow(iid, pos.quantity, pos.avg_cost, pos.cost_basis, price, price_date, mv,
                                    unreal, pct, pos.realized_pnl, pos.dividends, at_cost, stale))
        t.cost_basis += pos.cost_basis
        t.market_value += mv
        t.unrealized += unreal
    open_rows.sort(key=lambda r: r.market_value, reverse=True)
    flows = [(f.date, f.amount) for f in result.flows]
    if t.market_value > 0:
        flows.append((as_of, t.market_value))
    r = xirr(flows)
    t.xirr_pct = None if r is None else Decimal(str(round(r * 100, 1)))
    return open_rows, closed_rows, t


def allocate(items) -> list[dict]:
    """items: [(label, value)] -> [{'name', 'value', 'pct'}] largest first, zero values dropped."""
    totals: dict[str, Decimal] = {}
    for label, value in items:
        if value and value > 0:
            totals[label] = totals.get(label, ZERO) + value
    grand = sum(totals.values(), ZERO)
    return [
        {"name": k, "value": v, "pct": (v / grand * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)}
        for k, v in sorted(totals.items(), key=lambda kv: -kv[1])
    ]
