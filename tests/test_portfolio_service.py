"""Database-level portfolio tests. Numbers match the worked example in test_portfolio_engine.py:

  buy 10 @ 100 + 10 fees (1,010) ; buy 10 @ 120 + 10 fees (1,210) ; sell 5 @ 150 - 5 fees (745 in)
  -> 15 units, cost basis 1,665.00, average 111.00, realised +190.00
"""
from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select

from app.models.portfolio import Price
from app.services import finance as fsvc
from app.services import portfolio as svc
from app.services.errors import ValidationError

AS_OF = date(2026, 3, 21)


@pytest.fixture()
def world(db):
    bank = fsvc.create_account(db, name="HDFC", type="bank", opening_balance=D("100000"), opening_date=date(2025, 4, 1))
    broker = fsvc.create_account(db, name="Zerodha", type="brokerage", opening_balance=D("0"), opening_date=date(2025, 4, 1))
    abc = svc.save_instrument(db, None, symbol="abc", exchange="NSE", name="ABC Ltd", type="equity", sector="IT")
    fund = svc.save_instrument(db, None, symbol="FUNDX", exchange="MF", name="Fund X", type="mutual_fund")
    fsvc.create_transaction(db, kind="transfer", tx_date=date(2026, 1, 5), amount=D("50000"),
                            account_id=bank.id, to_account_id=broker.id)

    def tx(kind, day, **kw):
        return svc.save_investment_tx(db, None, kind=kind, tx_date=day, **kw)

    tx("buy", date(2026, 1, 10), instrument_id=abc.id, account_id=broker.id, quantity=D("10"), price=D("100"), fees=D("10"))
    tx("buy", date(2026, 2, 10), instrument_id=abc.id, account_id=broker.id, quantity=D("10"), price=D("120"), fees=D("10"))
    sell = tx("sell", date(2026, 3, 10), instrument_id=abc.id, account_id=broker.id, quantity=D("5"), price=D("150"), fees=D("5"))
    div = tx("dividend", date(2026, 3, 20), instrument_id=abc.id, account_id=broker.id, amount=D("30"))
    fee = tx("fee", date(2026, 3, 31), account_id=broker.id, amount=D("118"))
    svc.set_prices(db, date(2026, 3, 15), {abc.id: D("160")})
    return dict(bank=bank, broker=broker, abc=abc, fund=fund, sell=sell, div=div, fee=fee)


def test_holdings_and_performance(db, world):
    pf = svc.build_portfolio(db, as_of=AS_OF)
    (h,) = pf.holdings
    assert h.instrument.symbol == "ABC"                      # symbols are stored upper-case
    assert (h.quantity, h.cost_basis, h.avg_cost) == (D("15"), D("1665.00"), D("111.0000"))
    assert h.market_value == D("2400.00") and h.unrealized == D("735.00")
    t = pf.totals
    assert (t.realized, t.dividends, t.total_return) == (D("190.00"), D("30.00"), D("955.00"))
    assert pf.account_charges == D("118.00")


def test_cash_reconciles_with_account_balances(db, world):
    bal = fsvc.account_balances(db)
    assert bal[world["bank"].id] == D("50000.00")                     # 100,000 - 50,000 transferred out
    # 50,000 in - 1,010 - 1,210 + 745 (sell) + 30 (dividend) - 118 (account fee)
    assert bal[world["broker"].id] == D("48437.00")


def test_investment_cash_flows_do_not_pollute_income_and_expenses(db, world):
    s = fsvc.period_summary(db, date(2026, 1, 1), date(2026, 3, 31))
    assert s.income == 0 and s.expenses == 0


def test_net_worth_includes_holdings_and_snapshot_records_them(db, world):
    nw = fsvc.net_worth(db)
    assert nw.accounts_total == D("98437.00")           # 50,000 + 48,437
    assert nw.investments_value == D("2400.00")         # 15 x 160
    assert nw.net_worth == D("100837.00")
    snap = fsvc.record_snapshot(db, date(2026, 3, 21))
    assert snap.investments_value == D("2400.00") and snap.net_worth == D("100837.00")


def test_unpriced_holdings_count_at_cost_in_net_worth(db, world):
    db.query(Price).delete()
    db.commit()
    assert fsvc.net_worth(db).investments_value == D("1665.00")


