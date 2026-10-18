# Invest App

Personal wealth and investment application for the Indian market (FastAPI, SQLAlchemy, SQLite, Alembic).

Built in 8 phases; each phase ships something usable. See the roadmap in `app/roadmap.py`.

| Phase | Status |
| --- | --- |
| 1 Foundation & setup | Complete |
| 2 Personal finance & wealth | Next |
| 3-8 | Planned |

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
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. Health check: http://127.0.0.1:8000/health.
In VS Code use the "Invest App (uvicorn, reload)" launch configuration.

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
