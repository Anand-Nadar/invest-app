"""Phase 2 pages: overview, transactions, accounts, categories, net worth, reports, exports."""
import logging
import math
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import forms
from app.config import get_settings
from app.database import get_db
from app.models.finance import (
    ACCOUNT_TYPES, ASSET_TYPES, LIABILITY_TYPES, PAYMENT_METHODS, TX_KINDS,
    Account, Asset, Category, Liability, Transaction,
)
from app.roadmap import NAV_PHASES, PHASES
from app.routers.common import (
    XLSX_TYPE, attachment as _attachment, form_data, get_or_404, redirect, safe_date as _safe_date,
    safe_int as _safe_int,
)
from app.services import finance as svc
from app.services.charts import line_chart
from app.services.errors import ValidationError
from app.services.exports import to_csv, to_xlsx
from app.services.periods import (
    PRESETS, fy_bounds, fy_label, fy_year_of, month_bounds, resolve_range, shift_month,
)
from app.templating import templates

log = logging.getLogger(__name__)
router = APIRouter(prefix="/finance", include_in_schema=False)
PER_PAGE = 50


# ------------------------------------------------------------------------------ helpers
def render(request: Request, template: str, status_code: int = 200, **ctx):
    base = {"settings": get_settings(), "nav": NAV_PHASES, "phases": PHASES, "active": "finance"}
    return templates.TemplateResponse(request, f"finance/{template}", {**base, **ctx}, status_code=status_code)


# -------------------------------------------------------------------------- overview
@router.get("")
def overview(request: Request, month: str = "", db: Session = Depends(get_db)):
    today = date.today()
    first = _safe_date(f"{month}-01") if month else None
    first = (first or today).replace(day=1)
    start, end = month_bounds(first)
    nw = svc.net_worth(db)
    return render(
        request, "overview.html", tab="overview",
        month_first=first, prev_month=shift_month(first, -1).strftime("%Y-%m"),
        next_month=shift_month(first, 1).strftime("%Y-%m"),
        is_current_month=first == today.replace(day=1),
        summary=svc.period_summary(db, start, end), nw=nw,
        top_expenses=svc.category_breakdown(db, start, end, "expense")[:6],
        recent=svc.list_transactions(db)[:8],
        has_accounts=bool(nw.account_rows),
    )


# ------------------------------------------------------------------------- accounts
def _account_values(a: Account | None = None) -> dict:
    if a is None:
        return {"type": "bank", "opening_balance": "0", "opening_date": date.today().isoformat()}
    return {"name": a.name, "type": a.type, "opening_balance": str(a.opening_balance),
            "opening_date": a.opening_date.isoformat(), "notes": a.notes or ""}


def _parse_account(data: dict) -> dict:
    return dict(
        name=forms.clean_text(data.get("name"), "Name", 100, required=True),
        type=forms.parse_choice(data.get("type"), "Account type", ACCOUNT_TYPES),
        opening_balance=forms.parse_money(data.get("opening_balance") or "0", "Opening balance",
                                          allow_negative=True, allow_zero=True),
        opening_date=forms.parse_date(data.get("opening_date"), "Opening date"),
        notes=forms.clean_text(data.get("notes"), "Notes", 500),
    )


@router.get("/accounts")
def accounts(request: Request, db: Session = Depends(get_db)):
    return render(request, "accounts.html", tab="accounts", nw=svc.net_worth(db))


@router.get("/accounts/new")
def account_new(request: Request):
    return render(request, "account_form.html", tab="accounts", values=_account_values(), account=None,
                  account_types=ACCOUNT_TYPES, error=None)


@router.post("/accounts/new")
def account_create(request: Request, data: dict = Depends(form_data), db: Session = Depends(get_db)):
    try:
        svc.create_account(db, **_parse_account(data))
    except ValidationError as e:
        return render(request, "account_form.html", 400, tab="accounts", values=data, account=None,
                      account_types=ACCOUNT_TYPES, error=str(e))
    return redirect("/finance/accounts")