def test_edit_recalculates_everything(db, world):
    first_buy = svc.list_investment_txs(db, svc.InvFilter(kind="buy"))[-1]
    svc.save_investment_tx(db, first_buy, kind="buy", tx_date=first_buy.date, instrument_id=world["abc"].id,
                           account_id=world["broker"].id, quantity=D("10"), price=D("110"), fees=D("10"))
    pf = svc.build_portfolio(db, as_of=AS_OF)
    # cost 1,110 + 1,210 = 2,320; sell 5 of 20 removes 580; realised 745 - 580 = 165; left 1,740
    assert pf.totals.realized == D("165.00") and pf.holdings[0].cost_basis == D("1740.00")
    assert fsvc.account_balances(db)[world["broker"].id] == D("48337.00")   # 100 more spent on the first buy


def test_overselling_is_blocked_and_nothing_is_saved(db, world):
    before = len(svc.list_investment_txs(db))
    with pytest.raises(ValidationError, match="only 15 held"):
        svc.save_investment_tx(db, None, kind="sell", tx_date=date(2026, 4, 1), instrument_id=world["abc"].id,
                               account_id=world["broker"].id, quantity=D("16"), price=D("160"))
    assert len(svc.list_investment_txs(db)) == before
    assert fsvc.account_balances(db)[world["broker"].id] == D("48437.00")


def test_delete_rules(db, world):
    buys = svc.list_investment_txs(db, svc.InvFilter(kind="buy"))          # newest first
    svc.delete_investment_tx(db, buys[-1])                                 # first buy: sell is still covered
    assert svc.build_portfolio(db, as_of=AS_OF).holdings[0].quantity == D("5")
    with pytest.raises(ValidationError, match="depend on it"):
        svc.delete_investment_tx(db, svc.list_investment_txs(db, svc.InvFilter(kind="buy"))[0])
    svc.delete_investment_tx(db, world["div"])                              # a dividend can always go
    assert svc.build_portfolio(db, as_of=AS_OF).totals.dividends == 0


def test_split_and_bonus_through_the_service(db, world):
    svc.save_investment_tx(db, None, kind="split", tx_date=date(2026, 4, 1), instrument_id=world["abc"].id,
                           ratio_new=5, ratio_old=1)
    svc.set_prices(db, date(2026, 4, 2), {world["abc"].id: D("32")})
    h = svc.build_portfolio(db, as_of=date(2026, 4, 3)).holdings[0]
    assert (h.quantity, h.cost_basis, h.avg_cost) == (D("75"), D("1665.00"), D("22.2000"))
    assert h.market_value == D("2400.00")                  # 75 x 32: a split changes nothing about value
    svc.save_investment_tx(db, None, kind="bonus", tx_date=date(2026, 5, 1), instrument_id=world["abc"].id,
                           ratio_new=1, ratio_old=2)
    h = svc.build_portfolio(db, as_of=date(2026, 5, 2)).holdings[0]
    assert h.quantity == D("112") and h.cost_basis == D("1665.00")      # + floor(75 / 2) = 37
    assert fsvc.account_balances(db)[world["broker"].id] == D("48437.00")   # corporate actions move no cash


def test_mutual_fund_units_can_be_fractional_but_shares_cannot(db, world):
    svc.save_investment_tx(db, None, kind="buy", tx_date=date(2026, 3, 1), instrument_id=world["fund"].id,
                           account_id=world["broker"].id, quantity=D("12.345"), price=D("52.3417"))
    with pytest.raises(ValidationError, match="whole"):
        svc.save_investment_tx(db, None, kind="buy", tx_date=date(2026, 3, 1), instrument_id=world["abc"].id,
                               account_id=world["broker"].id, quantity=D("1.5"), price=D("100"))
    fund = [h for h in svc.build_portfolio(db, as_of=AS_OF).holdings if h.instrument.symbol == "FUNDX"][0]
    assert fund.quantity == D("12.345") and fund.cost_basis == D("646.17")   # 12.345 x 52.3417 = 646.1718...


@pytest.mark.parametrize("kwargs, message", [
    (dict(kind="buy", account=False, quantity="1", price="1"), "Choose the account"),
    (dict(kind="buy", day=date(2025, 3, 31), quantity="1", price="1"), "opens on"),
    (dict(kind="buy", instrument=False, quantity="1", price="1"), "Choose an instrument"),
    (dict(kind="buy", quantity="0", price="1"), "greater than zero"),
    (dict(kind="buy", quantity="1", price="1", fees="-1"), "negative"),
    (dict(kind="dividend", amount="0"), "greater than zero"),
    (dict(kind="split", ratio_new=0, ratio_old=1), "ratio"),
    (dict(kind="nonsense"), "valid transaction type"),
])
def test_investment_validation(db, world, kwargs, message):
    kw = dict(kwargs)
    kind, day = kw.pop("kind"), kw.pop("day", date(2026, 3, 1))
    use_account, use_instrument = kw.pop("account", True), kw.pop("instrument", True)
    for k in ("quantity", "price", "fees", "amount"):
        if k in kw:
            kw[k] = D(kw[k])
    before = len(svc.list_investment_txs(db))
    with pytest.raises(ValidationError, match=message):
        svc.save_investment_tx(db, None, kind=kind, tx_date=day,
                               instrument_id=world["abc"].id if use_instrument else None,
                               account_id=world["broker"].id if use_account else None, **kw)
    assert len(svc.list_investment_txs(db)) == before


