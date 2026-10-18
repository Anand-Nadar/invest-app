"""Database engine and session handling (SQLAlchemy 2.x, SQLite by default)."""
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def make_engine(url: str, **kwargs) -> Engine:
    """Create an engine. For SQLite: enforce foreign keys and use WAL for file databases."""
    url_obj = make_url(url)
    is_sqlite = url_obj.get_backend_name() == "sqlite"
    connect_args = kwargs.pop("connect_args", {})

    if is_sqlite:
        connect_args.setdefault("check_same_thread", False)
        if url_obj.database and url_obj.database != ":memory:":
            Path(url_obj.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, connect_args=connect_args, **kwargs)

    if is_sqlite:
        file_db = bool(url_obj.database) and url_obj.database != ":memory:"

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if file_db:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
