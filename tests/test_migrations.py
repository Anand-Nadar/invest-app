from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.config import BASE_DIR
from app.database import make_engine
from app.services.system import alembic_head


def _cfg(url: str) -> Config:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def test_upgrade_and_downgrade(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    cfg = _cfg(url)

    command.upgrade(cfg, "head")
    eng = make_engine(url)
    tables = set(inspect(eng).get_table_names())
    assert {"app_meta", "alembic_version", "accounts", "transactions", "instruments", "prices",
            "investment_transactions"} <= tables

    command.downgrade(cfg, "base")
    assert "app_meta" not in set(inspect(eng).get_table_names())
    eng.dispose()


def test_models_match_migrations(tmp_path):
    """Fails if a model changed without a new migration."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.models import Base

    url = f"sqlite:///{tmp_path / 'drift.db'}"
    command.upgrade(_cfg(url), "head")
    eng = make_engine(url)
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    eng.dispose()
    assert diff == []


def test_single_migration_head():
    assert alembic_head() is not None


def test_starter_categories_are_seeded(tmp_path):
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path / 'seed.db'}"
    command.upgrade(_cfg(url), "head")
    eng = make_engine(url)
    with eng.connect() as conn:
        names = {r[0] for r in conn.execute(text("SELECT name FROM categories"))}
        parents = conn.execute(
            text("SELECT c.name FROM categories c JOIN categories p ON c.parent_id = p.id WHERE p.name = 'Food'")
        ).scalars().all()
    eng.dispose()
    assert {"Salary", "Housing", "Rent", "Groceries"} <= names
    assert sorted(parents) == ["Dining out", "Groceries"]


def test_snapshot_column_added_by_phase3_and_removed_on_downgrade(tmp_path):
    url = f"sqlite:///{tmp_path / 'cols.db'}"
    cfg = _cfg(url)
    command.upgrade(cfg, "head")
    eng = make_engine(url)
    assert "investments_value" in {c["name"] for c in inspect(eng).get_columns("net_worth_snapshots")}
    command.downgrade(cfg, "0002")
    assert "investments_value" not in {c["name"] for c in inspect(eng).get_columns("net_worth_snapshots")}
    assert "instruments" not in set(inspect(eng).get_table_names())
    eng.dispose()