@router.get("/accounts/{account_id}/edit")
def account_edit(request: Request, account_id: int, db: Session = Depends(get_db)):
    acc = get_or_404(db, Account, account_id)
    return render(request, "account_form.html", tab="accounts", values=_account_values(acc), account=acc,
                  account_types=ACCOUNT_TYPES, error=None)


@router.post("/accounts/{account_id}/edit")
def account_update(request: Request, account_id: int, data: dict = Depends(form_data),
                   db: Session = Depends(get_db)):
    acc = get_or_404(db, Account, account_id)
    try:
        svc.update_account(db, acc, **_parse_account(data))
    except ValidationError as e:
        db.rollback()
        return render(request, "account_form.html", 400, tab="accounts", values=data, account=acc,
                      account_types=ACCOUNT_TYPES, error=str(e))
    return redirect("/finance/accounts")


@router.post("/accounts/{account_id}/toggle")
def account_toggle(account_id: int, db: Session = Depends(get_db)):
    acc = get_or_404(db, Account, account_id)
    svc.set_account_active(db, acc, not acc.is_active)
    return redirect("/finance/accounts")


@router.post("/accounts/{account_id}/delete")
def account_delete(request: Request, account_id: int, db: Session = Depends(get_db)):
    acc = get_or_404(db, Account, account_id)
    try:
        svc.delete_account(db, acc)
    except ValidationError as e:
        return render(request, "accounts.html", 400, tab="accounts", nw=svc.net_worth(db), error=str(e))
    return redirect("/finance/accounts")


# ----------------------------------------------------------------------- categories
@router.get("/categories")
def categories(request: Request, db: Session = Depends(get_db), error: str | None = None):
    return render(request, "categories.html", tab="categories", error=error, values={},
                  income=svc.top_level_categories(db, "income"), expense=svc.top_level_categories(db, "expense"))


def _categories_error(request, db, message, data=None):
    return render(request, "categories.html", 400, tab="categories", error=message, values=data or {},
                  income=svc.top_level_categories(db, "income"), expense=svc.top_level_categories(db, "expense"))


@router.post("/categories")
def category_create(request: Request, data: dict = Depends(form_data), db: Session = Depends(get_db)):
    try:
        parent_id = forms.parse_optional_int(data.get("parent_id"), "Parent category")
        kind = forms.parse_choice(data.get("kind"), "Type", ("income", "expense"))
        if parent_id:  # a subcategory inherits its parent's type
            parent = get_or_404(db, Category, parent_id)
            kind = parent.kind
        svc.create_category(db, name=forms.clean_text(data.get("name"), "Name", 100, required=True),
                            kind=kind, parent_id=parent_id)
    except ValidationError as e:
        db.rollback()
        return _categories_error(request, db, str(e), data)
    return redirect("/finance/categories")


@router.post("/categories/{category_id}/rename")
def category_rename(request: Request, category_id: int, data: dict = Depends(form_data),
                    db: Session = Depends(get_db)):
    cat = get_or_404(db, Category, category_id)
    try:
        svc.rename_category(db, cat, forms.clean_text(data.get("name"), "Name", 100, required=True))
    except ValidationError as e:
        db.rollback()
        return _categories_error(request, db, str(e))
    return redirect("/finance/categories")


@router.post("/categories/{category_id}/delete")
def category_delete(request: Request, category_id: int, db: Session = Depends(get_db)):
    cat = get_or_404(db, Category, category_id)
    try:
        svc.delete_category(db, cat)
    except ValidationError as e:
        return _categories_error(request, db, str(e))
    return redirect("/finance/categories")


# ---------------------------------------------------------------------- transactions
@dataclass
class TxView:
    filter: svc.TxFilter
    preset: str
    start_raw: str
    end_raw: str
    params: dict


def tx_view(period: str = "this_month", start: str = "", end: str = "", kind: str = "",
            account_id: str = "", category_id: str = "", q: str = "") -> TxView:
    s, e, preset = resolve_range(period, date.today(), _safe_date(start), _safe_date(end))
    f = svc.TxFilter(start=s, end=e, kind=kind if kind in TX_KINDS else None,
                     account_id=_safe_int(account_id), category_id=_safe_int(category_id), q=q.strip() or None)
    params = {"period": preset, "kind": f.kind, "account_id": f.account_id,
              "category_id": f.category_id, "q": f.q}
    if preset == "custom":
        params.update(start=start, end=end)
    return TxView(f, preset, start, end, {k: v for k, v in params.items() if v not in (None, "")})


