"""Recommendation performance calculations with fixed, explicit daily-bar horizons."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, Iterable, Optional, Sequence
from uuid import uuid4

from .models import DayBar


HORIZONS = (1, 5, 20)


@dataclass(frozen=True)
class EvaluationResult:
    returns: Dict[int, Optional[Decimal]]
    excess_returns: Dict[int, Optional[Decimal]]
    max_close_drawdown: Optional[Decimal]


def evaluate_execution(entry_price: Decimal, execution_date: date, bars: Iterable[DayBar],
                       benchmark: Iterable[DayBar]) -> EvaluationResult:
    """Evaluate from the filled stock price at T open to the close of T+N trading days."""
    stock = sorted((bar for bar in bars if bar.trade_date > execution_date), key=lambda bar: bar.trade_date)
    index = {bar.trade_date: bar for bar in benchmark}
    returns: Dict[int, Optional[Decimal]] = {}
    excess: Dict[int, Optional[Decimal]] = {}
    closing_path = [bar.close for bar in stock[:max(HORIZONS)]]
    for horizon in HORIZONS:
        if len(stock) < horizon:
            returns[horizon] = None
            excess[horizon] = None
            continue
        target = stock[horizon - 1]
        result = (target.close / entry_price) - Decimal("1")
        returns[horizon] = result
        benchmark_bar = index.get(target.trade_date)
        execution_benchmark = index.get(execution_date)
        excess[horizon] = None if not benchmark_bar or not execution_benchmark else result - (
            (benchmark_bar.close / execution_benchmark.open) - Decimal("1")
        )
    max_drawdown = max_close_drawdown([entry_price] + closing_path) if closing_path else None
    return EvaluationResult(returns, excess, max_drawdown)


def max_close_drawdown(prices: Sequence[Decimal]) -> Decimal:
    peak = prices[0]
    worst = Decimal("0")
    for price in prices:
        peak = max(peak, price)
        worst = max(worst, (peak - price) / peak)
    return worst


def summarize(results: Iterable[tuple[bool, EvaluationResult]]) -> dict:
    outcomes = list(results)
    executed = [result for is_executed, result in outcomes if is_executed]
    summary = {"recommendation_count": len(outcomes), "executed_count": len(executed)}
    summary["execution_rate"] = Decimal("0") if not outcomes else Decimal(len(executed)) / len(outcomes)
    for horizon in HORIZONS:
        values = [result.returns[horizon] for result in executed if result.returns[horizon] is not None]
        summary[f"t{horizon}_observed_count"] = len(values)
        summary[f"t{horizon}_win_rate"] = None if not values else Decimal(sum(value > 0 for value in values)) / len(values)
        summary[f"t{horizon}_average_return"] = None if not values else sum(values, Decimal("0")) / len(values)
    return summary


class EvaluationService:
    """Persist one immutable evaluation version for every recommendation in a frozen run."""

    def __init__(self, connection):
        self.connection = connection

    def evaluate_run(self, run_id: str, bars: Iterable[DayBar], benchmark_ticker: str,
                     evaluation_version: str) -> dict:
        all_bars = list(bars)
        benchmark = [bar for bar in all_bars if bar.ticker == benchmark_ticker]
        items = self.connection.execute(
            "SELECT i.item_id, i.ticker, e.deal_price_unadj, e.trade_date "
            "FROM recommendation_items i "
            "LEFT JOIN sim_order_intents o ON o.recommendation_item_id = i.item_id AND o.direction = 'BUY' "
            "LEFT JOIN sim_executions e ON e.intent_id = o.intent_id "
            "WHERE i.run_id = ? ORDER BY i.rank_order", [run_id]
        ).fetchall()
        if not items:
            raise ValueError("run has no recommendation items")
        now = datetime.now(timezone.utc)
        results = []
        for item_id, ticker, entry_price, execution_date in items:
            is_executed = entry_price is not None
            result = evaluate_execution(Decimal(str(entry_price)), execution_date,
                                        [bar for bar in all_bars if bar.ticker == ticker], benchmark) if is_executed else EvaluationResult({}, {}, None)
            self.connection.execute(
                "INSERT INTO performance_evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [str(uuid4()), item_id, evaluation_version, is_executed,
                 result.returns.get(1), result.excess_returns.get(1), result.returns.get(5), result.excess_returns.get(5),
                 result.returns.get(20), result.excess_returns.get(20), result.max_close_drawdown, now],
            )
            results.append((is_executed, result))
        return summarize(results)
