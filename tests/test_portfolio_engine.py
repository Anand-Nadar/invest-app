"""Independent checks of the portfolio maths. Expected values are worked out by hand."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.formatting import format_inr_signed, format_pct_signed, format_qty
from app.services.portfolio_engine import (
    BONUS, BUY, DIVIDEND, FEE, SELL, SPLIT, EngineError, Event, allocate, cash_effect, replay, summarise, xirr,
)


def ev(id, day, kind, iid=1, **kw):
    return Event(id=id, date=day, kind=kind, instrument_id=iid, **kw)


def d(m, day, y=2026):
    return date(y, m, day)


# --- the main worked example -------------------------------------------------------------
# Jan 10  buy 10 @ 100, fees 10   -> cost 1,010.00          qty 10
# Feb 10  buy 10 @ 120, fees 10   -> cost 1,200 + 10 = 1,210.00; total cost 2,220.00; qty 20; avg 111.00
# Mar 10  sell 5 @ 150, fees 5    -> proceeds 750 - 5 = 745.00
#                                    cost removed = 2,220 x 5/20 = 555.00; realised = 190.00
#                                    left: qty 15, cost 1,665.00
BASE = [
    ev(1, d(1, 10), BUY, quantity=D("10"), price=D("100"), fees=D("10")),
    ev(2, d(2, 10), BUY, quantity=D("10"), price=D("120"), fees=D("10")),
    ev(3, d(3, 10), SELL, quantity=D("5"), price=D("150"), fees=D("5")),
]


def test_weighted_average_cost_and_realised_pnl():
    r = replay(BASE)
    pos = r.positions[1]
    assert pos.quantity == D("15")
    assert pos.cost_basis == D("1665.00")
    assert pos.avg_cost == D("111.0000")
    assert pos.realized_pnl == D("190.00")
    (sale,) = r.sales
    assert (sale.proceeds, sale.cost, sale.pnl) == (D("745.00"), D("555.00"), D("190.00"))


def test_unrealised_pnl_with_price_and_dividend():
    events = BASE + [ev(4, d(3, 20), DIVIDEND, amount=D("30"))]
    r = replay(events)
    open_rows, closed, t = summarise(r, {1: (D("160"), d(3, 15))}, as_of=d(3, 21))
    (h,) = open_rows
    assert h.market_value == D("2400.00")             # 15 x 160
    assert h.unrealized == D("735.00")                # 2,400 - 1,665
    assert h.unrealized_pct == D("44.14")             # 735 / 1,665
    assert (h.valued_at_cost, h.stale) == (False, False)
    assert t.realized == D("190.00") and t.dividends == D("30.00")
    assert t.total_return == D("955.00")              # 735 + 190 + 30
    assert closed == []


def test_unpriced_holding_is_valued_at_cost_and_flagged():
    open_rows, _, t = summarise(replay(BASE), {}, as_of=d(3, 21))
    h = open_rows[0]
    assert h.valued_at_cost and h.market_value == h.cost_basis == D("1665.00")
    assert h.unrealized == D("0.00") and h.unrealized_pct is None
    assert t.unpriced == 1


def test_stale_price_flagged():
    open_rows, _, t = summarise(replay(BASE), {1: (D("160"), d(3, 1))}, as_of=d(3, 21))
    assert open_rows[0].stale and t.stale == 1


def test_split_keeps_cost_and_divides_average():
    events = BASE + [ev(4, d(4, 1), SPLIT, ratio_new=5, ratio_old=1)]
    pos = replay(events).positions[1]
    assert pos.quantity == D("75")                    # 15 x 5
    assert pos.cost_basis == D("1665.00")
    assert pos.avg_cost == D("22.2000")               # 1,665 / 75


def test_reverse_split():
    events = BASE[:1] + [ev(9, d(2, 1), SPLIT, ratio_new=1, ratio_old=2)]
    pos = replay(events).positions[1]
    assert pos.quantity == D("5") and pos.cost_basis == D("1010.00")


def test_bonus_shares_floor_for_equity_and_cost_unchanged():
    # 7 shares for 700.00; bonus 1 for every 2 held -> floor(3.5) = 3
    events = [ev(1, d(1, 1), BUY, quantity=D("7"), price=D("100")), ev(2, d(2, 1), BONUS, ratio_new=1, ratio_old=2)]
    pos = replay(events).positions[1]
    assert pos.quantity == D("10") and pos.cost_basis == D("700.00") and pos.avg_cost == D("70.0000")


def test_bonus_units_exact_for_mutual_funds():
    events = [ev(1, d(1, 1), BUY, quantity=D("7"), price=D("100"), whole_units=False),
              ev(2, d(2, 1), BONUS, ratio_new=1, ratio_old=2, whole_units=False)]
    assert replay(events).positions[1].quantity == D("10.5")


def test_partial_sales_reconcile_to_the_paisa():
    # 3 units for 1,000.00. Sell 1 -> cost removed 333.33; sell the last 2 -> exactly the remaining 666.67.
    events = [
        ev(1, d(1, 1), BUY, quantity=D("3"), price=D("333.3333"), fees=D("0.01")),   # money(999.9999)=1000.00 + 0.01
        ev(2, d(2, 1), SELL, quantity=D("1"), price=D("400")),
        ev(3, d(3, 1), SELL, quantity=D("2"), price=D("400")),
    ]
    r = replay(events)
    first, second = r.sales
    assert first.cost == D("333.34")                  # 1,000.01 x 1/3 = 333.3367 -> 333.34
    assert second.cost == D("666.67")                 # exactly what remains: 1,000.01 - 333.34
    assert first.cost + second.cost == D("1000.01")   # nothing lost to rounding
    pos = r.positions[1]
    assert pos.quantity == 0 and pos.cost_basis == D("0.00")
    assert pos.realized_pnl == D("199.99")            # (400 + 800) - 1,000.01


def test_closed_position_reported_separately():
    events = BASE[:1] + [ev(2, d(2, 1), SELL, quantity=D("10"), price=D("130"))]
    open_rows, closed, t = summarise(replay(events), {}, as_of=d(3, 1))
    assert open_rows == [] and len(closed) == 1
    assert closed[0].realized_pnl == D("290.00")      # 1,300 - 1,010
    assert t.realized == D("290.00")


def test_same_day_buy_is_processed_before_sell_regardless_of_entry_order():
    events = [ev(1, d(1, 5), SELL, quantity=D("5"), price=D("10")),   # entered first
              ev(2, d(1, 5), BUY, quantity=D("5"), price=D("9"))]
    assert replay(events).positions[1].quantity == 0


def test_same_day_split_applies_before_trades():
    events = [ev(1, d(1, 1), BUY, quantity=D("10"), price=D("100")),
              ev(3, d(2, 1), SELL, quantity=D("50"), price=D("20")),
              ev(2, d(2, 1), SPLIT, ratio_new=5, ratio_old=1)]
    assert replay(events).positions[1].quantity == 0     # 10 x 5 = 50 sold on the ex-date


def test_overselling_is_rejected_with_a_clear_message():
    with pytest.raises(EngineError, match="only 20 held"):
        replay(BASE[:2] + [ev(3, d(3, 1), SELL, quantity=D("25"), price=D("1"))], {1: "ABC"})
    with pytest.raises(EngineError, match="only 0 held"):
        replay([ev(1, d(1, 1), SELL, quantity=D("1"), price=D("1"))])


def test_removing_an_earlier_buy_makes_history_invalid():
    buy = ev(1, d(1, 1), BUY, quantity=D("10"), price=D("10"))
    sell = ev(2, d(2, 1), SELL, quantity=D("8"), price=D("12"))
    replay([buy, sell])                                  # valid
    with pytest.raises(EngineError, match="only 0 held"):
        replay([sell])                                   # as if the buy were deleted


def test_account_fees_are_tracked_but_not_in_cost_basis():
    r = replay(BASE + [ev(9, d(3, 31), FEE, iid=None, amount=D("118.00"))])
    assert r.account_charges == D("118.00")
    assert r.positions[1].cost_basis == D("1665.00")
    assert None not in r.positions


def test_cash_effects():
    assert cash_effect(BUY, D("10"), D("100"), D("10")) == D("-1010.00")
    assert cash_effect(SELL, D("5"), D("150"), D("5")) == D("745.00")
    assert cash_effect(DIVIDEND, amount=D("30")) == D("30")
    assert cash_effect(FEE, amount=D("12.34")) == D("-12.34")
    assert cash_effect(SPLIT) == 0 and cash_effect(BONUS) == 0
    assert cash_effect(BUY, D("3"), D("333.3333"), None) == D("-1000.00")


def test_cash_effects_sum_to_net_cash_flows_in_replay():
    events = BASE + [ev(4, d(3, 20), DIVIDEND, amount=D("30"))]
    cash = sum(cash_effect(e.kind, e.quantity, e.price, e.fees, e.amount) for e in events)
    assert cash == D("-1010") + D("-1210") + D("745") + D("30")


# --- XIRR ------------------------------------------------------------------------------------
def test_xirr_simple_ten_percent_year():
    r = xirr([(date(2025, 1, 1), D("-1000")), (date(2026, 1, 1), D("1100"))])
    assert abs(r - 0.10) < 1e-6


def test_xirr_two_investments():
    # -1000 on day 0, -1000 after 1 year, 2,310 after 2 years -> 5% p.a.: 1000*1.05^2 + 1000*1.05 = 2152.5; use 10%: 1210+1100 = 2310
    flows = [(date(2024, 1, 1), D("-1000")), (date(2025, 1, 1), D("-1000")), (date(2026, 1, 1), D("2310"))]
    r = xirr(flows)
    assert abs(r - 0.10) < 2e-3      # 2024 is a leap year: 366 days, so allow a small tolerance


def test_xirr_not_meaningful_cases():
    assert xirr([]) is None
    assert xirr([(date(2026, 1, 1), D("-100")), (date(2026, 1, 10), D("110"))]) is None      # under 30 days
    assert xirr([(date(2025, 1, 1), D("-100")), (date(2026, 1, 1), D("-50"))]) is None        # no inflow


def test_summarise_reports_xirr_for_a_year_old_investment():
    events = [ev(1, date(2025, 10, 18), BUY, quantity=D("10"), price=D("100"))]    # invest 1,000
    _, _, t = summarise(replay(events), {1: (D("110"), date(2026, 10, 18))}, as_of=date(2026, 10, 18))
    assert t.market_value == D("1100.00") and t.xirr_pct == D("10.0")


# --- allocation & formatting -------------------------------------------------------------------
def test_allocation_percentages():
    rows = allocate([("Stocks", D("600")), ("Cash", D("300")), ("Stocks", D("100")), ("Funds", D("0")), ("ETF", D("-5"))])
    assert [(r["name"], r["value"], r["pct"]) for r in rows] == [
        ("Stocks", D("700"), D("70.0")), ("Cash", D("300"), D("30.0"))]
    assert allocate([]) == []


def test_formatters():
    assert format_qty(D("10.000000")) == "10" and format_qty(D("12.345000")) == "12.345"
    assert format_qty(D("1234567")) == "12,34,567" and format_qty(D("0")) == "0" and format_qty(None) == "-"
    assert format_inr_signed(D("1000")) == "+₹1,000.00" and format_inr_signed(D("-5")) == "-₹5.00"
    assert format_inr_signed(D("0")) == "₹0.00"
    assert format_pct_signed(D("12.34")) == "+12.3%" and format_pct_signed(D("-3.05")) == "-3.1%"
    assert format_pct_signed(None) == "n/a" and format_pct_signed(D("0")) == "0.0%"
