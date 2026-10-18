"""Phase 3 pages: holdings, prices, investment transactions, instruments, realized P&L."""
import logging
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import forms
from app.config import get_settings
from app.database import get_db
from app.models.portfolio import EXCHANGES, INSTRUMENT_TYPES, INV_KIND_LABELS, INV_KINDS, Instrument, InvestmentTransaction
from app.roadmap import NAV_PHASES, PHASES
from app.routers.common import (
    XLSX_TYPE, attachment as _attachment, form_data, get_or_404, redirect, safe_date as _safe_date,
    safe_int as _safe_int,
)
from app.services import finance as finance_svc
from app.services import portfolio as svc
from app.services.errors import ValidationError
from app.services.exports import to_csv, to_xlsx
from app.services.periods import PRESETS, fy_bounds, fy_label, fy_year_of, resolve_range
from app.services.portfolio_engine import EngineError, ZERO
from app.templating import templates

log = logging.getLogger(__name__)
router = APIRouter(prefix="/portfolio", include_in_schema=False)
PER_PAGE = 50
SECTORS = ["Banking", "Financial services", "IT", "Pharma and healthcare", "FMCG", "Auto", "Energy",
           "Metals and mining", "Infrastructure", "Capital goods", "Consumer durables", "Telecom",
           "Real estate", "Chemicals", "Diversified", "Index or broad market"]


def render(request: Request, template: str, status_code: int = 200, **ctx):
    base = {"settings": get_settings(), "nav": NAV_PHASES, "phases": PHASES, "active": "portfolio"}
    return templates.TemplateResponse(request, f"portfolio/{template}", {**base, **ctx}, status_code=status_code)


def _brokerage_cash(db):
    balances = finance_svc.account_balances(db)
    rows = [(a, balances[a.id]) for a in finance_svc.list_accounts(db) if a.type == "brokerage"]
    return rows, sum((b for _a, b in rows), ZERO)


# ------------------------------------------------------------------------------ overview
@router.get("")
def overview(request: Request, db: Session = Depends(get_db)):
    cash_rows, cash_total = _brokerage_cash(db)
    pf, error = None, None
    try:
        pf = svc.build_portfolio(db, cash=cash_total)
    except EngineError as e:
        log.exception("Portfolio replay failed")
        error = str(e)
    return render(
        request, "overview.html", tab="holdings", pf=pf, error=error, cash_rows=cash_rows, cash_total=cash_total,
        negative_cash=[(a, b) for a, b in cash_rows if b < 0],
        n_instruments=len(svc.list_instruments(db)),
        n_transactions=len(svc.list_investment_txs(db)),
        type_labels=svc.TYPE_LABELS,
    )


# -------------------------------------------------------------------------------- prices
@router.get("/prices")
def prices_form(request: Request, db: Session = Depends(get_db), error: str | None = None):
    return _prices_page(request, db, {"date": date.today().isoformat()}, error)


def _prices_page(request, db, values, error=None, status=200):
    held = set()
    try:
        held = {r.instrument_id for r in svc.build_portfolio(db).holdings}
    except EngineError:
        pass
    instruments = sorted(svc.list_instruments(db, active_only=True), key=lambda i: (i.id not in held, i.symbol))
    return render(request, "prices.html", status, tab="holdings", instruments=instruments, held=held,
                  latest=svc.latest_prices(db), values=values, error=error)


@router.post("/prices")
def prices_save(request: Request, data: dict = Depends(form_data), db: Session = Depends(get_db)):
    try:
        on = forms.parse_date(data.get("date"), "Price date")
        entered = {}
        for inst in svc.list_instruments(db, active_only=True):
            raw = (data.get(f"price_{inst.id}") or "").strip()
            if raw:
                entered[inst.id] = forms.parse_price(raw, f"Price for {inst.symbol}")
        if not entered:
            raise ValidationError("Enter at least one price.")
        svc.set_prices(db, on, entered)
    except ValidationError as e:
        db.rollback()
        return _prices_page(request, db, data, str(e), 400)
    return redirect("/portfolio")


