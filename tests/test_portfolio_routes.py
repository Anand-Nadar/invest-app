from datetime import date
from decimal import Decimal as D

import pytest

from app.models.portfolio import Instrument
from app.services import finance as fsvc
from app.services import portfolio as svc


def post(client, url, **data):
    return client.post(url, data=data, follow_redirects=False)


@pytest.fixture()
def setup(client, db):
    """Bank + brokerage accounts, one instrument, and 50,000 moved into the brokerage - via the real forms."""
    post(client, "/finance/accounts/new", name="HDFC", type="bank", opening_balance="100000", opening_date="2026-04-01")
    post(client, "/finance/accounts/new", name="Zerodha", type="brokerage", opening_balance="0", opening_date="2026-04-01")
    accounts = {a.name: a.id for a in fsvc.list_accounts(db)}
    r = post(client, "/finance/transactions/new", kind="transfer", date="2026-04-05", amount="50000",
             account_id=str(accounts["HDFC"]), to_account_id=str(accounts["Zerodha"]))
    assert r.status_code == 303
    r = post(client, "/portfolio/instruments/new", symbol="abc", exchange="NSE", name="ABC Ltd", type="equity",
             sector="IT", isin="")
    assert r.status_code == 303
    inst = db.query(Instrument).one()
    return dict(bank=accounts["HDFC"], broker=accounts["Zerodha"], abc=inst.id)


def buy(client, s, day, qty, price, fees="0", kind="buy"):
    return post(client, "/portfolio/transactions/new", kind=kind, date=day, instrument_id=str(s["abc"]),
                account_id=str(s["broker"]), quantity=qty, price=price, fees=fees)


def test_all_portfolio_pages_render_empty(client):
    for url in ["/portfolio", "/portfolio/prices", "/portfolio/transactions", "/portfolio/transactions/new",
                "/portfolio/instruments", "/portfolio/instruments/new", "/portfolio/realized"]:
        assert client.get(url).status_code == 200, url
    assert "Add your first instrument" in client.get("/portfolio").text
    assert client.get("/portfolio/instruments/999").status_code == 404
    assert client.get("/portfolio/transactions/999/edit").status_code == 404


def test_buy_price_overview_and_cash(client, db, setup):
    assert buy(client, setup, "2026-05-01", "10", "100", "10").status_code == 303
    assert post(client, "/portfolio/prices", date="2026-05-02", **{f"price_{setup['abc']}": "110"}).status_code == 303

    page = client.get("/portfolio").text
    assert "₹1,100.00" in page            # 10 x 110
    assert "₹1,010.00" in page            # cost basis incl. 10 fees
    assert "+₹90.00" in page              # unrealized
    assert "ABC" in page and "Zerodha" in page

    # brokerage cash: 50,000 - 1,010
    assert "₹48,990.00" in client.get("/finance/accounts").text
    assert "₹48,990.00" in client.get("/portfolio").text
    # net worth = bank 50,000 + broker 48,990 + holdings 1,100
    assert "₹1,00,090.00" in client.get("/finance/wealth").text


def test_sell_realized_pnl_and_exports(client, db, setup):
    buy(client, setup, "2026-05-01", "10", "100", "10")
    assert buy(client, setup, "2026-05-10", "4", "120", "4", kind="sell").status_code == 303
    # proceeds 480 - 4 = 476; cost removed 1,010 x 4/10 = 404; realised +72
    page = client.get("/portfolio/realized?fy=2026").text
    assert "FY 2026-27" in page and "+₹72.00" in page and "₹476.00" in page and "₹404.00" in page
    assert "No sales" in client.get("/portfolio/realized?fy=2025").text

    csv = client.get("/portfolio/realized/export.csv?fy=2026").content.decode("utf-8-sig")
    assert "2026-05-10,ABC,ABC Ltd,4,476.00,404.00,72.00" in csv

    tx_csv = client.get("/portfolio/transactions/export.csv?period=all")
    assert tx_csv.status_code == 200 and "attachment" in tx_csv.headers["content-disposition"]
    text = tx_csv.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Date,Type,Symbol")
    assert "Sell,ABC" in text and "476.00" in text
    assert client.get("/portfolio/transactions/export.xlsx?period=all").content[:2] == b"PK"


def test_oversell_shows_error_and_changes_nothing(client, db, setup):
    buy(client, setup, "2026-05-01", "10", "100")
    r = buy(client, setup, "2026-05-02", "11", "100", kind="sell")
    assert r.status_code == 400 and "only 10 held" in r.text
    assert len(svc.list_investment_txs(db)) == 1


def test_invalid_input_messages(client, db, setup):
    r = buy(client, setup, "2026-05-01", "abc", "100")
    assert r.status_code == 400 and "must be a number" in r.text
    r = buy(client, setup, "2026-05-01", "1.5", "100")
    assert r.status_code == 400 and "whole" in r.text
    r = post(client, "/portfolio/transactions/new", kind="buy", date="2026-05-01", instrument_id="",
             account_id=str(setup["broker"]), quantity="1", price="1", fees="0")
    assert r.status_code == 400 and "Choose an instrument" in r.text
    assert svc.list_investment_txs(db) == []