def _tx_form_ctx(db, values, tx=None, error=None):
    return dict(tab="transactions", values=values, tx=tx, error=error, kinds=TX_KINDS,
                accounts=svc.list_accounts(db, active_only=tx is None),
                categories=svc.category_options(db), payment_methods=PAYMENT_METHODS)


def _tx_values(tx: Transaction | None, kind: str = "expense") -> dict:
    if tx is None:
        return {"kind": kind if kind in TX_KINDS else "expense", "date": date.today().isoformat()}
    return {"kind": tx.kind, "date": tx.date.isoformat(), "amount": str(tx.amount),
            "account_id": str(tx.account_id), "to_account_id": str(tx.to_account_id or ""),
            "category_id": str(tx.category_id or ""), "merchant": tx.merchant or "",
            "payment_method": tx.payment_method or "", "notes": tx.notes or ""}


def _parse_tx(data: dict) -> dict:
    kind = forms.parse_choice(data.get("kind"), "Type", TX_KINDS)
    pm = forms.clean_text(data.get("payment_method"), "Payment method", 30)
    return dict(
        kind=kind,
        tx_date=forms.parse_date(data.get("date"), "Date"),
        amount=forms.parse_money(data.get("amount"), "Amount"),
        account_id=forms.parse_optional_int(data.get("account_id"), "Account", required=True),
        to_account_id=forms.parse_optional_int(data.get("to_account_id"), "Destination account")
        if kind == "transfer" else None,
        category_id=forms.parse_optional_int(data.get("category_id"), "Category")
        if kind != "transfer" else None,
        merchant=forms.clean_text(data.get("merchant"), "Merchant", 150),
        payment_method=forms.parse_choice(pm, "Payment method", PAYMENT_METHODS) if pm else None,
        notes=forms.clean_text(data.get("notes"), "Notes", 500),
    )


@router.get("/transactions")
def transactions(request: Request, view: TxView = Depends(tx_view), page: int = 1,
                 db: Session = Depends(get_db)):
    rows = svc.list_transactions(db, view.filter)
    income, expenses = svc.filter_totals(rows)
    pages = max(1, math.ceil(len(rows) / PER_PAGE))
    page = min(max(page, 1), pages)
    return render(
        request, "transactions.html", tab="transactions", rows=rows[(page - 1) * PER_PAGE: page * PER_PAGE],
        total=len(rows), income=income, expenses=expenses, page=page, pages=pages, view=view,
        qs=urlencode(view.params), presets=PRESETS, kinds=TX_KINDS, accounts=svc.list_accounts(db),
        categories=svc.category_options(db), has_accounts=bool(svc.list_accounts(db)),
    )


def _tx_export_rows(db, view: TxView):
    headers = ["Date", "Type", "Amount", "Account", "To account", "Category", "Merchant",
               "Payment method", "Notes"]
    rows = [[t.date, t.kind, t.amount, t.account.name, t.to_account.name if t.to_account else "",
             svc.category_label(t.category), t.merchant or "", t.payment_method or "", t.notes or ""]
            for t in svc.list_transactions(db, view.filter)]
    return headers, rows


@router.get("/transactions/export.csv")
def transactions_csv(view: TxView = Depends(tx_view), db: Session = Depends(get_db)):
    headers, rows = _tx_export_rows(db, view)
    for r in rows:
        r[0] = r[0].isoformat()
        r[2] = str(r[2])
    return _attachment(to_csv(headers, rows), "transactions.csv", "text/csv; charset=utf-8")


@router.get("/transactions/export.xlsx")
def transactions_xlsx(view: TxView = Depends(tx_view), db: Session = Depends(get_db)):
    headers, rows = _tx_export_rows(db, view)
    return _attachment(to_xlsx(headers, rows, "Transactions"), "transactions.xlsx", XLSX_TYPE)


