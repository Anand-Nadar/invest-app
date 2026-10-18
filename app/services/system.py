"""System status used by the dashboard and /health."""
import logging
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import BASE_DIR

log = logging.getLogger(__name__)


def alembic_head() -> str | None:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(BASE_DIR / "alembic")))
    return ScriptDirectory.from_config(cfg).get_current_head()


def get_system_status(db: Session) -> dict:
    status = {"database_ok": False, "schema_revision": None, "schema_head": None, "migrations_current": False}
    try:
        db.execute(text("SELECT 1"))
        status["database_ok"] = True
    except Exception:
        log.exception("Database connectivity check failed")
        return status

    try:
        status["schema_head"] = alembic_head()
    except Exception:
        log.exception("Could not read migration head")

    try:
        row = db.execute(text("SELECT version_num FROM alembic_version")).first()
        status["schema_revision"] = row[0] if row else None
    except Exception:
        # Table is missing until `alembic upgrade head` has been run.
        db.rollback()

    status["migrations_current"] = (
        status["schema_revision"] is not None and status["schema_revision"] == status["schema_head"]
    )
    return status
