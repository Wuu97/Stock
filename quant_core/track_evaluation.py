"""Comparable, non-mutating performance evaluation for production and shadow recommendations."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Mapping, Optional, Sequence
from uuid import uuid4

from .evaluation import EvaluationResult, HORIZONS, _max_close_drawdown, summarize
from .matching import match_next_open
from .models import DayBar, FeeModel, OrderIntent


def evaluate_tracks(connection, run_ids: Sequence[str], bars: Iterable[DayBar], benchmark_ticker: str,
                    fee: FeeModel, evaluation_version: str, shares: int = 100) -> Mapping[str, dict]:
    """Evaluate frozen tracks under one matching and cost model without creating orders."""
    if not run_ids or shares <= 0 or shares % 100:
        raise ValueError("run_ids and a positive board-lot share count are required")
    all_bars = tuple(bars)
    by_ticker, by_key = defaultdict(list), {}
    for bar in all_bars:
        by_ticker[bar.ticker].append(bar)
        by_key[(bar.ticker, bar.trade_date)] = bar
    for values in by_ticker.values():
        values.sort(key=lambda item: item.trade_date)
    placeholders = ",".join("?" for _ in run_ids)
    rows = connection.execute(
        "SELECT i.item_id, i.ticker, r.target_trade_date, COALESCE(m.execution_mode, 'PRODUCTION') "
        "FROM recommendation_items i JOIN recommendation_runs r ON r.run_id = i.run_id "
        "LEFT JOIN recommendation_run_modes m ON m.run_id = r.run_id "
        f"WHERE i.run_id IN ({placeholders}) AND r.run_status = 'FROZEN' ORDER BY r.run_id, i.rank_order",
        list(run_ids),
    ).fetchall()
    now, grouped, rejection_reasons, pending = datetime.now(timezone.utc), defaultdict(list), defaultdict(Counter), defaultdict(int)
    for item_id, ticker, target_date, mode in rows:
        if connection.execute("SELECT 1 FROM performance_evaluations WHERE recommendation_item_id = ? AND evaluation_version = ?", [item_id, evaluation_version]).fetchone():
            continue
        if not _is_mature(target_date, by_ticker.get(ticker, []), by_ticker.get(benchmark_ticker, [])):
            pending[mode] += 1
            continue
        result, execution, reason = _evaluate_item(item_id, ticker, target_date, by_key.get((ticker, target_date)),
                                                    by_ticker.get(ticker, []), by_ticker.get(benchmark_ticker, []), fee, shares)
        connection.execute("INSERT INTO performance_evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            str(uuid4()), item_id, evaluation_version, execution is not None,
            result.returns.get(1), result.excess_returns.get(1), result.returns.get(5), result.excess_returns.get(5),
            result.returns.get(20), result.excess_returns.get(20), result.max_close_drawdown, now,
        ])
        connection.execute("INSERT INTO track_evaluation_details VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            item_id, evaluation_version, target_date, None if execution is None else execution.price,
            None if execution is None else execution.gross_amount + execution.commission + execution.transfer_fee,
            reason, False if execution is None else execution.price_cap_applied, now,
        ])
        grouped[mode].append((execution is not None, result))
        if reason:
            rejection_reasons[mode][reason] += 1
    modes = set(grouped) | set(pending)
    return {mode: {**summarize(grouped[mode]), "pending_maturity_count": pending[mode],
                   "reject_reasons": dict(rejection_reasons[mode])} for mode in modes}


def _is_mature(target_date, stock_bars: Sequence[DayBar], benchmark: Sequence[DayBar]) -> bool:
    """Require a complete T+20 stock and benchmark path before persisting results."""
    future = [bar for bar in stock_bars if bar.trade_date > target_date]
    if len(future) < max(HORIZONS):
        return False
    benchmark_dates = {bar.trade_date for bar in benchmark}
    return target_date in benchmark_dates and all(bar.trade_date in benchmark_dates for bar in future[:max(HORIZONS)])


def _evaluate_item(item_id: str, ticker: str, trade_date, entry_bar: Optional[DayBar], stock_bars: Sequence[DayBar],
                   benchmark: Sequence[DayBar], fee: FeeModel, shares: int):
    if entry_bar is None:
        return EvaluationResult({}, {}, None), None, "DATA_MISSING_BAR"
    execution = match_next_open(OrderIntent("evaluation-" + item_id, "evaluation", ticker, trade_date, "BUY", shares, item_id), entry_bar, fee)
    if not execution.accepted:
        return EvaluationResult({}, {}, None), None, execution.reason
    entry_cash = execution.gross_amount + execution.commission + execution.transfer_fee
    future, benchmark_by_date = [bar for bar in stock_bars if bar.trade_date > trade_date], {bar.trade_date: bar for bar in benchmark}
    returns, excess = {}, {}
    path = [execution.price] + [bar.close for bar in future[:max(HORIZONS)]]
    for horizon in HORIZONS:
        if len(future) < horizon:
            returns[horizon] = excess[horizon] = None
            continue
        target = future[horizon - 1]
        returns[horizon] = _sell_cash(target.close, shares, fee) / entry_cash - Decimal("1")
        benchmark_entry, benchmark_exit = benchmark_by_date.get(trade_date), benchmark_by_date.get(target.trade_date)
        excess[horizon] = None if not benchmark_entry or not benchmark_exit else returns[horizon] - (benchmark_exit.close / benchmark_entry.open - Decimal("1"))
    return EvaluationResult(returns, excess, _max_close_drawdown(path) if len(path) > 1 else None), execution, None


def _sell_cash(price: Decimal, shares: int, fee: FeeModel) -> Decimal:
    gross = price * shares
    return gross - max(gross * fee.commission_rate, fee.min_commission) - gross * fee.stamp_duty_rate - gross * fee.transfer_fee_rate
