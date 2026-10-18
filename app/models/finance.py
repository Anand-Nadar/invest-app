"""Phase 2 models: accounts, categories, transactions, assets, liabilities, net-worth snapshots.

Money is stored as Numeric(18, 2). A transaction's `amount` is always positive; its effect on
balances is derived from `kind`:
    income   -> + amount on `account`
    expense  -> - amount on `account`
    transfer -> - amount on `account`, + amount on `to_account` (never counted as income/expense)
"""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

MONEY = Numeric(18, 2)

ACCOUNT_TYPES = [
    ("bank", "Bank account"),
    ("cash", "Cash"),
    ("wallet", "Wallet / UPI"),
    ("credit_card", "Credit card"),
    ("other", "Other"),
]
ASSET_TYPES = [
    ("real_estate", "Real estate"),
    ("gold", "Gold"),
    ("vehicle", "Vehicle"),
    ("retirement", "EPF / PPF / NPS"),
    ("fixed_deposit", "Fixed deposit"),
    ("other", "Other"),
]
LIABILITY_TYPES = [
    ("home_loan", "Home loan"),
    ("car_loan", "Car loan"),
    ("personal_loan", "Personal loan"),
    ("education_loan", "Education loan"),
    ("credit_card", "Credit card dues"),
    ("other", "Other"),
]
PAYMENT_METHODS = ["UPI", "Debit card", "Credit card", "Net banking", "Cash", "Cheque", "Auto-debit", "Other"]
TX_KINDS = ("income", "expense", "transfer")
CATEGORY_KINDS = ("income", "expense")


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("name", name="uq_accounts_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20))
    opening_balance: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))
    opening_date: Mapped[dt.date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(String(500))


class Category(TimestampMixin, Base):
    """Two levels only: a top-level category and optional subcategories."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(10))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))

    parent: Mapped[Optional["Category"]] = relationship(
        "Category", remote_side="Category.id", back_populates="children"
    )
    children: Mapped[list["Category"]] = relationship(
        "Category", back_populates="parent", order_by="Category.name"
    )


class Transaction(TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("(kind = 'transfer') = (to_account_id IS NOT NULL)", name="transfer_shape"),
        CheckConstraint("to_account_id IS NULL OR to_account_id <> account_id", name="transfer_distinct"),
        Index("ix_transactions_date", "date"),
        Index("ix_transactions_account_id", "account_id"),
        Index("ix_transactions_category_id", "category_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(10))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    to_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    merchant: Mapped[str | None] = mapped_column(String(150))
    payment_method: Mapped[str | None] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(String(500))

    account: Mapped[Account] = relationship("Account", foreign_keys="Transaction.account_id", lazy="joined")
    to_account: Mapped[Account | None] = relationship(
        "Account", foreign_keys="Transaction.to_account_id", lazy="joined"
    )
    category: Mapped[Category | None] = relationship("Category", lazy="joined")


class Asset(TimestampMixin, Base):
    """Non-market assets valued manually (property, gold, EPF...). Stocks/funds arrive in Phase 3."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(30))
    value: Mapped[Decimal] = mapped_column(MONEY)
    as_of: Mapped[dt.date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(500))


class Liability(TimestampMixin, Base):
    __tablename__ = "liabilities"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(30))
    outstanding: Mapped[Decimal] = mapped_column(MONEY)
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    as_of: Mapped[dt.date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(500))


class NetWorthSnapshot(TimestampMixin, Base):
    __tablename__ = "net_worth_snapshots"
    __table_args__ = (UniqueConstraint("snapshot_date", name="uq_net_worth_snapshots_snapshot_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[dt.date] = mapped_column(Date)
    accounts_total: Mapped[Decimal] = mapped_column(MONEY)
    other_assets: Mapped[Decimal] = mapped_column(MONEY)
    liabilities: Mapped[Decimal] = mapped_column(MONEY)
    net_worth: Mapped[Decimal] = mapped_column(MONEY)
