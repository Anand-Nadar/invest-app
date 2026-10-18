"""Personal-finance logic (Phase 2; Phase 3 adds investment cash and holdings to balances and net worth).

Rules that every calculation here follows:
  * Money is Decimal, never float.
  * Transfers between your own accounts move balances but are NEVER income or expense.
  * Account balance = opening balance + all transaction effects (see _effects).
  * Net worth = account balances (incl. brokerage cash) + other assets + investment holdings - liabilities.
"""
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.finance import (
    ACCOUNT_TYPES, ASSET_TYPES, CATEGORY_KINDS, LIABILITY_TYPES, PAYMENT_METHODS, TX_KINDS,
    Account, Asset, Category, Liability, NetWorthSnapshot, Transaction,
)
from app.models.portfolio import InvestmentTransaction
from app.services import portfolio as portfolio_svc
from app.services.errors import ValidationError
from app.services.periods import shift_month
from app.services.portfolio_engine import cash_effect

log = logging.getLogger(__name__)
ZERO = Decimal("0.00")


def _check_choice(value, choices, label):
    keys = [c[0] if isinstance(c, tuple) else c for c in choices]
    if value not in keys:
        raise ValidationError(f"Choose a valid {label}.")


def _commit(db: Session, obj):
    db.add(obj)
    db.commit()
    return obj


# ----------------------------------------------------------------------------- accounts
def list_accounts(db: Session, active_only: bool = False) -> list[Account]:
    stmt = select(Account).order_by(Account.is_active.desc(), Account.name)
    if active_only:
        stmt = stmt.where(Account.is_active.is_(True))
    return list(db.scalars(stmt))


def _account_name_taken(db: Session, name: str, exclude_id: int | None = None) -> bool:
    stmt = select(Account.id).where(func.lower(Account.name) == name.lower())
    if exclude_id:
        stmt = stmt.where(Account.id != exclude_id)
    return db.scalar(stmt) is not None


def create_account(db, *, name, type, opening_balance, opening_date, notes=None) -> Account:
    _check_choice(type, ACCOUNT_TYPES, "account type")
    if _account_name_taken(db, name):
        raise ValidationError(f"An account named '{name}' already exists.")
    return _commit(db, Account(
        name=name, type=type, opening_balance=opening_balance, opening_date=opening_date,
        notes=notes, is_active=True,
    ))


def update_account(db, account: Account, *, name, type, opening_balance, opening_date, notes=None) -> Account:
    _check_choice(type, ACCOUNT_TYPES, "account type")
    if _account_name_taken(db, name, exclude_id=account.id):
        raise ValidationError(f"An account named '{name}' already exists.")
    earliest_dates = [
        db.scalar(select(func.min(Transaction.date)).where(
            or_(Transaction.account_id == account.id, Transaction.to_account_id == account.id))),
        db.scalar(select(func.min(InvestmentTransaction.date)).where(InvestmentTransaction.account_id == account.id)),
    ]
    earliest = min((d for d in earliest_dates if d), default=None)
    if earliest and earliest < opening_date:
        raise ValidationError(
            f"This account has transactions from {earliest:%d %b %Y}. "
            "Set the opening date on or before that day."
        )
    account.name, account.type, account.opening_balance = name, type, opening_balance
    account.opening_date, account.notes = opening_date, notes
    return _commit(db, account)


def set_account_active(db, account: Account, active: bool) -> Account:
    account.is_active = active
    return _commit(db, account)


def delete_account(db, account: Account) -> None:
    n = db.scalar(
        select(func.count(Transaction.id)).where(
            or_(Transaction.account_id == account.id, Transaction.to_account_id == account.id)
        )
    ) + db.scalar(select(func.count(InvestmentTransaction.id)).where(InvestmentTransaction.account_id == account.id))
    if n:
        raise ValidationError(
            f"'{account.name}' has {n} transaction{'s' if n != 1 else ''}. Deactivate it instead of deleting."
        )
    db.delete(account)
    db.commit()


# --------------------------------------------------------------------------- categories
def category_label(cat: Category | None) -> str:
    if cat is None:
        return ""
    return f"{cat.parent.name} > {cat.name}" if cat.parent else cat.name


def top_level_categories(db: Session, kind: str | None = None) -> list[Category]:
    stmt = select(Category).where(Category.parent_id.is_(None)).order_by(Category.kind.desc(), Category.name)
    if kind:
        stmt = stmt.where(Category.kind == kind)
    return list(db.scalars(stmt))


def category_options(db: Session, kind: str | None = None) -> list[dict]:
    """Flat, ordered list for dropdowns: parents followed by their children."""
    out = []
    for top in top_level_categories(db, kind):
        out.append({"id": top.id, "label": top.name, "kind": top.kind})
        for ch in top.children:
            out.append({"id": ch.id, "label": f"{top.name} > {ch.name}", "kind": top.kind})
    return out


