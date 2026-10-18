"""Investment portfolio logic (database side). All maths lives in portfolio_engine.

Safety rule: every create/edit/delete of an investment transaction is applied inside the session,
the whole history is replayed, and the change is rolled back if the history becomes impossible
(for example deleting a buy that a later sell depends on).
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.finance import Account
from app.models.portfolio import EXCHANGES, INSTRUMENT_TYPES, INV_KINDS, Instrument, InvestmentTransaction, Price
from app.services import portfolio_engine as eng
from app.services.errors import ValidationError

log = logging.getLogger(__name__)
ZERO = eng.ZERO
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
TYPE_LABELS = {"equity": "Stocks", "etf": "ETFs", "mutual_fund": "Mutual funds"}
CASH_KINDS = ("buy", "sell", "dividend", "fee")


def _commit(db: Session, obj):
    db.add(obj)
    db.commit()
    return obj


# ----------------------------------------------------------------------------- instruments
def list_instruments(db: Session, active_only: bool = False) -> list[Instrument]:
    stmt = select(Instrument).order_by(Instrument.is_active.desc(), Instrument.symbol)
    if active_only:
        stmt = stmt.where(Instrument.is_active.is_(True))
    return list(db.scalars(stmt))


def instrument_label(i: Instrument) -> str:
    return f"{i.symbol} - {i.name}"


def _validate_instrument(db, inst, *, symbol, exchange, name, type, sector, isin):
    if type not in dict(INSTRUMENT_TYPES):
        raise ValidationError("Choose a valid instrument type.")
    if exchange not in dict(EXCHANGES):
        raise ValidationError("Choose a valid exchange.")
    if type == "mutual_fund" and exchange != "MF":
        raise ValidationError("Mutual funds use the 'Mutual fund (AMFI)' exchange.")
    if type != "mutual_fund" and exchange == "MF":
        raise ValidationError("Stocks and ETFs must be listed on NSE or BSE.")
    if isin and not ISIN_RE.match(isin):
        raise ValidationError("ISIN must be 12 characters, for example INE002A01018.")
    stmt = select(Instrument.id).where(Instrument.exchange == exchange, func.upper(Instrument.symbol) == symbol.upper())
    if inst is not None:
        stmt = stmt.where(Instrument.id != inst.id)
    if db.scalar(stmt) is not None:
        raise ValidationError(f"{symbol} on {exchange} already exists.")


def save_instrument(db, inst: Instrument | None, *, symbol, exchange, name, type, sector=None, isin=None) -> Instrument:
    symbol = symbol.strip().upper()
    isin = isin.strip().upper() if isin else None
    _validate_instrument(db, inst, symbol=symbol, exchange=exchange, name=name, type=type, sector=sector, isin=isin)
    inst = inst or Instrument(is_active=True)
    inst.symbol, inst.exchange, inst.name, inst.type = symbol, exchange, name, type
    inst.sector, inst.isin = sector, isin
    return _commit(db, inst)


def set_instrument_active(db, inst: Instrument, active: bool) -> Instrument:
    inst.is_active = active
    return _commit(db, inst)


def delete_instrument(db, inst: Instrument) -> None:
    n = db.scalar(select(func.count(InvestmentTransaction.id)).where(InvestmentTransaction.instrument_id == inst.id))
    if n:
        raise ValidationError(
            f"{inst.symbol} has {n} transaction{'s' if n != 1 else ''}. Mark it inactive instead of deleting it."
        )
    db.delete(inst)  # its prices go with it (ON DELETE CASCADE)
    db.commit()


# ---------------------------------------------------------------------------------- prices
def set_prices(db: Session, on: date, prices: dict[int, Decimal], source: str = "manual") -> int:
    """Upsert one price per instrument for `on`. Returns how many were saved."""
    for iid, price in prices.items():
        if price <= 0:
            raise ValidationError("Prices must be greater than zero.")
        if db.get(Instrument, iid) is None:
            raise ValidationError("An instrument no longer exists.")
        row = db.scalar(select(Price).where(Price.instrument_id == iid, Price.date == on))
        if row is None:
            row = Price(instrument_id=iid, date=on)
        row.price, row.source = price, source
        db.add(row)
    db.commit()
    return len(prices)


def latest_prices(db: Session, as_of: date | None = None) -> dict[int, tuple[Decimal, date]]:
    """{instrument_id: (price, date)} using the most recent price on or before `as_of`."""
    q = select(Price.instrument_id, func.max(Price.date).label("d"))
    if as_of:
        q = q.where(Price.date <= as_of)
    sub = q.group_by(Price.instrument_id).subquery()
    rows = db.execute(
        select(Price.instrument_id, Price.price, Price.date).join(
            sub, and_(Price.instrument_id == sub.c.instrument_id, Price.date == sub.c.d)
        )
    )
    return {iid: (Decimal(price), d) for iid, price, d in rows}


def price_history(db: Session, instrument_id: int, limit: int = 15) -> list[Price]:
    return list(db.scalars(
        select(Price).where(Price.instrument_id == instrument_id).order_by(Price.date.desc()).limit(limit)
    ))


# ------------------------------------------------------------------------ transactions
def _to_event(t: InvestmentTransaction, whole_units: bool) -> eng.Event:
    return eng.Event(
        id=t.id, date=t.date, kind=t.kind, instrument_id=t.instrument_id,
        quantity=Decimal(t.quantity) if t.quantity is not None else None,
        price=Decimal(t.price) if t.price is not None else None,
        fees=Decimal(t.fees or 0), amount=Decimal(t.amount) if t.amount is not None else None,
        ratio_new=t.ratio_new, ratio_old=t.ratio_old, whole_units=whole_units,
    )


def load_events(db: Session, instruments: dict[int, Instrument] | None = None) -> list[eng.Event]:
    instruments = instruments or {i.id: i for i in db.scalars(select(Instrument))}
    rows = db.scalars(select(InvestmentTransaction)).unique()
    return [
        _to_event(t, whole_units=not (instruments.get(t.instrument_id) and instruments[t.instrument_id].type == "mutual_fund"))
        for t in rows
    ]


def _labels(db) -> dict[int, str]:
    return {i.id: i.symbol for i in db.scalars(select(Instrument))}


def _check_history(db: Session) -> None:
    """Replay everything; raise EngineError if the stored history is impossible."""
    eng.replay(load_events(db), _labels(db))


def save_investment_tx(db: Session, tx: InvestmentTransaction | None, *, kind, tx_date, instrument_id=None,
                       account_id=None, quantity=None, price=None, fees=ZERO, amount=None,
                       ratio_new=None, ratio_old=None, notes=None) -> InvestmentTransaction:
    if kind not in INV_KINDS:
        raise ValidationError("Choose a valid transaction type.")

    inst = db.get(Instrument, instrument_id) if instrument_id else None
    if kind != "fee" and inst is None:
        raise ValidationError("Choose an instrument.")
    if instrument_id and inst is None:
        raise ValidationError("That instrument no longer exists.")

    account = db.get(Account, account_id) if account_id else None
    if kind in CASH_KINDS:
        if account is None:
            raise ValidationError("Choose the account the cash moves through.")
        if tx_date < account.opening_date:
            raise ValidationError(
                f"'{account.name}' opens on {account.opening_date:%d %b %Y}. "
                "Pick a later date or move the account's opening date earlier."
            )

    if kind in ("buy", "sell"):
        if not quantity or quantity <= 0 or not price or price <= 0:
            raise ValidationError("Quantity and price must be greater than zero.")
        if inst.type != "mutual_fund" and quantity != quantity.to_integral_value():
            raise ValidationError(f"{inst.symbol} trades in whole units; enter a whole number of shares.")
        if fees is None or fees < 0:
            raise ValidationError("Fees cannot be negative.")
    elif kind in ("dividend", "fee"):
        if not amount or amount <= 0:
            raise ValidationError("Amount must be greater than zero.")
    else:  # split / bonus
        if not ratio_new or not ratio_old or ratio_new <= 0 or ratio_old <= 0:
            raise ValidationError("Both parts of the ratio must be whole numbers greater than zero.")

    is_new = tx is None
    tx = tx or InvestmentTransaction()
    relevant = {
        "buy": ("instrument_id", "account_id", "quantity", "price", "fees"),
        "sell": ("instrument_id", "account_id", "quantity", "price", "fees"),
        "dividend": ("instrument_id", "account_id", "amount"),
        "fee": ("instrument_id", "account_id", "amount"),
        "split": ("instrument_id", "ratio_new", "ratio_old"),
        "bonus": ("instrument_id", "ratio_new", "ratio_old"),
    }[kind]
    values = dict(instrument_id=instrument_id, account_id=account_id, quantity=quantity, price=price,
                  fees=fees, amount=amount, ratio_new=ratio_new, ratio_old=ratio_old)
    tx.date, tx.kind, tx.notes = tx_date, kind, notes
    for field_name, value in values.items():
        setattr(tx, field_name, value if field_name in relevant else (ZERO if field_name == "fees" else None))

    db.add(tx)
    db.flush()
    try:
        _check_history(db)
    except eng.EngineError as e:
        db.rollback()
        raise ValidationError(str(e)) from None
    db.commit()
    db.refresh(tx)
    return tx


def delete_investment_tx(db: Session, tx: InvestmentTransaction) -> None:
    db.delete(tx)
    db.flush()
    try:
        _check_history(db)
    except eng.EngineError as e:
        db.rollback()
        raise ValidationError(f"This can't be deleted because later transactions depend on it. {e}") from None
    db.commit()


@dataclass
class InvFilter:
    start: date | None = None
    end: date | None = None
    kind: str | None = None
    instrument_id: int | None = None
    account_id: int | None = None


def list_investment_txs(db: Session, f: InvFilter | None = None) -> list[InvestmentTransaction]:
    f = f or InvFilter()
    stmt = select(InvestmentTransaction).order_by(InvestmentTransaction.date.desc(), InvestmentTransaction.id.desc())
    if f.start:
        stmt = stmt.where(InvestmentTransaction.date >= f.start)
    if f.end:
        stmt = stmt.where(InvestmentTransaction.date <= f.end)
    if f.kind:
        stmt = stmt.where(InvestmentTransaction.kind == f.kind)
    if f.instrument_id:
        stmt = stmt.where(InvestmentTransaction.instrument_id == f.instrument_id)
    if f.account_id:
        stmt = stmt.where(InvestmentTransaction.account_id == f.account_id)
    return list(db.scalars(stmt).unique())


def cash_effect_of(t: InvestmentTransaction) -> Decimal:
    return eng.cash_effect(t.kind, t.quantity, t.price, t.fees, t.amount)


# --------------------------------------------------------------------------- portfolio
@dataclass
class Portfolio:
    holdings: list
    closed: list
    totals: eng.Totals
    sales: list
    instruments: dict
    account_charges: Decimal = ZERO
    allocation_type: list = field(default_factory=list)
    allocation_sector: list = field(default_factory=list)


def build_portfolio(db: Session, as_of: date | None = None, cash: Decimal = ZERO) -> Portfolio:
    """`cash` (brokerage balances) is only used to include cash in the asset-class allocation."""
    as_of = as_of or date.today()
    instruments = {i.id: i for i in db.scalars(select(Instrument))}
    result = eng.replay(load_events(db, instruments), {i: x.symbol for i, x in instruments.items()})
    open_rows, closed_rows, totals = eng.summarise(result, latest_prices(db, as_of), as_of)
    for row in open_rows + closed_rows:
        row.instrument = instruments.get(row.instrument_id)
    return Portfolio(
        holdings=open_rows, closed=closed_rows, totals=totals, sales=result.sales,
        instruments=instruments, account_charges=result.account_charges,
        allocation_type=eng.allocate(
            [(TYPE_LABELS.get(r.instrument.type, "Other"), r.market_value) for r in open_rows] + [("Cash", cash)]
        ),
        allocation_sector=eng.allocate([(r.instrument.sector or "Unclassified", r.market_value) for r in open_rows]),
    )


def holdings_value(db: Session) -> Decimal:
    """Market value of open holdings (unpriced ones at cost). Used by net worth; never raises."""
    try:
        return build_portfolio(db).totals.market_value
    except eng.EngineError:
        log.exception("Portfolio history is inconsistent; holdings excluded from net worth")
        return ZERO
