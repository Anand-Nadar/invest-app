from datetime import date
from decimal import Decimal as D
from io import BytesIO

import pytest

from app.models.finance import Category
from app.services import finance as svc


def post(client, url, **data):
    return client.post(url, data=data, follow_redirects=False)


@pytest.fixture()
def setup(client, db):
    """One account and a few categories, created through the real forms."""
    r = post(client, "/finance/accounts/new", name="HDFC", type="bank", opening_balance="10,000", opening_date="2026-04-01")
    assert r.status_code == 303
    post(client, "/finance/categories", name="Salary", kind="income", parent_id="")
    post(client, "/finance/categories", name="Food", kind="expense", parent_id="")
    food = db.query(Category).filter_by(name="Food").one()
    post(client, "/finance/categories", name="Groceries", kind="expense", parent_id=str(food.id))
    acc = svc.list_accounts(db)[0]
    ids = {c.name: c.id for c in db.query(Category)}
    return dict(account=acc.id, **ids)


def test_all_finance_pages_render_empty(client):
    for url in ["/finance", "/finance/accounts", "/finance/accounts/new", "/finance/transactions",
                "/finance/transactions/new", "/finance/categories", "/finance/wealth",
                "/finance/wealth/assets/new", "/finance/wealth/liabilities/new", "/finance/reports"]:
        assert client.get(url).status_code == 200, url


def test_full_flow_updates_overview_and_lists(client, db, setup):
    post(client, "/finance/transactions/new", kind="income", date="2026-10-01", amount="50,000",
         account_id=str(setup["account"]), category_id=str(setup["Salary"]))
    r = post(client, "/finance/transactions/new", kind="expense", date="2026-10-03", amount="1200.50",
             account_id=str(setup["account"]), category_id=str(setup["Groceries"]),
             merchant="BigBasket", payment_method="UPI", notes="weekly")
    assert r.status_code == 303

    page = client.get("/finance?month=2026-10").text
    assert "₹50,000.00" in page and "₹1,200.50" in page and "₹48,799.50" in page
    assert "97.6%" in page    # 48799.50 / 50000

    accounts = client.get("/finance/accounts").text
    assert "₹58,799.50" in accounts       # 10000 + 50000 - 1200.50

    lst = client.get("/finance/transactions?period=all").text
    assert "BigBasket" in lst and "Food &gt; Groceries" in lst
    assert "BigBasket" not in client.get("/finance/transactions?period=all&q=nothing").text
    assert "BigBasket" in client.get("/finance/transactions?period=all&q=bigbasket").text


def test_invalid_form_shows_error_and_saves_nothing(client, db, setup):
    r = post(client, "/finance/transactions/new", kind="expense", date="2026-10-03", amount="abc",
             account_id=str(setup["account"]), category_id=str(setup["Groceries"]))
    assert r.status_code == 400 and "must be a number" in r.text
    r = post(client, "/finance/transactions/new", kind="expense", date="2026-10-03", amount="10",
             account_id=str(setup["account"]), category_id=str(setup["Salary"]))
    assert r.status_code == 400 and "income category" in r.text
    assert svc.list_transactions(db) == []


def test_edit_and_delete_transaction(client, db, setup):
    post(client, "/finance/transactions/new", kind="expense", date="2026-10-03", amount="100",
         account_id=str(setup["account"]), category_id=str(setup["Groceries"]))
    tx = svc.list_transactions(db)[0]
    assert client.get(f"/finance/transactions/{tx.id}/edit").status_code == 200
    r = post(client, f"/finance/transactions/{tx.id}/edit", kind="expense", date="2026-10-03", amount="250",
             account_id=str(setup["account"]), category_id=str(setup["Groceries"]))
    assert r.status_code == 303
    db.expire_all()
    assert svc.account_balances(db)[setup["account"]] == D("9750.00")
    post(client, f"/finance/transactions/{tx.id}/delete")
    db.expire_all()
    assert svc.account_balances(db)[setup["account"]] == D("10000.00")
    assert client.get(f"/finance/transactions/{tx.id}/edit").status_code == 404


def test_transfer_via_form(client, db, setup):
    post(client, "/finance/accounts/new", name="Cash", type="cash", opening_balance="0", opening_date="2026-04-01")
    cash = [a for a in svc.list_accounts(db) if a.name == "Cash"][0]
    r = post(client, "/finance/transactions/new", kind="transfer", date="2026-10-02", amount="500",
             account_id=str(setup["account"]), to_account_id=str(cash.id), category_id=str(setup["Groceries"]))
    assert r.status_code == 303      # stray category from a hidden field is ignored for transfers
    s = svc.period_summary(db, date(2026, 10, 1), date(2026, 10, 31))
    assert s.income == 0 and s.expenses == 0