def create_category(db, *, name, kind, parent_id=None) -> Category:
    _check_choice(kind, CATEGORY_KINDS, "category type")
    parent = None
    if parent_id is not None:
        parent = db.get(Category, parent_id)
        if parent is None:
            raise ValidationError("The parent category no longer exists.")
        if parent.parent_id is not None:
            raise ValidationError("Subcategories can only be added under a top-level category.")
        if parent.kind != kind:
            raise ValidationError("A subcategory must have the same type as its parent.")
    stmt = select(Category.id).where(
        func.lower(Category.name) == name.lower(), Category.kind == kind,
        Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id,
    )
    if db.scalar(stmt) is not None:
        raise ValidationError(f"The {kind} category '{name}' already exists here.")
    return _commit(db, Category(name=name, kind=kind, parent_id=parent_id))


def rename_category(db, cat: Category, name: str) -> Category:
    stmt = select(Category.id).where(
        func.lower(Category.name) == name.lower(), Category.kind == cat.kind, Category.id != cat.id,
        Category.parent_id.is_(None) if cat.parent_id is None else Category.parent_id == cat.parent_id,
    )
    if db.scalar(stmt) is not None:
        raise ValidationError(f"The {cat.kind} category '{name}' already exists here.")
    cat.name = name
    return _commit(db, cat)


def delete_category(db, cat: Category) -> None:
    if db.scalar(select(func.count(Category.id)).where(Category.parent_id == cat.id)):
        raise ValidationError(f"'{cat.name}' has subcategories. Delete or move them first.")
    used = db.scalar(select(func.count(Transaction.id)).where(Transaction.category_id == cat.id))
    if used:
        raise ValidationError(
            f"'{cat.name}' is used by {used} transaction{'s' if used != 1 else ''} and can't be deleted."
        )
    db.delete(cat)
    db.commit()


# ------------------------------------------------------------------------- transactions
def _validate_tx(db, *, kind, tx_date, amount, account_id, to_account_id, category_id):
    _check_choice(kind, TX_KINDS, "transaction type")
    if amount is None or amount <= 0:
        raise ValidationError("Amount must be greater than zero.")
    account = db.get(Account, account_id) if account_id else None
    if account is None:
        raise ValidationError("Choose an account.")
    accounts = [account]

    if kind == "transfer":
        to_account = db.get(Account, to_account_id) if to_account_id else None
        if to_account is None:
            raise ValidationError("Choose the account the money is moving to.")
        if to_account.id == account.id:
            raise ValidationError("A transfer needs two different accounts.")
        if category_id:
            raise ValidationError("Transfers don't have a category.")
        accounts.append(to_account)
    else:
        if to_account_id:
            raise ValidationError("Only transfers have a destination account.")
        cat = db.get(Category, category_id) if category_id else None
        if cat is None:
            raise ValidationError("Choose a category.")
        if cat.kind != kind:
            raise ValidationError(f"'{cat.name}' is an {cat.kind} category, but this is an {kind}.")

    for acc in accounts:
        if tx_date < acc.opening_date:
            raise ValidationError(
                f"'{acc.name}' opens on {acc.opening_date:%d %b %Y}. "
                "Pick a later date or move the account's opening date earlier."
            )


def _apply_tx(tx: Transaction, *, kind, tx_date, amount, account_id, to_account_id, category_id,
              merchant, payment_method, notes):
    tx.kind, tx.date, tx.amount, tx.account_id = kind, tx_date, amount, account_id
    tx.to_account_id = to_account_id if kind == "transfer" else None
    tx.category_id = None if kind == "transfer" else category_id
    tx.merchant, tx.payment_method, tx.notes = merchant, payment_method, notes


def create_transaction(db, *, kind, tx_date, amount, account_id, to_account_id=None, category_id=None,
                       merchant=None, payment_method=None, notes=None) -> Transaction:
    _validate_tx(db, kind=kind, tx_date=tx_date, amount=amount, account_id=account_id,
                 to_account_id=to_account_id, category_id=category_id)
    if payment_method is not None:
        _check_choice(payment_method, PAYMENT_METHODS, "payment method")
    tx = Transaction()
    _apply_tx(tx, kind=kind, tx_date=tx_date, amount=amount, account_id=account_id,
              to_account_id=to_account_id, category_id=category_id, merchant=merchant,
              payment_method=payment_method, notes=notes)
    _commit(db, tx)
    db.refresh(tx)
    return tx


