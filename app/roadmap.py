"""Single source of truth for the 8-phase roadmap. Drives navigation and the dashboard."""
from dataclasses import dataclass

DONE, NEXT, PLANNED = "done", "next", "planned"


@dataclass(frozen=True)
class Phase:
    number: int
    slug: str
    title: str
    nav_label: str | None  # None = no page of its own
    summary: str
    status: str


PHASES: list[Phase] = [
    Phase(1, "foundation", "Foundation & setup", None,
          "Application structure, database, configuration and UI shell", DONE),
    Phase(2, "finance", "Personal finance & wealth", "Finance",
          "Income, expenses, savings, assets, liabilities and net worth", DONE),
    Phase(3, "portfolio", "Investment portfolio", "Portfolio",
          "Transactions, holdings, cost basis, profit/loss and dividends", DONE),
    Phase(4, "market", "Market data integration", "Market data",
          "Indian stock symbols, historical prices and data validation", NEXT),
    Phase(5, "research", "Long-term stock research", "Research",
          "Fundamentals, screening criteria, scoring and company reports", PLANNED),
    Phase(6, "swing", "Swing-trading research", "Swing trading",
          "Technical indicators, trade setups, risk controls and backtesting", PLANNED),
    Phase(7, "insights", "Insights, alerts & reports", "Insights",
          "Portfolio analysis, notifications, exports and financial projections", PLANNED),
    Phase(8, "release", "Testing & deployment", None,
          "Security, backups, reliability, documentation and release", PLANNED),
]

NAV_PHASES = [p for p in PHASES if p.nav_label]
BY_SLUG = {p.slug: p for p in PHASES}
