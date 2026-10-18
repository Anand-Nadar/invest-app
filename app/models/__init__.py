"""Import every model here so Alembic autogenerate can see them."""
from app.models.app_meta import AppMeta
from app.models.base import Base
from app.models.finance import Account, Asset, Category, Liability, NetWorthSnapshot, Transaction

__all__ = [
    "Base", "AppMeta", "Account", "Asset", "Category", "Liability", "NetWorthSnapshot", "Transaction",
]