def update_transaction(db, tx: Transaction, *, kind, tx_date, amount, account_id, to_account_id=None,
                       category_id=None, merchant=None, payment_method=None, notes=None) -> Transaction:
    _validate_tx(db, kind=kind, tx_date=tx_date, amount=amount, account_id=account_id,
                 to_account_id=to_account_id, category_id=category_id)
    if payment_method is not None:
        _check_choice(payment_method, PAYMENT_METHODS, "payment method")
    _apply_tx(tx, kind=kind, tx_date=tx_date, amount=amount, account_id=account_id,
              to_account_id=to_account_id, category_id=category_id, merchant=merchant,
              payment_method=payment_method, notes=notes)
    _commit(db, tx)
    db.refresh(tx)  # reload relationships (account/category) after FK changes
    return tx


def delete_transaction(db, tx: Transaction) -> None:
    db.delete(tx)
    db.commit()


@dataclass
class TxFilter:
    start: date | None = None
    end: date | None = None
    kind: str | None = None
    account_id: int | None = None
    category_id: int | None = None
    q: str | None = None


def list_transactions(db: Session, f: TxFilter | None = None) -> list[Transaction]:
    f = f or TxFilter()
    stmt = select(Transaction).order_by(Transaction.date.desc(), Transaction.id.desc())
    if f.start:
        stmt = stmt.where(Transaction.date >= f.start)
    if f.end:
        stmt = stmt.where(Transaction.date <= f.end)
    if f.kind:
        stmt = stmt.where(Transaction.kind == f.kind)
    if f.account_id:
        stmt = stmt.where(or_(Transaction.account_id == f.account_id, Transaction.to_account_id == f.account_id))
    if f.category_id:
        child_ids = select(Category.id).where(Category.parent_id == f.category_id)
        stmt = stmt.where(or_(Transaction.category_id == f.category_id, Transaction.category_id.in_(child_ids)))
    if f.q:
        like = f"%{f.q}%"
        stmt = stmt.where(or_(Transaction.merchant.ilike(like), Transaction.notes.ilike(like)))
    return list(db.scalars(stmt).unique())


def filter_totals(rows) -> tuple[Decimal, Decimal]:
    """(income, expenses) of the given rows; transfers are ignored."""
    income = sum((r.amount for r in rows if r.kind == "income"), ZERO)
    expenses = sum((r.amount for r in rows if r.kind == "expense"), ZERO)
    return income, expenses


# ---------------------------------------------------------------------------- calculations
def _effects(kind, amount, account_id, to_account_id):
    if kind == "income":
        yield account_id, amount
    elif kind == "expense":
        yield account_id, -amount
    elif kind == "transfer":
        yield account_id, -amount
        yield to_account_id, amount


def account_balances(db: Session) -> dict[int, Decimal]:
    balances = {a.id: Decimal(a.opening_balance) for a in db.scalars(select(Account))}
    rows = db.execute(
        select(Transaction.kind, Transaction.amount, Transaction.account_id, Transaction.to_account_id)
    )
    for kind, amount, account_id, to_account_id in rows:
        for acc_id, delta in _effects(kind, Decimal(amount), account_id, to_account_id):
            balances[acc_id] += delta
    # Investment buys/sells/dividends/fees move cash through their account (see portfolio_engine.cash_effect)
    inv_rows = db.execute(
        select(InvestmentTransaction.kind, InvestmentTransaction.account_id, InvestmentTransaction.quantity,
               InvestmentTransaction.price, InvestmentTransaction.fees, InvestmentTransaction.amount)
        .where(InvestmentTransaction.account_id.is_not(None))
    )
    for kind, acc_id, qty, price, fees, amount in inv_rows:
        balances[acc_id] += cash_effect(kind, qty, price, fees, amount)
    return balances