def test_account_fee_needs_no_instrument(db, world):
    svc.save_investment_tx(db, None, kind="fee", tx_date=date(2026, 4, 1), account_id=world["broker"].id, amount=D("11"))
    assert fsvc.account_balances(db)[world["broker"].id] == D("48426.00")


def test_instrument_rules(db, world):
    with pytest.raises(ValidationError, match="already exists"):
        svc.save_instrument(db, None, symbol="ABC", exchange="NSE", name="Dup", type="equity")
    svc.save_instrument(db, None, symbol="ABC", exchange="BSE", name="Same symbol, other exchange", type="equity")
    with pytest.raises(ValidationError, match="AMFI"):
        svc.save_instrument(db, None, symbol="Z", exchange="NSE", name="Z", type="mutual_fund")
    with pytest.raises(ValidationError, match="NSE or BSE"):
        svc.save_instrument(db, None, symbol="Z", exchange="MF", name="Z", type="equity")
    with pytest.raises(ValidationError, match="ISIN"):
        svc.save_instrument(db, None, symbol="Q", exchange="NSE", name="Q", type="equity", isin="BAD")
    svc.save_instrument(db, world["abc"], symbol="ABC", exchange="NSE", name="ABC Limited", type="equity", isin="INE002A01018")
    assert db.get(type(world["abc"]), world["abc"].id).name == "ABC Limited"
    with pytest.raises(ValidationError, match="Mark it inactive"):
        svc.delete_instrument(db, world["abc"])


def test_deleting_an_unused_instrument_removes_its_prices(db, world):
    spare = svc.save_instrument(db, None, symbol="SPARE", exchange="NSE", name="Spare", type="equity")
    svc.set_prices(db, date(2026, 3, 1), {spare.id: D("10")})
    svc.delete_instrument(db, spare)
    assert db.scalar(select(func.count(Price.id)).where(Price.instrument_id == spare.id)) == 0


def test_prices_upsert_and_latest_on_or_before(db, world):
    iid = world["abc"].id
    svc.set_prices(db, date(2026, 3, 15), {iid: D("165.5")})                # replaces the 160 saved that day
    svc.set_prices(db, date(2026, 3, 30), {iid: D("170")})
    assert svc.latest_prices(db)[iid] == (D("170.0000"), date(2026, 3, 30))
    assert svc.latest_prices(db, as_of=date(2026, 3, 20))[iid] == (D("165.5000"), date(2026, 3, 15))
    assert svc.latest_prices(db, as_of=date(2026, 3, 1)) == {}
    assert len(svc.price_history(db, iid)) == 2
    with pytest.raises(ValidationError):
        svc.set_prices(db, date(2026, 3, 30), {iid: D("0")})


def test_accounts_with_investment_activity_are_protected(db, world):
    with pytest.raises(ValidationError, match="Deactivate"):
        fsvc.delete_account(db, world["broker"])
    with pytest.raises(ValidationError, match="opening date"):
        fsvc.update_account(db, world["broker"], name="Zerodha", type="brokerage", opening_balance=D("0"),
                            opening_date=date(2026, 2, 1))


def test_filters(db, world):
    f = svc.InvFilter
    assert len(svc.list_investment_txs(db)) == 5
    assert len(svc.list_investment_txs(db, f(kind="buy"))) == 2
    assert len(svc.list_investment_txs(db, f(instrument_id=world["abc"].id))) == 4      # the fee has no instrument
    assert len(svc.list_investment_txs(db, f(start=date(2026, 3, 1), end=date(2026, 3, 31)))) == 3
    assert svc.cash_effect_of(world["sell"]) == D("745.00")


def test_allocation_includes_cash_and_sector(db, world):
    pf = svc.build_portfolio(db, as_of=AS_OF, cash=D("48437"))
    assert [(a["name"], a["pct"]) for a in pf.allocation_type] == [("Cash", D("95.3")), ("Stocks", D("4.7"))]
    assert [a["name"] for a in pf.allocation_sector] == ["IT"]
