"""personal finance: accounts, categories, transactions, assets, liabilities, snapshots

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NOW = sa.func.now()


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=NOW, nullable=False),
    ]


SEED = [
    ("income", "Salary", []),
    ("income", "Bonus", []),
    ("income", "Interest", []),
    ("income", "Rental income", []),
    ("income", "Other income", []),
    ("expense", "Housing", ["Rent", "Utilities", "Maintenance"]),
    ("expense", "Food", ["Groceries", "Dining out"]),
    ("expense", "Transport", ["Fuel", "Public transport", "Cab"]),
    ("expense", "Health", []),
    ("expense", "Insurance", []),
    ("expense", "Education", []),
    ("expense", "Shopping", []),
    ("expense", "Entertainment", []),
    ("expense", "Travel", []),
    ("expense", "Loan EMI", []),
    ("expense", "Taxes", []),
    ("expense", "Other expenses", []),
]


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("opening_balance", sa.Numeric(18, 2), nullable=False),
        sa.Column("opening_date", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.String(length=500), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.UniqueConstraint("name", name="uq_accounts_name"),
    )

    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["categories.id"], name="fk_categories_parent_id_categories", ondelete="RESTRICT"
        ),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("to_account_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("merchant", sa.String(length=150), nullable=True),
        sa.Column("payment_method", sa.String(length=30), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_transactions"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="fk_transactions_account_id_accounts", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["to_account_id"], ["accounts.id"], name="fk_transactions_to_account_id_accounts", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], name="fk_transactions_category_id_categories", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_transactions_amount_positive")),
        sa.CheckConstraint(
            "(kind = 'transfer') = (to_account_id IS NOT NULL)", name=op.f("ck_transactions_transfer_shape")
        ),
        sa.CheckConstraint(
            "to_account_id IS NULL OR to_account_id <> account_id", name=op.f("ck_transactions_transfer_distinct")
        ),
    )
    op.create_index("ix_transactions_date", "transactions", ["date"])
    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_category_id", "transactions", ["category_id"])

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("value", sa.Numeric(18, 2), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_assets"),
    )

    op.create_table(
        "liabilities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("outstanding", sa.Numeric(18, 2), nullable=False),
        sa.Column("interest_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_liabilities"),
    )

    op.create_table(
        "net_worth_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("accounts_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("other_assets", sa.Numeric(18, 2), nullable=False),
        sa.Column("liabilities", sa.Numeric(18, 2), nullable=False),
        sa.Column("net_worth", sa.Numeric(18, 2), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_net_worth_snapshots"),
        sa.UniqueConstraint("snapshot_date", name="uq_net_worth_snapshots_snapshot_date"),
    )

    # Starter categories (editable in the app)
    conn = op.get_bind()
    cats = sa.table(
        "categories",
        sa.column("id", sa.Integer), sa.column("name", sa.String),
        sa.column("kind", sa.String), sa.column("parent_id", sa.Integer),
    )
    for kind, name, children in SEED:
        conn.execute(cats.insert().values(name=name, kind=kind, parent_id=None))
        parent_id = conn.execute(
            sa.select(cats.c.id).where(cats.c.name == name, cats.c.kind == kind, cats.c.parent_id.is_(None))
        ).scalar_one()
        for child in children:
            conn.execute(cats.insert().values(name=child, kind=kind, parent_id=parent_id))


def downgrade() -> None:
    op.drop_table("net_worth_snapshots")
    op.drop_table("liabilities")
    op.drop_table("assets")
    op.drop_index("ix_transactions_category_id", table_name="transactions")
    op.drop_index("ix_transactions_account_id", table_name="transactions")
    op.drop_index("ix_transactions_date", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("categories")
    op.drop_table("accounts")
