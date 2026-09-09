"""Immutable strategy-experiment definitions and ledger-derived performance metrics."""

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from math import sqrt
from typing import Iterable, Optional, Sequence

from .models import DayBar
from .portfolio import PortfolioPolicy
from .risk import ExitRule
from .strategy_research import StrategySpec


@dataclass(frozen=True)
class ExperimentSpec:
    strategy: StrategySpec
    portfolio: PortfolioPolicy
    exit_rule: ExitRule
    cost_model_version: str
    start_date: date
    end_date: date
    market_snapshot_ids: tuple[str, ...]
    universe_reference: str
    benchmark_ticker: str
    initial_cash: Decimal

    def __post_init__(self) -> None:
        if self.start_date > self.end_date or not self.market_snapshot_ids or not self.cost_model_version or not self.benchmark_ticker or self.initial_cash <= 0:
            raise ValueError("experiment spec is incomplete")

    def canonical_json(self) -> str:
        payload = {
            "strategy": asdict(self.strategy), "portfolio": _jsonable(asdict(self.portfolio)),
            "exit_rule": _jsonable(asdict(self.exit_rule)), "cost_model_version": self.cost_model_version,
            "start_date": self.start_date.isoformat(), "end_date": self.end_date.isoformat(),
            "market_snapshot_ids": sorted(self.market_snapshot_ids), "universe_reference": self.universe_reference,
            "benchmark_ticker": self.benchmark_ticker,
            "initial_cash": str(self.initial_cash),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ExperimentStore:
    """Append-only persistence for frozen experiment definitions and one result."""

    def __init__(self, connection):
        self.connection = connection

    def create(self, experiment_id: str, account_id: str, spec: ExperimentSpec, created_at: datetime) -> None:
        payload = spec.canonical_json()
        self.connection.execute("INSERT INTO strategy_experiments VALUES (?, ?, ?, ?, ?)", [
            experiment_id, account_id, payload, sha256(payload.encode("utf-8")).hexdigest(), created_at,
        ])

    def store_result(self, experiment_id: str, metrics: dict, created_at: datetime) -> None:
        payload = json.dumps(_jsonable(metrics), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.connection.execute("INSERT INTO strategy_experiment_results VALUES (?, ?, ?, ?)", [
            experiment_id, payload, sha256(payload.encode("utf-8")).hexdigest(), created_at,
        ])


def calculate_metrics(connection, account_id: str, benchmark_bars: Iterable[DayBar]) -> dict:
    """Calculate comparable metrics from persisted NAV, executions and journal facts."""
    nav = connection.execute(
        "SELECT trade_date, total_equity FROM sim_nav_daily WHERE account_id = ? ORDER BY trade_date", [account_id]
    ).fetchall()
    if len(nav) < 2:
        raise ValueError("experiment needs at least two NAV observations")
    equity = [Decimal(str(row[1])) for row in nav]
    returns = [(equity[index] / equity[index - 1]) - Decimal("1") for index in range(1, len(equity))]
    benchmark = {bar.trade_date: bar.close for bar in benchmark_bars}
    dates = [row[0] for row in nav]
    missing = [day for day in dates if day not in benchmark]
    if missing:
        raise ValueError("benchmark is incomplete for experiment NAV dates")
    total_return = (equity[-1] / equity[0]) - Decimal("1")
    benchmark_return = (benchmark[dates[-1]] / benchmark[dates[0]]) - Decimal("1")
    completed_orders, filled_orders, rejected_orders = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(order_status = 'FILLED'), 0), COALESCE(SUM(order_status = 'REJECTED'), 0) "
        "FROM sim_order_intents WHERE account_id = ?", [account_id]
    ).fetchone()
    gross = connection.execute(
        "SELECT COALESCE(SUM(gross_amount), 0) FROM sim_executions WHERE account_id = ?", [account_id]
    ).fetchone()[0]
    pnl_rows = connection.execute(
        "SELECT ref_event_id, SUM(credit_amount - debit_amount) FROM ledger_journal_entries "
        "WHERE account_id = ? AND account_code = '5001' AND ref_event_id IS NOT NULL GROUP BY 1", [account_id]
    ).fetchall()
    holding_rows = connection.execute(
        "SELECT d.shares_deducted, d.trade_date - l.buy_trade_date FROM sim_lot_disposal_events d "
        "JOIN sim_position_lots l ON l.lot_id = d.lot_id WHERE l.account_id = ?", [account_id]
    ).fetchall()
    return {
        "nav_observations": len(nav), "total_return": total_return,
        "annualized_return": _annualized(total_return, len(returns)),
        "benchmark_return": benchmark_return, "excess_return": total_return - benchmark_return,
        "max_drawdown": _max_drawdown(equity), "sharpe": _sharpe(returns),
        "order_count": completed_orders, "filled_order_count": filled_orders, "rejected_order_count": rejected_orders,
        "execution_rate": _ratio(filled_orders, completed_orders), "rejection_rate": _ratio(rejected_orders, completed_orders),
        "turnover": Decimal(str(gross)) / (sum(equity, Decimal("0")) / len(equity)),
        "realized_trade_count": len(pnl_rows), "realized_win_rate": _ratio(sum(value > 0 for _, value in pnl_rows), len(pnl_rows)),
        "average_realized_pnl": None if not pnl_rows else sum((Decimal(str(value)) for _, value in pnl_rows), Decimal("0")) / len(pnl_rows),
        "average_holding_days": _weighted_holding_days(holding_rows),
    }


def _ratio(numerator: int, denominator: int) -> Optional[Decimal]:
    return None if not denominator else Decimal(numerator) / Decimal(denominator)


def _annualized(total_return: Decimal, periods: int) -> Optional[Decimal]:
    if not periods or total_return <= Decimal("-1"):
        return None
    return Decimal(str((float(Decimal("1") + total_return) ** (252 / periods)) - 1))


def _sharpe(returns: Sequence[Decimal]) -> Optional[Decimal]:
    if len(returns) < 2:
        return None
    values = [float(value) for value in returns]
    average = sum(values) / len(values)
    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    return None if variance == 0 else Decimal(str(average / sqrt(variance) * sqrt(252)))


def _max_drawdown(equity: Sequence[Decimal]) -> Decimal:
    peak, drawdown = equity[0], Decimal("0")
    for value in equity:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak)
    return drawdown


def _weighted_holding_days(rows) -> Optional[Decimal]:
    shares = sum(Decimal(str(row[0])) for row in rows)
    return None if not shares else sum(Decimal(str(quantity * days)) for quantity, days in rows) / shares


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
