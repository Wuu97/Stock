"""Persistence shell for generating immutable, next-open sell intents from daily exit rules."""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable, Tuple
from uuid import uuid4

from .lots import remaining_shares
from .models import DayBar, Disposal, Lot, OrderIntent
from .risk import ExitRule, evaluate_exit
from .settlement import SettlementService


def create_exit_intents(connection, account_id: str, as_of_date: date, next_trading_date: date,
                        bars: Iterable[DayBar], rule: ExitRule) -> Tuple[tuple[str, str], ...]:
    """Persist one immutable sell intent per eligible holding and return (ticker, reason)."""
    if next_trading_date <= as_of_date:
        raise ValueError("next_trading_date must be after as_of_date")
    all_bars = tuple(bars)
    lots, disposals = _load_inventory(connection, account_id)
    outcomes = []
    for ticker in sorted({lot.ticker for lot in lots}):
        ticker_lots = [lot for lot in lots if lot.ticker == ticker]
        sellable = sum(
            remaining_shares(lot, disposals)
            for lot in ticker_lots if lot.available_from_date <= as_of_date
        )
        if sellable <= 0:
            continue
        existing = connection.execute(
            "SELECT 1 FROM sim_order_intents WHERE account_id = ? AND ticker = ? "
            "AND direction = 'SELL' AND order_status = 'PENDING'",
            [account_id, ticker],
        ).fetchone()
        if existing:
            continue
        entry_price = _weighted_cost(ticker_lots, disposals)
        history_start = min(lot.buy_trade_date for lot in ticker_lots)
        signal = evaluate_exit(ticker, entry_price,
                               (bar for bar in all_bars if history_start <= bar.trade_date <= as_of_date), rule)
        if signal is None:
            continue
        intent = OrderIntent(str(uuid4()), account_id, ticker, next_trading_date, "SELL", sellable)
        SettlementService(connection).create_intent(intent)
        connection.execute(
            "INSERT INTO sim_exit_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [str(uuid4()), intent.intent_id, account_id, ticker, as_of_date, next_trading_date,
             signal.reason, signal.reference_close, signal.peak_close, datetime.now(timezone.utc)],
        )
        outcomes.append((ticker, signal.reason))
    return tuple(outcomes)


def _load_inventory(connection, account_id: str) -> tuple[list[Lot], list[Disposal]]:
    lots = [Lot(*row) for row in connection.execute(
        "SELECT lot_id, account_id, ticker, buy_trade_date, available_from_date, orig_shares, unadj_unit_cost "
        "FROM sim_position_lots WHERE account_id = ?", [account_id]
    ).fetchall()]
    disposals = [Disposal(*row) for row in connection.execute(
        "SELECT lot_id, trade_date, shares_deducted FROM sim_lot_disposal_events "
        "WHERE lot_id IN (SELECT lot_id FROM sim_position_lots WHERE account_id = ?)", [account_id]
    ).fetchall()]
    return lots, disposals


def _weighted_cost(lots: Iterable[Lot], disposals: Iterable[Disposal]) -> Decimal:
    quantities = [(remaining_shares(lot, disposals), lot.unit_cost) for lot in lots]
    shares = sum(quantity for quantity, _ in quantities)
    return sum(Decimal(quantity) * cost for quantity, cost in quantities) / Decimal(shares)
