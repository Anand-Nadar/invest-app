"""Phase 3 models: instruments, prices and investment transactions.

Cash for buys, sells, dividends and fees is booked against a Phase 2 Account (type "brokerage" is
recommended), so account balances stay the single source of truth for cash.
"""
import datetime as dt
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.finance import MONEY, Account

INSTRUMENT_TYPES = [("equity", "Stock"), ("etf", "ETF"), ("mutual_fund", "Mutual fund")]
EXCHANGES = [("NSE", "NSE"), ("BSE", "BSE"), ("MF", "Mutual fund (AMFI)")]
INV_KINDS = ("buy", "sell", "dividend", "fee", "split", "bonus")
INV_KIND_LABELS = {"buy": "Buy", "sell": "Sell", "dividend": "Dividend", "fee": "Fee",
                   "split": "Stock split", "bonus": "Bonus shares"}


class Instrument(TimestampMixin, Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("exchange", "symbol", name="uq_instruments_exchange_symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40))
    exchange: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(150))
    type: Mapped[str] = mapped_column(String(20))
    sector: Mapped[str | None] = mapped_column(String(60))
    isin: Mapped[str | None] = mapped_column(String(12))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Price(TimestampMixin, Base):
    """One price per instrument per day. Entered by hand in Phase 3; Phase 4 fills it automatically."""

    __tablename__ = "prices"
    __table_args__ = (
        UniqueConstraint("instrument_id", "date", name="uq_prices_instrument_id_date"),
        CheckConstraint("price > 0", name="price_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    date: Mapped[dt.date] = mapped_column(Date)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    source: Mapped[str] = mapped_column(String(30), default="manual")


class InvestmentTransaction(TimestampMixin, Base):
    __tablename__ = "investment_transactions"
    __table_args__ = (
        CheckConstraint("kind IN ('buy','sell','dividend','fee','split','bonus')", name="kind_valid"),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="quantity_positive"),
        CheckConstraint("price IS NULL OR price > 0", name="price_positive"),
        CheckConstraint("fees >= 0", name="fees_non_negative"),
        CheckConstraint("amount IS NULL OR amount > 0", name="amount_positive"),
        CheckConstraint(
            "(ratio_new IS NULL OR ratio_new > 0) AND (ratio_old IS NULL OR ratio_old > 0)", name="ratio_positive"
        ),
        Index("ix_investment_transactions_date", "date"),
        Index("ix_investment_transactions_instrument_id", "instrument_id"),
        Index("ix_investment_transactions_account_id", "account_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(10))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id", ondelete="RESTRICT"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    fees: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
    ratio_new: Mapped[int | None] = mapped_column(Integer)
    ratio_old: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(String(500))

    instrument: Mapped[Instrument | None] = relationship("Instrument", lazy="joined")
    account: Mapped[Account | None] = relationship("Account", lazy="joined")
