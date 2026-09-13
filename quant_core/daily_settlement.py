"""Application service that settles frozen buy recommendations in rank order."""

from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, Tuple
from uuid import uuid4

from .matching import OpenGapPolicy
from .models import DayBar, FeeModel, OrderIntent
from .portfolio import PortfolioPolicy, construct_buys
from .settlement import SettlementService
from .strategy import Recommendation


@dataclass(frozen=True)
class SettlementBatchItem:
    recommendation_item_id: str
    ticker: str
    status: str
    reason: str = ""


def settle_frozen_buys(connection, account_id: str, market_snapshot_id: str, trade_date: date,
                       next_trading_date: date, shares_per_recommendation: int,
                       fee: FeeModel, portfolio_policy: PortfolioPolicy = None,
                       open_gap_policy: OpenGapPolicy = OpenGapPolicy()) -> Tuple[SettlementBatchItem, ...]:
    """Settle the newest production run once, with ticker de-duplication and budgeted sizes."""
    if next_trading_date <= trade_date:
        raise ValueError("next_trading_date must be after trade_date")
    if shares_per_recommendation <= 0 or shares_per_recommendation % 100:
        raise ValueError("shares_per_recommendation must be a positive board lot")
    account = connection.execute("SELECT 1 FROM sim_accounts WHERE account_id = ?", [account_id]).fetchone()
    if account is None:
        raise ValueError("simulation account is missing")
    selected_run = connection.execute(
        "SELECT r.run_id FROM recommendation_runs r "
        "LEFT JOIN recommendation_run_modes m ON m.run_id = r.run_id "
        "WHERE r.target_trade_date = ? AND r.run_status = 'FROZEN' "
        "AND COALESCE(m.execution_mode, 'PRODUCTION') = 'PRODUCTION' "
        "ORDER BY r.created_at DESC, r.run_id DESC LIMIT 1",
        [trade_date],
    ).fetchone()
    if selected_run is None:
        return ()
    rows = connection.execute(
        "SELECT i.item_id, i.ticker, i.rank_order, i.ref_close_unadj FROM recommendation_items i "
        "WHERE i.run_id = ? ORDER BY i.rank_order, i.item_id", [selected_run[0]]
    ).fetchall()
    unique_rows, seen = [], set()
    for row in rows:
        if row[1] not in seen:
            unique_rows.append(row)
            seen.add(row[1])
    bars = _bars_for_trade_date(connection, market_snapshot_id, trade_date)
    service = SettlementService(connection)
    policy = portfolio_policy or PortfolioPolicy.fixed_shares(shares_per_recommendation)
    held = connection.execute(
        "WITH disposed AS (SELECT lot_id, SUM(shares_deducted) AS shares FROM sim_lot_disposal_events GROUP BY lot_id) "
        "SELECT COUNT(*) FROM (SELECT l.ticker FROM sim_position_lots l LEFT JOIN disposed d ON d.lot_id = l.lot_id "
        "WHERE l.account_id = ? GROUP BY l.ticker HAVING SUM(l.orig_shares - COALESCE(d.shares, 0)) > 0)", [account_id]
    ).fetchone()[0]
    recommendations = [Recommendation(ticker, rank, None, close, {}) for _, ticker, rank, close in unique_rows]
    planned = {item.ticker: item.shares for item in construct_buys(recommendations, policy, service.cash_balance(account_id), fee, held)}
    outcomes = []
    for item_id, ticker, _, reference_close in unique_rows:
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
        shares = planned.get(ticker)
        if not shares:
            outcomes.append(SettlementBatchItem(item_id, ticker, "SKIPPED", "BUDGET_OR_MAX_POSITIONS"))
            continue
        intent = OrderIntent(existing[0] if existing else str(uuid4()), account_id, ticker, trade_date,
                             "BUY", shares, item_id)
        if not existing:
            service.create_intent(intent)
        status = service.settle(intent, bar, next_trading_date, fee, reference_close, open_gap_policy)
        reason = "" if status == "FILLED" else connection.execute(
            "SELECT COALESCE(reject_reason_code, '') FROM sim_order_intents WHERE intent_id = ?", [intent.intent_id]
        ).fetchone()[0]
        outcomes.append(SettlementBatchItem(item_id, ticker, status, reason))
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