# ------------------------------------------------------------------------------ instruments
def _parse_instrument(data: dict) -> dict:
    return dict(
        symbol=forms.clean_text(data.get("symbol"), "Symbol", 40, required=True),
        exchange=forms.parse_choice(data.get("exchange"), "Exchange", EXCHANGES),
        name=forms.clean_text(data.get("name"), "Name", 150, required=True),
        type=forms.parse_choice(data.get("type"), "Type", INSTRUMENT_TYPES),
        sector=forms.clean_text(data.get("sector"), "Sector", 60),
        isin=forms.clean_text(data.get("isin"), "ISIN", 12),
    )


def _instrument_values(i: Instrument | None) -> dict:
    if i is None:
        return {"exchange": "NSE", "type": "equity"}
    return {"symbol": i.symbol, "exchange": i.exchange, "name": i.name, "type": i.type,
            "sector": i.sector or "", "isin": i.isin or ""}


def _instrument_form(request, values, inst=None, error=None, status=200):
    return render(request, "instrument_form.html", status, tab="instruments", values=values, inst=inst, error=error,
                  exchanges=EXCHANGES, types=INSTRUMENT_TYPES, sectors=SECTORS)


@router.get("/instruments")
def instruments(request: Request, db: Session = Depends(get_db), error: str | None = None):
    return _instruments_page(request, db, error)


def _instruments_page(request, db, error=None, status=200):
    held = {}
    try:
        held = {r.instrument_id: r for r in svc.build_portfolio(db).holdings}
    except EngineError:
        pass
    return render(request, "instruments.html", status, tab="instruments", instruments=svc.list_instruments(db),
                  held=held, latest=svc.latest_prices(db), types=dict(INSTRUMENT_TYPES), error=error)


@router.get("/instruments/new")
def instrument_new(request: Request):
    return _instrument_form(request, _instrument_values(None))


@router.post("/instruments/new")
def instrument_create(request: Request, data: dict = Depends(form_data), db: Session = Depends(get_db)):
    try:
        svc.save_instrument(db, None, **_parse_instrument(data))
    except ValidationError as e:
        db.rollback()
        return _instrument_form(request, data, error=str(e), status=400)
    return redirect("/portfolio/instruments")


@router.get("/instruments/{instrument_id}")
def instrument_detail(request: Request, instrument_id: int, db: Session = Depends(get_db)):
    inst = get_or_404(db, Instrument, instrument_id)
    pf, error = None, None
    try:
        pf = svc.build_portfolio(db)
    except EngineError as e:
        error = str(e)
    row = None
    if pf:
        row = next((r for r in pf.holdings + pf.closed if r.instrument_id == inst.id), None)
    txs = svc.list_investment_txs(db, svc.InvFilter(instrument_id=inst.id))
    return render(
        request, "instrument_detail.html", tab="instruments", inst=inst, row=row, error=error, txs=txs,
        effects={t.id: svc.cash_effect_of(t) for t in txs}, labels=INV_KIND_LABELS,
        sales=[s for s in (pf.sales if pf else []) if s.instrument_id == inst.id][::-1],
        history=svc.price_history(db, inst.id), values={"date": date.today().isoformat()}, types=dict(INSTRUMENT_TYPES),
    )


@router.post("/instruments/{instrument_id}/price")
def instrument_price(request: Request, instrument_id: int, data: dict = Depends(form_data),
                     db: Session = Depends(get_db)):
    inst = get_or_404(db, Instrument, instrument_id)
    try:
        svc.set_prices(db, forms.parse_date(data.get("date"), "Price date"),
                       {inst.id: forms.parse_price(data.get("price"), "Price")})
    except ValidationError as e:
        db.rollback()
        return _instruments_page(request, db, str(e), 400)
    return redirect(f"/portfolio/instruments/{inst.id}")