@router.get("/transactions/new")
def transaction_new(request: Request, kind: str = "expense", db: Session = Depends(get_db)):
    return render(request, "transaction_form.html", **_tx_form_ctx(db, _tx_values(None, kind)))


@router.post("/transactions/new")
def transaction_create(request: Request, data: dict = Depends(form_data), db: Session = Depends(get_db)):
    try:
        svc.create_transaction(db, **_parse_tx(data))
    except ValidationError as e:
        db.rollback()
        return render(request, "transaction_form.html", 400, **_tx_form_ctx(db, data, error=str(e)))
    return redirect("/finance/transactions?period=all")


@router.get("/transactions/{tx_id}/edit")
def transaction_edit(request: Request, tx_id: int, db: Session = Depends(get_db)):
    tx = get_or_404(db, Transaction, tx_id)
    return render(request, "transaction_form.html", **_tx_form_ctx(db, _tx_values(tx), tx=tx))


@router.post("/transactions/{tx_id}/edit")
def transaction_update(request: Request, tx_id: int, data: dict = Depends(form_data),
                       db: Session = Depends(get_db)):
    tx = get_or_404(db, Transaction, tx_id)
    try:
        svc.update_transaction(db, tx, **_parse_tx(data))
    except ValidationError as e:
        db.rollback()
        return render(request, "transaction_form.html", 400, **_tx_form_ctx(db, data, tx=tx, error=str(e)))
    return redirect("/finance/transactions?period=all")


@router.post("/transactions/{tx_id}/delete")
def transaction_delete(tx_id: int, db: Session = Depends(get_db)):
    svc.delete_transaction(db, get_or_404(db, Transaction, tx_id))
    return redirect("/finance/transactions?period=all")


# --------------------------------------------------------------------------- net worth
WEALTH = {
    "assets": dict(model=Asset, types=ASSET_TYPES, noun="asset", amount_label="Current value"),
    "liabilities": dict(model=Liability, types=LIABILITY_TYPES, noun="liability", amount_label="Outstanding amount"),
}


def _wealth_cfg(kind: str) -> dict:
    if kind not in WEALTH:
        raise HTTPException(status_code=404)
    return WEALTH[kind]


def _wealth_values(kind: str, obj=None) -> dict:
    if obj is None:
        return {"as_of": date.today().isoformat(), "type": WEALTH[kind]["types"][0][0]}
    amount = obj.value if kind == "assets" else obj.outstanding
    v = {"name": obj.name, "type": obj.type, "amount": str(amount), "as_of": obj.as_of.isoformat(),
         "notes": obj.notes or ""}
    if kind == "liabilities":
        v["interest_rate"] = "" if obj.interest_rate is None else str(obj.interest_rate)
    return v


def _wealth_form(request, kind, values, obj=None, error=None, status=200):
    return render(request, "wealth_form.html", status, tab="wealth", kind=kind, obj=obj, values=values,
                  error=error, **{k: v for k, v in WEALTH[kind].items() if k != "model"})


def _save_wealth(db, kind, obj, data):
    common = dict(
        name=forms.clean_text(data.get("name"), "Name", 100, required=True),
        type=forms.parse_choice(data.get("type"), "Type", WEALTH[kind]["types"]),
        as_of=forms.parse_date(data.get("as_of"), "As-of date"),
        notes=forms.clean_text(data.get("notes"), "Notes", 500),
    )
    amount = forms.parse_money(data.get("amount"), WEALTH[kind]["amount_label"], allow_zero=True)
    if kind == "assets":
        return svc.save_asset(db, obj, value=amount, **common)
    return svc.save_liability(db, obj, outstanding=amount,
                              interest_rate=forms.parse_rate(data.get("interest_rate"), "Interest rate"), **common)


@router.get("/wealth")
def wealth(request: Request, db: Session = Depends(get_db)):
    snaps = svc.list_snapshots(db)
    return render(request, "wealth.html", tab="wealth", nw=svc.net_worth(db), snaps=snaps,
                  chart=line_chart([s.net_worth for s in snaps]), asset_types=dict(ASSET_TYPES),
                  liability_types=dict(LIABILITY_TYPES), account_types=dict(ACCOUNT_TYPES))


