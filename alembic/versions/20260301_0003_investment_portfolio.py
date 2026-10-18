"""investment portfolio: instruments, prices, investment transactions; snapshot investments column

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

NOW = sa.func.now()


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=NOW, nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("exchange", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("sector", sa.String(length=60), nullable=True),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_instruments"),
        sa.UniqueConstraint("exchange", "symbol", name="uq_instruments_exchange_symbol"),
    )

    op.create_table(
        "prices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_prices"),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.id"], name="fk_prices_instrument_id_instruments", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("instrument_id", "date", name="uq_prices_instrument_id_date"),
        sa.CheckConstraint("price > 0", name=op.f("ck_prices_price_positive")),
    )

    op.create_table(
        "investment_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("price", sa.Numeric(18, 4), nullable=True),
        sa.Column("fees", sa.Numeric(18, 2), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("ratio_new", sa.Integer(), nullable=True),
        sa.Column("ratio_old", sa.Integer(), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_investment_transactions"),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.id"],
            name="fk_investment_transactions_instrument_id_instruments", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"],
            name="fk_investment_transactions_account_id_accounts", ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "kind IN ('buy','sell','dividend','fee','split','bonus')",
            name=op.f("ck_investment_transactions_kind_valid"),
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity > 0", name=op.f("ck_investment_transactions_quantity_positive")
        ),
        sa.CheckConstraint("price IS NULL OR price > 0", name=op.f("ck_investment_transactions_price_positive")),
        sa.CheckConstraint("fees >= 0", name=op.f("ck_investment_transactions_fees_non_negative")),
        sa.CheckConstraint("amount IS NULL OR amount > 0", name=op.f("ck_investment_transactions_amount_positive")),
        sa.CheckConstraint(
            "(ratio_new IS NULL OR ratio_new > 0) AND (ratio_old IS NULL OR ratio_old > 0)",
            name=op.f("ck_investment_transactions_ratio_positive"),
        ),
    )
    op.create_index("ix_investment_transactions_date", "investment_transactions", ["date"])
    op.create_index("ix_investment_transactions_instrument_id", "investment_transactions", ["instrument_id"])
    op.create_index("ix_investment_transactions_account_id", "investment_transactions", ["account_id"])

    op.add_column(
        "net_worth_snapshots",
        sa.Column("investments_value", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    with op.batch_alter_table("net_worth_snapshots") as batch:
        batch.drop_column("investments_value")
    op.drop_index("ix_investment_transactions_account_id", table_name="investment_transactions")
    op.drop_index("ix_investment_transactions_instrument_id", table_name="investment_transactions")
    op.drop_index("ix_investment_transactions_date", table_name="investment_transactions")
    op.drop_table("investment_transactions")
    op.drop_table("prices")
    op.drop_table("instruments")