@router.get("/instruments/{instrument_id}/edit")
def instrument_edit(request: Request, instrument_id: int, db: Session = Depends(get_db)):
    inst = get_or_404(db, Instrument, instrument_id)
    return _instrument_form(request, _instrument_values(inst), inst)


@router.post("/instruments/{instrument_id}/edit")
def instrument_update(request: Request, instrument_id: int, data: dict = Depends(form_data),
                      db: Session = Depends(get_db)):
    inst = get_or_404(db, Instrument, instrument_id)
    try:
        svc.save_instrument(db, inst, **_parse_instrument(data))
    except ValidationError as e:
        db.rollback()
        return _instrument_form(request, data, inst, str(e), 400)
    return redirect("/portfolio/instruments")


@router.post("/instruments/{instrument_id}/toggle")
def instrument_toggle(instrument_id: int, db: Session = Depends(get_db)):
    inst = get_or_404(db, Instrument, instrument_id)
    svc.set_instrument_active(db, inst, not inst.is_active)
    return redirect("/portfolio/instruments")


@router.post("/instruments/{instrument_id}/delete")
def instrument_delete(request: Request, instrument_id: int, db: Session = Depends(get_db)):
    inst = get_or_404(db, Instrument, instrument_id)
    try:
        svc.delete_instrument(db, inst)
    except ValidationError as e:
        return _instruments_page(request, db, str(e), 400)
    return redirect("/portfolio/instruments")


# ----------------------------------------------------------------------------- transactions
@dataclass
class InvView:
    filter: svc.InvFilter
    preset: str
    params: dict


def inv_view(period: str = "all", start: str = "", end: str = "", kind: str = "", instrument_id: str = "",
             account_id: str = "") -> InvView:
    s, e, preset = resolve_range(period, date.today(), _safe_date(start), _safe_date(end))
    f = svc.InvFilter(start=s, end=e, kind=kind if kind in INV_KINDS else None,
                      instrument_id=_safe_int(instrument_id), account_id=_safe_int(account_id))
    params = {"period": preset, "kind": f.kind, "instrument_id": f.instrument_id, "account_id": f.account_id}
    if preset == "custom":
        params.update(start=start, end=end)
    return InvView(f, preset, {k: v for k, v in params.items() if v not in (None, "")})


@router.get("/transactions")
def transactions(request: Request, view: InvView = Depends(inv_view), page: int = 1, db: Session = Depends(get_db)):
    rows = svc.list_investment_txs(db, view.filter)
    effects = {t.id: svc.cash_effect_of(t) for t in rows}
    pages = max(1, math.ceil(len(rows) / PER_PAGE))
    page = min(max(page, 1), pages)
    return render(
        request, "transactions.html", tab="transactions", rows=rows[(page - 1) * PER_PAGE: page * PER_PAGE],
        total=len(rows), effects=effects, net_cash=sum(effects.values(), ZERO), page=page, pages=pages, view=view,
        qs=urlencode(view.params), presets=PRESETS, kinds=INV_KINDS, labels=INV_KIND_LABELS,
        instruments=svc.list_instruments(db), accounts=finance_svc.list_accounts(db),
        has_instruments=bool(svc.list_instruments(db)),
    )


def _export_rows(db, view: InvView):
    headers = ["Date", "Type", "Symbol", "Instrument", "Quantity", "Price", "Fees", "Amount", "Split/bonus ratio",
               "Cash effect", "Account", "Notes"]
    rows = []
    for t in svc.list_investment_txs(db, view.filter):
        ratio = f"{t.ratio_new}:{t.ratio_old}" if t.ratio_new else ""
        rows.append([t.date, INV_KIND_LABELS[t.kind], t.instrument.symbol if t.instrument else "",
                     t.instrument.name if t.instrument else "", t.quantity, t.price, t.fees, t.amount, ratio,
                     svc.cash_effect_of(t), t.account.name if t.account else "", t.notes or ""])
    return headers, rows