@router.post("/wealth/snapshot")
def wealth_snapshot(db: Session = Depends(get_db)):
    svc.record_snapshot(db, date.today())
    return redirect("/finance/wealth")


@router.get("/wealth/{kind}/new")
def wealth_new(request: Request, kind: str):
    _wealth_cfg(kind)
    return _wealth_form(request, kind, _wealth_values(kind))


@router.post("/wealth/{kind}/new")
def wealth_create(request: Request, kind: str, data: dict = Depends(form_data), db: Session = Depends(get_db)):
    _wealth_cfg(kind)
    try:
        _save_wealth(db, kind, None, data)
    except ValidationError as e:
        db.rollback()
        return _wealth_form(request, kind, data, error=str(e), status=400)
    return redirect("/finance/wealth")


@router.get("/wealth/{kind}/{item_id}/edit")
def wealth_edit(request: Request, kind: str, item_id: int, db: Session = Depends(get_db)):
    obj = get_or_404(db, _wealth_cfg(kind)["model"], item_id)
    return _wealth_form(request, kind, _wealth_values(kind, obj), obj=obj)


@router.post("/wealth/{kind}/{item_id}/edit")
def wealth_update(request: Request, kind: str, item_id: int, data: dict = Depends(form_data),
                  db: Session = Depends(get_db)):
    obj = get_or_404(db, _wealth_cfg(kind)["model"], item_id)
    try:
        _save_wealth(db, kind, obj, data)
    except ValidationError as e:
        db.rollback()
        return _wealth_form(request, kind, data, obj=obj, error=str(e), status=400)
    return redirect("/finance/wealth")


@router.post("/wealth/{kind}/{item_id}/delete")
def wealth_delete(kind: str, item_id: int, db: Session = Depends(get_db)):
    svc.delete_item(db, get_or_404(db, _wealth_cfg(kind)["model"], item_id))
    return redirect("/finance/wealth")


# ----------------------------------------------------------------------------- reports
def _fy_param(fy: str) -> int:
    try:
        n = int(fy)
        if 1990 <= n <= 2100:
            return n
    except ValueError:
        pass
    return fy_year_of(date.today())


@router.get("/reports")
def reports(request: Request, fy: str = "", db: Session = Depends(get_db)):
    year = _fy_param(fy)
    start, end = fy_bounds(year)
    months = svc.monthly_summaries(db, start, end)
    total = svc.period_summary(db, start, end)
    peak = max([m.income for _d, m in months] + [m.expenses for _d, m in months] + [svc.ZERO]) or 1
    return render(
        request, "reports.html", tab="reports", fy=year, fy_label=fy_label(year), prev_fy=year - 1,
        next_fy=year + 1, months=months, total=total, peak=peak,
        expense_breakdown=svc.category_breakdown(db, start, end, "expense"),
        income_breakdown=svc.category_breakdown(db, start, end, "income"),
    )


def _report_rows(db, year):
    start, end = fy_bounds(year)
    headers = ["Month", "Income", "Expenses", "Net savings", "Savings rate (%)"]
    rows = [[d.strftime("%b %Y"), m.income, m.expenses, m.net_savings,
             m.savings_rate if m.savings_rate is not None else ""] for d, m in svc.monthly_summaries(db, start, end)]
    t = svc.period_summary(db, start, end)
    rows.append([fy_label(year) + " total", t.income, t.expenses, t.net_savings,
                 t.savings_rate if t.savings_rate is not None else ""])
    return headers, rows


@router.get("/reports/export.csv")
def reports_csv(fy: str = "", db: Session = Depends(get_db)):
    year = _fy_param(fy)
    headers, rows = _report_rows(db, year)
    rows = [[str(v) if hasattr(v, "quantize") else v for v in r] for r in rows]
    return _attachment(to_csv(headers, rows), f"report-fy{year}.csv", "text/csv; charset=utf-8")


@router.get("/reports/export.xlsx")
def reports_xlsx(fy: str = "", db: Session = Depends(get_db)):
    year = _fy_param(fy)
    headers, rows = _report_rows(db, year)
    return _attachment(to_xlsx(headers, rows, "Monthly report"), f"report-fy{year}.xlsx", XLSX_TYPE)
