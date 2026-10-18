# Invest App

Personal wealth and investment application for the Indian market (FastAPI, SQLAlchemy, SQLite, Alembic).

Built in 8 phases; each phase ships something usable. See the roadmap in `app/roadmap.py`.

| Phase | Status |
| --- | --- |
| 1 Foundation & setup | Complete |
| 2 Personal finance & wealth | Complete |
| 3 Investment portfolio | Complete |
| 4 Market data integration | Next |
| 5-8 | Planned |

## Setup

```bash
cd /home/dell/workspace/invest-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # optional
alembic upgrade head        # creates data/invest.db
```

## Run

```bash
python run.py
```

Open http://127.0.0.1:8100. Health check: http://127.0.0.1:8100/health.
Change the port with `INVEST_PORT=9000 python run.py` or in `.env`.
In VS Code use the "Invest App (uvicorn, reload)" launch configuration.

## Phase 2 features (Finance)

- Accounts (bank, cash, wallet, credit card) with balances derived from the ledger
- Income, expense and transfer transactions; merchants, payment methods, notes
- Categories with subcategories (starter set is seeded by the migration)
- Filters (date presets incl. Indian financial year, type, account, category, text), pagination
- CSV and Excel export of transactions and the monthly report
- Assets, liabilities, net worth and dated net-worth snapshots
- Monthly and financial-year reports with category breakdowns

Rules: amounts are `Decimal`; **transfers never count as income or expense**; a transaction can't be dated
before its account's opening date; accounts with transactions can be deactivated but not deleted.

## Phase 3 features (Portfolio)

- Instruments: NSE/BSE stocks, ETFs and mutual funds, with optional sector and ISIN
- Transactions: buy, sell, dividend, account fee, stock split and bonus shares
- Holdings with quantity, average cost, cost basis, market value, unrealized and realized P&L, dividends, XIRR
- Cash for every investment transaction moves through a Finance account (type "Brokerage / demat"),
  so account balances and net worth stay consistent
- Asset allocation by asset class (including cash) and by sector
- Realized P&L by Indian financial year, CSV export; transaction exports to CSV/Excel
- Prices are entered by hand (bulk "Update prices" page) until Phase 4 adds automatic data

Rules:
- Cost basis is the **weighted-average** method; buying fees are part of cost, selling fees reduce proceeds.
- Every save, edit and delete replays the whole history and is rejected if it becomes impossible
  (e.g. selling units you don't hold, or deleting a buy that a later sell depends on).
- Same-day ordering: splits/bonus first, then buys, then sells.
- Holdings without a price are valued at cost and flagged; prices older than 7 days are flagged.
- Investment dividends are shown in Portfolio and are not counted as Finance "income".
- Capital gains tax in India is normally FIFO with holding periods; the numbers here are for performance tracking.

## Test

```bash
pytest
```

## Database migrations

```bash
alembic revision --autogenerate -m "add accounts"   # after changing models
alembic upgrade head
alembic downgrade -1
```

`tests/test_migrations.py` fails if a model changes without a matching migration.

## Project layout

```
app/
  main.py          app factory, error handlers, logging on startup
  config.py        settings (INVEST_* env vars / .env)
  database.py      engine + session dependency
  models/          SQLAlchemy models (conventions in models/base.py)
  routers/         HTTP routes
  services/        business logic (calculations go here, fully unit-tested)
  templates/       Jinja2 pages
  static/          CSS
alembic/           migrations
tests/             pytest suite
data/              SQLite database (git-ignored)
logs/              app.log (git-ignored)
```

## Private data

`data/`, `logs/`, `.env`, database files, CSV and Excel files are git-ignored. Never commit real financial records.