@router.get("/transactions/export.csv")
def transactions_csv(view: InvView = Depends(inv_view), db: Session = Depends(get_db)):
    headers, rows = _export_rows(db, view)
    rows = [[v.isoformat() if isinstance(v, date) else ("" if v is None else str(v)) for v in r] for r in rows]
    return _attachment(to_csv(headers, rows), "investment-transactions.csv", "text/csv; charset=utf-8")


@router.get("/transactions/export.xlsx")
def transactions_xlsx(view: InvView = Depends(inv_view), db: Session = Depends(get_db)):
    headers, rows = _export_rows(db, view)
    return _attachment(to_xlsx(headers, rows, "Investments"), "investment-transactions.xlsx", XLSX_TYPE)


def _itx_form_ctx(db, values, tx=None, error=None):
    instruments = svc.list_instruments(db, active_only=tx is None)
    accounts = finance_svc.list_accounts(db, active_only=tx is None)
    if tx is None:
        brokerage = [a for a in accounts if a.type == "brokerage"]
        if len(brokerage) == 1:
            values = {"account_id": str(brokerage[0].id), **values}
    return dict(tab="transactions", values=values, tx=tx, error=error, kinds=INV_KINDS, labels=INV_KIND_LABELS,
                instruments=instruments, accounts=accounts)


def _itx_values(tx: InvestmentTransaction | None, kind: str = "buy") -> dict:
    if tx is None:
        return {"kind": kind if kind in INV_KINDS else "buy", "date": date.today().isoformat(), "fees": "0"}
    return {"kind": tx.kind, "date": tx.date.isoformat(), "instrument_id": str(tx.instrument_id or ""),
            "account_id": str(tx.account_id or ""),
            "quantity": format(tx.quantity.normalize(), "f") if tx.quantity is not None else "",
            "price": format(tx.price.normalize(), "f") if tx.price is not None else "",
            "fees": str(tx.fees), "amount": str(tx.amount) if tx.amount is not None else "",
            "ratio_new": str(tx.ratio_new or ""), "ratio_old": str(tx.ratio_old or ""), "notes": tx.notes or ""}


def _parse_itx(data: dict) -> dict:
    kind = forms.parse_choice(data.get("kind"), "Type", INV_KINDS)
    out = dict(kind=kind, tx_date=forms.parse_date(data.get("date"), "Date"),
               notes=forms.clean_text(data.get("notes"), "Notes", 500))
    instrument_id = forms.parse_optional_int(data.get("instrument_id"), "Instrument", required=(kind != "fee"))
    out["instrument_id"] = instrument_id
    if kind in ("buy", "sell"):
        out.update(
            account_id=forms.parse_optional_int(data.get("account_id"), "Account", required=True),
            quantity=forms.parse_quantity(data.get("quantity")),
            price=forms.parse_price(data.get("price"), "Price per unit"),
            fees=forms.parse_money(data.get("fees") or "0", "Fees", allow_zero=True),
        )
    elif kind in ("dividend", "fee"):
        out.update(
            account_id=forms.parse_optional_int(data.get("account_id"), "Account", required=True),
            amount=forms.parse_money(data.get("amount"), "Amount"),
        )
    else:
        out.update(ratio_new=forms.parse_positive_int(data.get("ratio_new"), "New units"),
                   ratio_old=forms.parse_positive_int(data.get("ratio_old"), "Old units"))
    return out


@router.get("/transactions/new")
def transaction_new(request: Request, kind: str = "buy", db: Session = Depends(get_db)):
    return render(request, "transaction_form.html", **_itx_form_ctx(db, _itx_values(None, kind)))


