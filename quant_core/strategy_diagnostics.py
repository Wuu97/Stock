"""Ledger-derived diagnostics for comparing research strategies without changing them."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from .experiments import BenchmarkClose


@dataclass(frozen=True)
class RegimeConfig:
    lookback_days: int = 60
    threshold: Decimal = Decimal("0.05")

    def __post_init__(self) -> None:
        if self.lookback_days <= 0 or self.threshold <= 0:
            raise ValueError("regime configuration must be positive")


def calculate_strategy_diagnostics(connection, account_id: str, benchmark: Iterable[BenchmarkClose],
                                   regime: RegimeConfig = RegimeConfig()) -> dict:
    """Return comparable, persisted-fact diagnostics for one completed experiment."""
    nav_rows = connection.execute(
        "SELECT trade_date, total_equity FROM sim_nav_daily WHERE account_id = ? ORDER BY trade_date", [account_id]
    ).fetchall()
    if len(nav_rows) < 2:
        raise ValueError("strategy diagnostics need at least two NAV observations")
    benchmark_by_date = {row.trade_date: row.close for row in benchmark}
    dates = [row[0] for row in nav_rows]
    missing = [day for day in dates if day not in benchmark_by_date]
    if missing:
        raise ValueError("benchmark is incomplete for strategy diagnostics")
    equities = [Decimal(str(row[1])) for row in nav_rows]
    monthly: dict[str, list[tuple[Decimal, Decimal]]] = {}
    regimes: dict[str, list[tuple[Decimal, Decimal]]] = {"UP": [], "DOWN": [], "RANGE": [], "WARMUP": []}
    for index in range(1, len(dates)):
        day_return = (equities[index] / equities[index - 1]) - Decimal("1")
        benchmark_return = (benchmark_by_date[dates[index]] / benchmark_by_date[dates[index - 1]]) - Decimal("1")
        monthly.setdefault(dates[index].strftime("%Y-%m"), []).append((day_return, benchmark_return))
        regimes[_regime_for(index, dates, benchmark_by_date, regime)].append((day_return, benchmark_return))
    costs = connection.execute(
        "SELECT COALESCE(SUM(commission), 0), COALESCE(SUM(stamp_duty), 0), COALESCE(SUM(transfer_fee), 0), "
        "COALESCE(SUM(price_cap_applied), 0) FROM sim_executions WHERE account_id = ?", [account_id]
    ).fetchone()
    exit_rows = connection.execute(
        "SELECT s.trigger_code, COUNT(*), COALESCE(SUM(o.order_status = 'FILLED'), 0), "
        "COALESCE(SUM(o.order_status = 'REJECTED'), 0), COALESCE(SUM(o.order_status = 'PENDING'), 0) "
        "FROM sim_exit_signals s JOIN sim_order_intents o ON o.intent_id = s.intent_id "
        "WHERE s.account_id = ? GROUP BY s.trigger_code ORDER BY s.trigger_code", [account_id]
    ).fetchall()
    return {
        "account_id": account_id,
        "regime_config": {"lookback_days": regime.lookback_days, "threshold": regime.threshold},
        "monthly_excess_returns": {month: _compound(values) - _compound_benchmark(values) for month, values in monthly.items()},
        "regime_excess_returns": {name: _compound(values) - _compound_benchmark(values) for name, values in regimes.items()},
        "regime_observations": {name: len(values) for name, values in regimes.items()},
        "trading_friction": {
            "commission": Decimal(str(costs[0])), "stamp_duty": Decimal(str(costs[1])),
            "transfer_fee": Decimal(str(costs[2])), "price_cap_applied_count": int(costs[3]),
        },
        "exit_outcomes": {
            reason: {"signals": signals, "filled": filled, "rejected": rejected, "pending": pending}
            for reason, signals, filled, rejected, pending in exit_rows
        },
    }


def _regime_for(index: int, dates: list[date], closes: dict[date, Decimal], config: RegimeConfig) -> str:
    if index < config.lookback_days:
        return "WARMUP"
    trailing_return = (closes[dates[index]] / closes[dates[index - config.lookback_days]]) - Decimal("1")
    if trailing_return >= config.threshold:
        return "UP"
    if trailing_return <= -config.threshold:
        return "DOWN"
    return "RANGE"


def _compound(values: list[tuple[Decimal, Decimal]]) -> Decimal:
    result = Decimal("1")
    for value, _ in values:
        result *= Decimal("1") + value
    return result


def _compound_benchmark(values: list[tuple[Decimal, Decimal]]) -> Decimal:
    result = Decimal("1")
    for _, value in values:
        result *= Decimal("1") + value
    return result

