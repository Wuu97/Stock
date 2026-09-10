"""Application service that settles frozen buy recommendations in rank order."""

from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, Tuple
from uuid import uuid4

from .models import DayBar, FeeModel, OrderIntent
from .settlement import SettlementService


@dataclass(frozen=True)
class SettlementBatchItem:
    recommendation_item_id: str
    ticker: str
    status: str
    reason: str = ""


def settle_frozen_buys(connection, account_id: str, market_snapshot_id: str, trade_date: date,
                       next_trading_date: date, shares_per_recommendation: int,
                       fee: FeeModel) -> Tuple[SettlementBatchItem, ...]:
    """Create and settle one buy intent per frozen recommendation, without duplicate orders."""
    if next_trading_date <= trade_date:
        raise ValueError("next_trading_date must be after trade_date")
    if shares_per_recommendation <= 0 or shares_per_recommendation % 100:
        raise ValueError("shares_per_recommendation must be a positive board lot")
    account = connection.execute("SELECT 1 FROM sim_accounts WHERE account_id = ?", [account_id]).fetchone()
    if account is None:
        raise ValueError("simulation account is missing")
    rows = connection.execute(
        "SELECT i.item_id, i.ticker FROM recommendation_items i "
        "JOIN recommendation_runs r ON r.run_id = i.run_id "
        "LEFT JOIN recommendation_run_modes m ON m.run_id = r.run_id "
        "WHERE r.target_trade_date = ? AND r.run_status = 'FROZEN' "
        "AND COALESCE(m.execution_mode, 'PRODUCTION') = 'PRODUCTION' ORDER BY i.rank_order",
        [trade_date],
    ).fetchall()
    bars = _bars_for_trade_date(connection, market_snapshot_id, trade_date)
    service = SettlementService(connection)
    outcomes = []
    for item_id, ticker in rows:
        existing = connection.execute(
            "SELECT intent_id, order_status FROM sim_order_intents WHERE recommendation_item_id = ?", [item_id]
        ).fetchone()
        if existing and existing[1] != "PENDING":
            outcomes.append(SettlementBatchItem(item_id, ticker, "SKIPPED", existing[1]))
            continue
        bar = bars.get(ticker)
        if bar is None:
            outcomes.append(SettlementBatchItem(item_id, ticker, "SKIPPED", "DATA_MISSING_BAR"))
            continue
        intent = OrderIntent(existing[0] if existing else str(uuid4()), account_id, ticker, trade_date,
                             "BUY", shares_per_recommendation, item_id)
        if not existing:
            service.create_intent(intent)
        status = service.settle(intent, bar, next_trading_date, fee)
        outcomes.append(SettlementBatchItem(item_id, ticker, status))
    service.value_day(account_id, trade_date, {ticker: bar.close for ticker, bar in bars.items()})
    return tuple(outcomes)


def settle_pending_sells(connection, account_id: str, market_snapshot_id: str, trade_date: date,
                         next_trading_date: date, fee: FeeModel) -> Tuple[SettlementBatchItem, ...]:
    """Settle due risk sell intents through the same matching and ledger service."""
    if next_trading_date <= trade_date:
        raise ValueError("next_trading_date must be after trade_date")
    rows = connection.execute(
        "SELECT intent_id, ticker, target_shares FROM sim_order_intents "
        "WHERE account_id = ? AND target_trade_date = ? AND direction = 'SELL' "
        "AND order_status = 'PENDING' ORDER BY intent_id",
        [account_id, trade_date],
    ).fetchall()
    bars = _bars_for_trade_date(connection, market_snapshot_id, trade_date)
    service, outcomes = SettlementService(connection), []
    for intent_id, ticker, shares in rows:
        bar = bars.get(ticker)
        if bar is None:
            outcomes.append(SettlementBatchItem(intent_id, ticker, "SKIPPED", "DATA_MISSING_BAR"))
            continue
        status = service.settle(OrderIntent(intent_id, account_id, ticker, trade_date, "SELL", shares),
                                bar, next_trading_date, fee)
        outcomes.append(SettlementBatchItem(intent_id, ticker, status))
    return tuple(outcomes)


def _bars_for_trade_date(connection, market_snapshot_id: str, trade_date: date) -> Dict[str, DayBar]:
    rows = connection.execute(
        "SELECT trade_date, ticker, open, high, low, close, volume, amount, limit_up, limit_down, status "
        "FROM daily_bars WHERE market_snapshot_id = ? AND trade_date = ?", [market_snapshot_id, trade_date]
    ).fetchall()
    return {bar.ticker: bar for bar in (DayBar(*row) for row in rows)}