@router.post("/transactions/new")
def transaction_create(request: Request, data: dict = Depends(form_data), db: Session = Depends(get_db)):
    try:
        svc.save_investment_tx(db, None, **_parse_itx(data))
    except ValidationError as e:
        db.rollback()
        return render(request, "transaction_form.html", 400, **_itx_form_ctx(db, data, error=str(e)))
    return redirect("/portfolio/transactions")


@router.get("/transactions/{tx_id}/edit")
def transaction_edit(request: Request, tx_id: int, db: Session = Depends(get_db)):
    tx = get_or_404(db, InvestmentTransaction, tx_id)
    return render(request, "transaction_form.html", **_itx_form_ctx(db, _itx_values(tx), tx=tx))


@router.post("/transactions/{tx_id}/edit")
def transaction_update(request: Request, tx_id: int, data: dict = Depends(form_data), db: Session = Depends(get_db)):
    tx = get_or_404(db, InvestmentTransaction, tx_id)
    try:
        svc.save_investment_tx(db, tx, **_parse_itx(data))
    except ValidationError as e:
        db.rollback()
        return render(request, "transaction_form.html", 400, **_itx_form_ctx(db, data, tx=tx, error=str(e)))
    return redirect("/portfolio/transactions")


@router.post("/transactions/{tx_id}/delete")
def transaction_delete(request: Request, tx_id: int, db: Session = Depends(get_db)):
    tx = get_or_404(db, InvestmentTransaction, tx_id)
    try:
        svc.delete_investment_tx(db, tx)
    except ValidationError as e:
        db.rollback()
        return render(request, "transaction_form.html", 400, **_itx_form_ctx(db, _itx_values(tx), tx=tx, error=str(e)))
    return redirect("/portfolio/transactions")


# --------------------------------------------------------------------------------- realized
def _fy_param(fy: str) -> int:
    try:
        n = int(fy)
        if 1990 <= n <= 2100:
            return n
    except ValueError:
        pass
    return fy_year_of(date.today())


def _realized_context(db, year: int):
    start, end = fy_bounds(year)
    pf = svc.build_portfolio(db)
    sales = [s for s in pf.sales if start <= s.date <= end]
    dividends = sum((t.amount for t in svc.list_investment_txs(db, svc.InvFilter(start=start, end=end, kind="dividend"))), ZERO)
    return pf, sales, dividends


@router.get("/realized")
def realized(request: Request, fy: str = "", db: Session = Depends(get_db)):
    year = _fy_param(fy)
    error, pf, sales, dividends = None, None, [], ZERO
    try:
        pf, sales, dividends = _realized_context(db, year)
    except EngineError as e:
        error = str(e)
    return render(
        request, "realized.html", tab="realized", fy=year, fy_label=fy_label(year), prev_fy=year - 1, next_fy=year + 1,
        sales=sorted(sales, key=lambda s: (s.date, s.event_id), reverse=True), instruments=pf.instruments if pf else {},
        proceeds=sum((s.proceeds for s in sales), ZERO), cost=sum((s.cost for s in sales), ZERO),
        pnl_total=sum((s.pnl for s in sales), ZERO), dividends=dividends, error=error,
    )


@router.get("/realized/export.csv")
def realized_csv(fy: str = "", db: Session = Depends(get_db)):
    year = _fy_param(fy)
    try:
        pf, sales, _div = _realized_context(db, year)
    except EngineError:
        return redirect("/portfolio/realized")
    headers = ["Sale date", "Symbol", "Instrument", "Quantity", "Proceeds (after fees)", "Average cost", "Realized P&L"]
    rows = [[s.date.isoformat(), pf.instruments[s.instrument_id].symbol, pf.instruments[s.instrument_id].name,
             format(s.quantity.normalize(), "f"), str(s.proceeds), str(s.cost), str(s.pnl)]
            for s in sorted(sales, key=lambda s: (s.date, s.event_id))]
    return _attachment(to_csv(headers, rows), f"realized-fy{year}.csv", "text/csv; charset=utf-8")