def test_account_deactivate_and_delete_rules(client, db, setup):
    acc = setup["account"]
    post(client, f"/finance/accounts/{acc}/toggle")
    db.expire_all()
    assert svc.list_accounts(db)[0].is_active is False
    post(client, "/finance/transactions/new", kind="income", date="2026-10-01", amount="5",
         account_id=str(acc), category_id=str(setup["Salary"]))
    r = post(client, f"/finance/accounts/{acc}/delete")
    assert r.status_code == 400 and "Deactivate it instead" in r.text
    dup = post(client, "/finance/accounts/new", name="hdfc", type="bank", opening_balance="0", opening_date="2026-04-01")
    assert dup.status_code == 400 and "already exists" in dup.text


def test_wealth_assets_liabilities_and_snapshot(client, db, setup):
    r = post(client, "/finance/wealth/assets/new", name="Gold", type="gold", amount="2,00,000", as_of="2026-10-01")
    assert r.status_code == 303
    r = post(client, "/finance/wealth/liabilities/new", name="Car loan", type="car_loan", amount="40000",
             interest_rate="9.5", as_of="2026-10-01")
    assert r.status_code == 303
    page = client.get("/finance/wealth").text
    assert "₹1,70,000.00" in page          # 10000 + 200000 - 40000
    assert "9.5%" in page
    assert post(client, "/finance/wealth/snapshot").status_code == 303
    assert "₹1,70,000.00" in client.get("/finance/wealth").text
    assert client.get("/finance/wealth/nonsense/new").status_code == 404
    bad = post(client, "/finance/wealth/assets/new", name="X", type="gold", amount="-5", as_of="2026-10-01")
    assert bad.status_code == 400


def test_edit_asset(client, db, setup):
    post(client, "/finance/wealth/assets/new", name="Gold", type="gold", amount="100", as_of="2026-10-01")
    asset = svc.list_assets(db)[0]
    assert client.get(f"/finance/wealth/assets/{asset.id}/edit").status_code == 200
    post(client, f"/finance/wealth/assets/{asset.id}/edit", name="Gold", type="gold", amount="150", as_of="2026-10-02")
    db.expire_all()
    assert svc.list_assets(db)[0].value == D("150.00")
    post(client, f"/finance/wealth/assets/{asset.id}/delete")
    assert svc.list_assets(db) == []


def test_reports_and_exports(client, db, setup):
    post(client, "/finance/transactions/new", kind="income", date="2026-10-01", amount="1000",
         account_id=str(setup["account"]), category_id=str(setup["Salary"]))
    post(client, "/finance/transactions/new", kind="expense", date="2026-10-02", amount="250",
         account_id=str(setup["account"]), category_id=str(setup["Groceries"]), merchant="=HYPERLINK(1)")
    page = client.get("/finance/reports?fy=2026").text
    assert "FY 2026-27" in page and "₹750.00" in page and "75.0%" in page

    csv = client.get("/finance/transactions/export.csv?period=all")
    assert csv.status_code == 200 and "attachment" in csv.headers["content-disposition"]
    text = csv.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Date,Type,Amount")
    assert "'=HYPERLINK(1)" in text and "2026-10-02,expense,250.00" in text

    xlsx = client.get("/finance/transactions/export.xlsx?period=all")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"

    rcsv = client.get("/finance/reports/export.csv?fy=2026").content.decode("utf-8-sig")
    assert "Oct 2026,1000.00,250.00,750.00,75.0" in rcsv
    assert client.get("/finance/reports/export.xlsx?fy=2026").content[:2] == b"PK"
    assert client.get("/finance/reports?fy=garbage").status_code == 200


def test_category_pages(client, db, setup):
    page = client.get("/finance/categories").text
    assert "Groceries" in page and "Salary" in page
    r = post(client, "/finance/categories", name="Salary", kind="income", parent_id="")
    assert r.status_code == 400 and "already exists" in r.text
    gid = setup["Groceries"]
    r = post(client, f"/finance/categories/{gid}/rename", name="Supermarket")
    assert r.status_code == 303
    r = post(client, f"/finance/categories/{setup['Food']}/delete")
    assert r.status_code == 400 and "subcategories" in r.text


def test_main_dashboard_shows_finance_summary(client, setup):
    assert "No accounts yet" not in client.get("/").text