@dataclass(frozen=True)
class PeriodSummary:
    income: Decimal = ZERO
    expenses: Decimal = ZERO

    @property
    def net_savings(self) -> Decimal:
        return self.income - self.expenses

    @property
    def savings_rate(self) -> Decimal | None:
        """Net savings as % of income; None when there was no income."""
        if self.income <= 0:
            return None
        return (self.net_savings / self.income * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _income_expense_rows(db, start, end):
    return db.execute(
        select(Transaction.date, Transaction.kind, Transaction.amount).where(
            Transaction.kind.in_(("income", "expense")), Transaction.date >= start, Transaction.date <= end
        )
    )


def period_summary(db: Session, start: date, end: date) -> PeriodSummary:
    income = expenses = ZERO
    for _d, kind, amount in _income_expense_rows(db, start, end):
        if kind == "income":
            income += Decimal(amount)
        else:
            expenses += Decimal(amount)
    return PeriodSummary(income, expenses)


def monthly_summaries(db: Session, start: date, end: date) -> list[tuple[date, PeriodSummary]]:
    inc, exp = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for d, kind, amount in _income_expense_rows(db, start, end):
        key = d.replace(day=1)
        if kind == "income":
            inc[key] += Decimal(amount)
        else:
            exp[key] += Decimal(amount)
    out, cur, last = [], start.replace(day=1), end.replace(day=1)
    while cur <= last:
        out.append((cur, PeriodSummary(inc[cur], exp[cur])))
        cur = shift_month(cur, 1)
    return out


def category_breakdown(db: Session, start: date, end: date, kind: str) -> list[dict]:
    """Totals per top-level category (subcategories rolled up), largest first."""
    cats = {c.id: c for c in db.scalars(select(Category))}
    tops: dict[int, dict] = {}
    rows = db.execute(
        select(Transaction.category_id, Transaction.amount).where(
            Transaction.kind == kind, Transaction.date >= start, Transaction.date <= end
        )
    )
    for cid, amount in rows:
        cat = cats.get(cid)
        if cat is None:
            continue
        top = cats[cat.parent_id] if cat.parent_id else cat
        entry = tops.setdefault(top.id, {"name": top.name, "amount": ZERO, "children": defaultdict(lambda: ZERO)})
        entry["amount"] += Decimal(amount)
        if cat.parent_id:
            entry["children"][cat.name] += Decimal(amount)
    total = sum((e["amount"] for e in tops.values()), ZERO)
    result = []
    for e in sorted(tops.values(), key=lambda e: e["amount"], reverse=True):
        result.append({
            "name": e["name"],
            "amount": e["amount"],
            "pct": (e["amount"] / total * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if total else ZERO,
            "children": [{"name": n, "amount": a} for n, a in sorted(e["children"].items(), key=lambda kv: -kv[1])],
        })
    return result


# ------------------------------------------------------------------- assets & liabilities
def list_assets(db) -> list[Asset]:
    return list(db.scalars(select(Asset).order_by(Asset.value.desc(), Asset.name)))


def list_liabilities(db) -> list[Liability]:
    return list(db.scalars(select(Liability).order_by(Liability.outstanding.desc(), Liability.name)))


def save_asset(db, asset: Asset | None, *, name, type, value, as_of, notes=None) -> Asset:
    _check_choice(type, ASSET_TYPES, "asset type")
    if value < 0:
        raise ValidationError("Value cannot be negative.")
    asset = asset or Asset()
    asset.name, asset.type, asset.value, asset.as_of, asset.notes = name, type, value, as_of, notes
    return _commit(db, asset)


def save_liability(db, liability: Liability | None, *, name, type, outstanding, as_of,
                   interest_rate=None, notes=None) -> Liability:
    _check_choice(type, LIABILITY_TYPES, "liability type")
    if outstanding < 0:
        raise ValidationError("Outstanding amount cannot be negative.")
    liability = liability or Liability()
    liability.name, liability.type, liability.outstanding = name, type, outstanding
    liability.interest_rate, liability.as_of, liability.notes = interest_rate, as_of, notes
    return _commit(db, liability)


def delete_item(db, obj) -> None:
    db.delete(obj)
    db.commit()


# --------------------------------------------------------------------------- net worth
@dataclass
class NetWorth:
    account_rows: list  # [(Account, balance)]
    assets: list
    liabilities: list
    accounts_total: Decimal
    other_assets: Decimal
    total_liabilities: Decimal
    investments_value: Decimal = ZERO  # market value of stocks/funds (unpriced holdings at cost)

    @property
    def net_worth(self) -> Decimal:
        return self.accounts_total + self.other_assets + self.investments_value - self.total_liabilities

    @property
    def total_assets(self) -> Decimal:
        return self.accounts_total + self.other_assets + self.investments_value


def net_worth(db: Session) -> NetWorth:
    balances = account_balances(db)
    rows = [(a, balances[a.id]) for a in list_accounts(db)]
    assets, liabilities = list_assets(db), list_liabilities(db)
    return NetWorth(
        account_rows=rows,
        assets=assets,
        liabilities=liabilities,
        accounts_total=sum((b for _a, b in rows), ZERO),
        other_assets=sum((Decimal(a.value) for a in assets), ZERO),
        total_liabilities=sum((Decimal(l.outstanding) for l in liabilities), ZERO),
        investments_value=portfolio_svc.holdings_value(db),
    )


def record_snapshot(db: Session, on: date) -> NetWorthSnapshot:
    """Store (or overwrite) the net-worth snapshot for `on`."""
    nw = net_worth(db)
    snap = db.scalar(select(NetWorthSnapshot).where(NetWorthSnapshot.snapshot_date == on)) or NetWorthSnapshot(
        snapshot_date=on
    )
    snap.accounts_total, snap.other_assets = nw.accounts_total, nw.other_assets
    snap.investments_value = nw.investments_value
    snap.liabilities, snap.net_worth = nw.total_liabilities, nw.net_worth
    return _commit(db, snap)


def list_snapshots(db: Session) -> list[NetWorthSnapshot]:
    return list(db.scalars(select(NetWorthSnapshot).order_by(NetWorthSnapshot.snapshot_date)))
