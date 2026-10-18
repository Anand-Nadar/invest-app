"""Reconciliation tests. Expected numbers are worked out by hand in the comments."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.models.finance import Transaction
from app.services import finance as svc
from app.services.errors import ValidationError

OCT = (date(2026, 10, 1), date(2026, 10, 31))


@pytest.fixture()
def world(db):
    hdfc = svc.create_account(db, name="HDFC", type="bank", opening_balance=D("10000"), opening_date=date(2026, 4, 1))
    cash = svc.create_account(db, name="Cash", type="cash", opening_balance=D("500"), opening_date=date(2026, 4, 1))
    salary = svc.create_category(db, name="Salary", kind="income")
    housing = svc.create_category(db, name="Housing", kind="expense")
    rent = svc.create_category(db, name="Rent", kind="expense", parent_id=housing.id)
    food = svc.create_category(db, name="Food", kind="expense")
    groceries = svc.create_category(db, name="Groceries", kind="expense", parent_id=food.id)
    dining = svc.create_category(db, name="Dining out", kind="expense", parent_id=food.id)

    def tx(kind, day, amount, account, to=None, cat=None, **kw):
        return svc.create_transaction(
            db, kind=kind, tx_date=date(2026, 10, day), amount=D(amount), account_id=account.id,
            to_account_id=to.id if to else None, category_id=cat.id if cat else None, **kw)

    txs = dict(
        salary=tx("income", 1, "50000", hdfc, cat=salary),
        groceries=tx("expense", 3, "1200.50", hdfc, cat=groceries, merchant="BigBasket", payment_method="UPI"),
        rent=tx("expense", 5, "15000", hdfc, cat=rent),
        transfer=tx("transfer", 6, "3000", hdfc, to=cash),
        dining=tx("expense", 7, "800", cash, cat=dining),
    )
    return dict(hdfc=hdfc, cash=cash, salary=salary, housing=housing, rent=rent, food=food,
                groceries=groceries, dining=dining, txs=txs)


def test_account_balances(db, world):
    bal = svc.account_balances(db)
    # HDFC: 10000 + 50000 - 1200.50 - 15000 - 3000 (transfer out) = 40799.50
    assert bal[world["hdfc"].id] == D("40799.50")
    # Cash: 500 + 3000 (transfer in) - 800 = 2700.00
    assert bal[world["cash"].id] == D("2700.00")


def test_transfers_do_not_inflate_income_or_expenses(db, world):
    s = svc.period_summary(db, *OCT)
    assert s.income == D("50000.00")                    # the 3000 transfer is not income
    assert s.expenses == D("17000.50")                  # 1200.50 + 15000 + 800; transfer is not an expense
    assert s.net_savings == D("32999.50")
    assert s.savings_rate == D("66.0")                  # 32999.50 / 50000 = 65.999%


def test_transfer_does_not_change_total_money(db, world):
    before = sum(svc.account_balances(db).values())
    svc.create_transaction(db, kind="transfer", tx_date=date(2026, 10, 20), amount=D("999.99"),
                           account_id=world["cash"].id, to_account_id=world["hdfc"].id)
    assert sum(svc.account_balances(db).values()) == before


def test_monthly_summaries_cover_every_month(db, world):
    months = svc.monthly_summaries(db, date(2026, 4, 1), date(2027, 3, 31))
    assert len(months) == 12
    by_month = {d: s for d, s in months}
    assert by_month[date(2026, 10, 1)].income == D("50000.00")
    assert by_month[date(2026, 9, 1)].income == D("0.00")
    assert by_month[date(2026, 9, 1)].savings_rate is None
    assert sum((s.income for _d, s in months), D("0")) == D("50000.00")


def test_category_breakdown_rolls_up_subcategories(db, world):
    rows = svc.category_breakdown(db, *OCT, "expense")
    assert [r["name"] for r in rows] == ["Housing", "Food"]
    housing, food = rows
    assert housing["amount"] == D("15000.00") and housing["pct"] == D("88.2")   # 15000 / 17000.50
    assert food["amount"] == D("2000.50") and food["pct"] == D("11.8")          # 1200.50 + 800
    assert {c["name"]: c["amount"] for c in food["children"]} == {"Groceries": D("1200.50"), "Dining out": D("800.00")}
    assert svc.category_breakdown(db, *OCT, "income")[0]["amount"] == D("50000.00")


def test_net_worth(db, world):
    svc.save_asset(db, None, name="Gold", type="gold", value=D("200000"), as_of=date(2026, 10, 1))
    svc.save_liability(db, None, name="Car loan", type="car_loan", outstanding=D("40000"),
                       as_of=date(2026, 10, 1), interest_rate=D("9.5"))
    nw = svc.net_worth(db)
    assert nw.accounts_total == D("43499.50")      # 40799.50 + 2700.00
    assert nw.other_assets == D("200000.00")
    assert nw.total_liabilities == D("40000.00")
    assert nw.net_worth == D("203499.50")          # 43499.50 + 200000 - 40000


def test_snapshot_upserts_per_day(db, world):
    first = svc.record_snapshot(db, date(2026, 10, 18))
    svc.create_transaction(db, kind="income", tx_date=date(2026, 10, 18), amount=D("100"),
                           account_id=world["cash"].id, category_id=world["salary"].id)
    second = svc.record_snapshot(db, date(2026, 10, 18))
    assert first.id == second.id
    assert len(svc.list_snapshots(db)) == 1
    assert second.net_worth == D("43599.50")


def test_edit_and_delete_update_balances(db, world):
    groceries = world["txs"]["groceries"]
    svc.update_transaction(db, groceries, kind="expense", tx_date=groceries.date, amount=D("200"),
                           account_id=world["hdfc"].id, category_id=world["groceries"].id)
    assert svc.account_balances(db)[world["hdfc"].id] == D("41800.00")   # 10000+50000-200-15000-3000
    svc.delete_transaction(db, world["txs"]["transfer"])
    assert svc.account_balances(db)[world["hdfc"].id] == D("44800.00")
    assert svc.account_balances(db)[world["cash"].id] == D("-300.00")    # 500 - 800


def test_changing_transaction_kind_to_transfer_and_back(db, world):
    t = world["txs"]["dining"]
    svc.update_transaction(db, t, kind="transfer", tx_date=t.date, amount=D("800"),
                           account_id=world["cash"].id, to_account_id=world["hdfc"].id)
    assert t.category_id is None and t.to_account_id == world["hdfc"].id
    svc.update_transaction(db, t, kind="expense", tx_date=t.date, amount=D("800"),
                           account_id=world["cash"].id, category_id=world["dining"].id)
    assert t.to_account_id is None and t.category_id == world["dining"].id


def test_filters(db, world):
    all_rows = svc.list_transactions(db)
    assert len(all_rows) == 5
    assert all_rows[0].date >= all_rows[-1].date
    f = svc.TxFilter
    assert len(svc.list_transactions(db, f(kind="expense"))) == 3
    assert len(svc.list_transactions(db, f(account_id=world["cash"].id))) == 2        # transfer in + dining
    assert len(svc.list_transactions(db, f(category_id=world["food"].id))) == 2       # includes subcategories
    assert len(svc.list_transactions(db, f(q="bigbasket"))) == 1
    assert len(svc.list_transactions(db, f(start=date(2026, 10, 5), end=date(2026, 10, 6)))) == 2
    income, expenses = svc.filter_totals(svc.list_transactions(db, f(account_id=world["hdfc"].id)))
    assert (income, expenses) == (D("50000.00"), D("16200.50"))


@pytest.mark.parametrize("kwargs, message", [
    (dict(kind="transfer", account="hdfc", to="hdfc"), "two different accounts"),
    (dict(kind="transfer", account="hdfc", to=None), "money is moving to"),
    (dict(kind="transfer", account="hdfc", to="cash", cat="salary"), "don't have a category"),
    (dict(kind="expense", account="hdfc", cat="salary"), "income category"),
    (dict(kind="income", account="hdfc", cat="rent"), "expense category"),
    (dict(kind="expense", account="hdfc", cat=None), "Choose a category"),
    (dict(kind="income", account="hdfc", cat="salary", to="cash"), "Only transfers"),
    (dict(kind="expense", account="hdfc", cat="rent", amount="0"), "greater than zero"),
    (dict(kind="expense", account="hdfc", cat="rent", day=date(2026, 3, 31)), "opens on"),
])
def test_transaction_validation(db, world, kwargs, message):
    with pytest.raises(ValidationError, match=message):
        svc.create_transaction(
            db, kind=kwargs["kind"], tx_date=kwargs.get("day", date(2026, 10, 10)),
            amount=D(kwargs.get("amount", "10")), account_id=world[kwargs["account"]].id,
            to_account_id=world[kwargs["to"]].id if kwargs.get("to") else None,
            category_id=world[kwargs["cat"]].id if kwargs.get("cat") else None)
    assert db.query(Transaction).count() == 5   # nothing was saved


def test_account_rules(db, world):
    with pytest.raises(ValidationError, match="already exists"):
        svc.create_account(db, name="hdfc", type="bank", opening_balance=D("0"), opening_date=date(2026, 4, 1))
    with pytest.raises(ValidationError, match="transactions"):
        svc.delete_account(db, world["hdfc"])
    with pytest.raises(ValidationError, match="opening date"):
        svc.update_account(db, world["hdfc"], name="HDFC", type="bank", opening_balance=D("10000"),
                           opening_date=date(2026, 10, 15))
    empty = svc.create_account(db, name="Old", type="other", opening_balance=D("0"), opening_date=date(2026, 4, 1))
    svc.delete_account(db, empty)
    assert all(a.name != "Old" for a in svc.list_accounts(db))
    svc.set_account_active(db, world["cash"], False)
    assert [a.name for a in svc.list_accounts(db, active_only=True)] == ["HDFC"]
    # inactive accounts still count toward net worth
    assert svc.net_worth(db).accounts_total == D("43499.50")


def test_credit_card_negative_balance_reduces_net_worth(db):
    svc.create_account(db, name="Bank", type="bank", opening_balance=D("1000"), opening_date=date(2026, 1, 1))
    svc.create_account(db, name="Card", type="credit_card", opening_balance=D("-250.75"), opening_date=date(2026, 1, 1))
    assert svc.net_worth(db).net_worth == D("749.25")


def test_category_rules(db, world):
    with pytest.raises(ValidationError, match="already exists"):
        svc.create_category(db, name="salary", kind="income")
    with pytest.raises(ValidationError, match="top-level"):
        svc.create_category(db, name="Deep", kind="expense", parent_id=world["rent"].id)
    with pytest.raises(ValidationError, match="same type"):
        svc.create_category(db, name="Odd", kind="income", parent_id=world["housing"].id)
    with pytest.raises(ValidationError, match="subcategories"):
        svc.delete_category(db, world["housing"])
    with pytest.raises(ValidationError, match="used by"):
        svc.delete_category(db, world["rent"])
    # same name is fine under a different parent
    svc.create_category(db, name="Other", kind="expense", parent_id=world["food"].id)
    svc.create_category(db, name="Other", kind="expense", parent_id=world["housing"].id)
    unused = svc.create_category(db, name="Spare", kind="expense")
    svc.rename_category(db, unused, "Spare change")
    svc.delete_category(db, unused)
    labels = [o["label"] for o in svc.category_options(db, "expense")]
    assert "Housing > Rent" in labels and "Spare change" not in labels
    assert svc.category_label(world["rent"]) == "Housing > Rent"


def test_asset_and_liability_validation(db):
    with pytest.raises(ValidationError):
        svc.save_asset(db, None, name="X", type="nonsense", value=D("1"), as_of=date(2026, 1, 1))
    with pytest.raises(ValidationError):
        svc.save_liability(db, None, name="X", type="home_loan", outstanding=D("-1"), as_of=date(2026, 1, 1))
    a = svc.save_asset(db, None, name="Flat", type="real_estate", value=D("5000000"), as_of=date(2026, 1, 1))
    svc.save_asset(db, a, name="Flat", type="real_estate", value=D("5200000"), as_of=date(2026, 6, 1))
    assert svc.net_worth(db).other_assets == D("5200000.00")