def test_split_bonus_and_dividend_forms(client, db, setup):
    buy(client, setup, "2026-05-01", "10", "100")
    r = post(client, "/portfolio/transactions/new", kind="split", date="2026-06-01", instrument_id=str(setup["abc"]),
             ratio_old="1", ratio_new="5", account_id="", quantity="", price="")
    assert r.status_code == 303
    r = post(client, "/portfolio/transactions/new", kind="dividend", date="2026-06-10", instrument_id=str(setup["abc"]),
             account_id=str(setup["broker"]), amount="75")
    assert r.status_code == 303
    pf = svc.build_portfolio(db, as_of=date(2026, 6, 30))
    assert pf.holdings[0].quantity == D("50") and pf.totals.dividends == D("75.00")
    page = client.get("/portfolio/transactions?period=all").text
    assert "Each 1 unit became 5" in page and "Dividend received" in page


def test_edit_and_delete_transactions(client, db, setup):
    buy(client, setup, "2026-05-01", "10", "100", "10")
    buy(client, setup, "2026-05-10", "5", "150", "0", kind="sell")
    first_buy, sell = svc.list_investment_txs(db)[::-1][0], svc.list_investment_txs(db)[0]
    assert client.get(f"/portfolio/transactions/{first_buy.id}/edit").status_code == 200
    # editing the buy down to 4 shares would leave the later sell of 5 uncovered
    r = post(client, f"/portfolio/transactions/{first_buy.id}/edit", kind="buy", date="2026-05-01",
             instrument_id=str(setup["abc"]), account_id=str(setup["broker"]), quantity="4", price="100", fees="10")
    assert r.status_code == 400 and "only 4 held" in r.text
    # deleting the buy is blocked for the same reason
    r = post(client, f"/portfolio/transactions/{first_buy.id}/delete")
    assert r.status_code == 400 and "depend on it" in r.text
    # deleting the sell is fine
    assert post(client, f"/portfolio/transactions/{sell.id}/delete").status_code == 303
    db.expire_all()
    assert svc.build_portfolio(db, as_of=date(2026, 6, 1)).holdings[0].quantity == D("10")


def test_instrument_pages_and_rules(client, db, setup):
    dup = post(client, "/portfolio/instruments/new", symbol="ABC", exchange="NSE", name="Again", type="equity")
    assert dup.status_code == 400 and "already exists" in dup.text
    bad = post(client, "/portfolio/instruments/new", symbol="X", exchange="NSE", name="X", type="mutual_fund")
    assert bad.status_code == 400 and "AMFI" in bad.text

    buy(client, setup, "2026-05-01", "10", "100")
    detail = client.get(f"/portfolio/instruments/{setup['abc']}").text
    assert "ABC" in detail and "₹1,000.00" in detail
    r = post(client, f"/portfolio/instruments/{setup['abc']}/price", date="2026-05-02", price="105.5")
    assert r.status_code == 303
    assert "₹105.50" in client.get(f"/portfolio/instruments/{setup['abc']}").text

    r = post(client, f"/portfolio/instruments/{setup['abc']}/delete")
    assert r.status_code == 400 and "Mark it inactive" in r.text
    assert post(client, f"/portfolio/instruments/{setup['abc']}/toggle").status_code == 303
    db.expire_all()
    assert db.get(Instrument, setup["abc"]).is_active is False

    assert client.get(f"/portfolio/instruments/{setup['abc']}/edit").status_code == 200
    r = post(client, f"/portfolio/instruments/{setup['abc']}/edit", symbol="ABC", exchange="NSE", name="ABC Ltd",
             type="equity", sector="Banking", isin="")
    assert r.status_code == 303


def test_prices_form_validation(client, setup):
    r = post(client, "/portfolio/prices", date="2026-05-02")
    assert r.status_code == 400 and "at least one price" in r.text
    r = post(client, "/portfolio/prices", date="2026-05-02", **{f"price_{setup['abc']}": "abc"})
    assert r.status_code == 400 and "must be a number" in r.text


def test_transaction_filters_and_empty_states(client, db, setup):
    assert "Record your first transaction" in client.get("/portfolio").text
    buy(client, setup, "2026-05-01", "10", "100")
    page = client.get(f"/portfolio/transactions?period=all&kind=buy&instrument_id={setup['abc']}").text
    assert "1 transaction." in page
    assert "No transactions found" in client.get("/portfolio/transactions?period=all&kind=sell").text


def test_main_dashboard_and_snapshot_show_investments(client, db, setup):
    buy(client, setup, "2026-05-01", "10", "100")
    post(client, "/finance/wealth/snapshot")
    assert "Investments" in client.get("/").text
    snap = fsvc.list_snapshots(db)[0]
    assert snap.investments_value == D("1000.00")      # unpriced: counted at cost
