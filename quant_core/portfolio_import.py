"""Auditable import of a manually recorded opening cash portfolio."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from .ledger import buy_entries, journal_rows
from .lots import money
from .margin import OpeningMarginDebt, create_margin_account, margin_debt_balance
from .settlement import SettlementService


@dataclass(frozen=True)
class OpeningHolding:
    ticker: str
    shares: int
    unit_cost: Decimal

    def __post_init__(self) -> None:
        if not self.ticker or self.shares <= 0 or self.shares % 100 or self.unit_cost <= 0:
            raise ValueError("opening holding requires a ticker, positive board-lot shares, and positive unit cost")


def import_opening_portfolio(connection, account_id: str, account_name: str, cash_balance: Decimal,
                             as_of_trade_date: date, holdings: Iterable[OpeningHolding], source_path: Path,
                             opening_net_assets: Decimal = None) -> str:
    """Create a cash account and fully sellable opening lots from an external snapshot.

    The supplied positions are an opening-state assertion, not invented prior
    executions. Their source artifact hash is retained for later audit.
    """
    holdings = tuple(sorted(holdings, key=lambda item: item.ticker))
    if cash_balance < 0 or not holdings:
        raise ValueError("opening portfolio requires non-negative cash and at least one holding")
    if len({item.ticker for item in holdings}) != len(holdings):
        raise ValueError("opening portfolio has duplicate tickers")
    source_hash = sha256(source_path.read_bytes()).hexdigest()
    invested = money(sum(Decimal(item.shares) * item.unit_cost for item in holdings))
    service = SettlementService(connection)
    initial_net_assets = money(opening_net_assets) if opening_net_assets is not None else money(cash_balance + invested)
    if initial_net_assets <= 0:
        raise ValueError("opening net assets must be positive")
    service.create_account(account_id, account_name, initial_net_assets, as_of_trade_date)
    now, opening_snapshot_id = datetime.now(timezone.utc), str(uuid4())
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute("INSERT INTO sim_account_opening_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", [
            opening_snapshot_id, account_id, as_of_trade_date, cash_balance, str(source_path), source_hash, now,
        ])
        for holding in holdings:
            event_id = str(uuid4())
            gross = money(Decimal(holding.shares) * holding.unit_cost)
            connection.execute("INSERT INTO sim_position_lots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                str(uuid4()), account_id, holding.ticker, event_id, as_of_trade_date, as_of_trade_date,
                holding.shares, holding.unit_cost, now,
            ])
            connection.executemany("INSERT INTO ledger_journal_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                   journal_rows(f"J_OPENING_{event_id}", account_id, as_of_trade_date,
                                                buy_entries(gross, Decimal("0"), Decimal("0"), holding.ticker), now, event_id))
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return opening_snapshot_id


def import_opening_margin_portfolio(connection, account_id: str, account_name: str, cash_balance: Decimal,
                                    as_of_trade_date: date, holdings: Iterable[OpeningHolding], source_path: Path,
                                    annual_financing_rate: Decimal, debts: Iterable[OpeningMarginDebt],
                                    opening_net_assets: Decimal = None) -> str:
    """Import a margin opening state while keeping financing liabilities distinct from cash."""
    holdings = tuple(holdings)
    opening_snapshot_id = import_opening_portfolio(connection, account_id, account_name, cash_balance,
                                                    as_of_trade_date, holdings, source_path)
    connection.execute("BEGIN TRANSACTION")
    try:
        create_margin_account(connection, account_id, annual_financing_rate, debts, as_of_trade_date)
        invested = money(sum(Decimal(item.shares) * item.unit_cost for item in holdings))
        derived_net_assets = money(cash_balance + invested - margin_debt_balance(connection, account_id))
        net_opening_equity = money(opening_net_assets) if opening_net_assets is not None else derived_net_assets
        if net_opening_equity <= 0:
            raise ValueError("margin opening equity must be positive")
        connection.execute("UPDATE sim_accounts SET initial_cash = ? WHERE account_id = ?", [net_opening_equity, account_id])
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return opening_snapshot_id
