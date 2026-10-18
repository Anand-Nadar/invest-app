import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import AppMeta


def test_can_write_and_read_app_meta(db):
    db.add(AppMeta(key="installed", value="yes"))
    db.commit()
    row = db.get(AppMeta, "installed")
    assert row.value == "yes"
    assert row.created_at is not None


def test_primary_key_is_enforced(db):
    db.add(AppMeta(key="k", value="1"))
    db.commit()
    db.add(AppMeta(key="k", value="2"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_foreign_keys_pragma_enabled(db):
    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
