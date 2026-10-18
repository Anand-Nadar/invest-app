"""Import every model here so Alembic autogenerate can see them."""
from app.models.app_meta import AppMeta
from app.models.base import Base

__all__ = ["Base", "AppMeta"]
