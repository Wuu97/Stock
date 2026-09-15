"""Immutable, stage-separated strategy scorecards for reproducible comparisons."""

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from math import sqrt
from typing import Mapping
from uuid import uuid4

from .experiments import ExperimentSpec


EVALUATION_METHOD_VERSION = "strategy_scorecard_v1_60d_rolling_sharpe"
ROLLING_SHARPE_WINDOW_DAYS = 60
MIN_SUFFICIENT_TRADES = 30
FIRST_SCORECARD_STRATEGIES = (
    "historical_momentum_v1", "pure_momentum_v1", "kdj_manual_v1", "macd_manual_v1",
)


@dataclass(frozen=True)
class CompetitionProfile:
    """The invariant environment shared by strategies in one competition."""

    competition_profile_id: str
    competition_profile_version: str
    payload: Mapping[str, object]

    def canonical_json(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def profile_from_experiment(profile_id: str, profile_version: str, spec: ExperimentSpec) -> CompetitionProfile:
    """Extract only strategy-neutral replay conditions from an experiment specification."""
    payload = {
        "universe_reference": spec.universe_reference,
        "portfolio": _jsonable(asdict(spec.portfolio)),
        "exit_rule": _jsonable(asdict(spec.exit_rule)),
        "cost_model_version": spec.cost_model_version,
        "market_snapshot_ids": sorted(spec.market_snapshot_ids),
        "benchmark_ticker": spec.benchmark_ticker,
        "benchmark_data_reference": spec.benchmark_data_reference,
        "initial_cash": str(spec.initial_cash),
        "open_gap_policy": _jsonable(asdict(spec.open_gap_policy)),
    }
    return CompetitionProfile(profile_id, profile_version, payload)


class StrategyScorecardStore:
    def __init__(self, connection):
        self.connection = connection

    def ensure_profile(self, profile: CompetitionProfile, created_at: datetime) -> str:
        payload = profile.canonical_json()
        digest = sha256(payload.encode()).hexdigest()
        existing = self.connection.execute(
            "SELECT profile_sha256 FROM competition_profiles WHERE competition_profile_id = ? AND competition_profile_version = ?",
            [profile.competition_profile_id, profile.competition_profile_version],
        ).fetchone()
        if existing:
            if existing[0] != digest:
                raise ValueError("competition profile version already exists with different immutable content")
            return digest
        self.connection.execute("INSERT INTO competition_profiles VALUES (?, ?, ?, ?, ?)", [
            profile.competition_profile_id, profile.competition_profile_version, payload, digest, created_at,
        ])
        return digest

    def store_experiment_scorecard(self, experiment_id: str, profile: CompetitionProfile,
                                   evaluation_stage: str, as_of_time: datetime,
                                   evaluation_method_version: str = EVALUATION_METHOD_VERSION) -> str:
        if evaluation_stage != "BACKTEST":
            raise ValueError("experiment scorecards must use BACKTEST stage")
        row = self.connection.execute(
            "SELECT e.spec_json, e.spec_sha256, e.account_id, r.metrics_json "
            "FROM strategy_experiments e JOIN strategy_experiment_results r ON r.experiment_id = e.experiment_id "
            "WHERE e.experiment_id = ?", [experiment_id]
        ).fetchone()
        if not row:
            raise ValueError("unknown experiment")
        spec_payload, config_sha256, account_id, base_metrics_json = row
        existing = self.connection.execute(
            "SELECT scorecard_id FROM strategy_scorecards WHERE source_experiment_id = ? AND evaluation_method_version = ?",
            [experiment_id, evaluation_method_version],
        ).fetchone()
        if existing:
            return existing[0]
        spec = json.loads(spec_payload)
        self.ensure_profile(profile, as_of_time)
        metrics = build_backtest_metrics(self.connection, account_id, json.loads(base_metrics_json))
        metrics_json = json.dumps(_jsonable(metrics), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        scorecard_id = str(uuid4())
        self.connection.execute("INSERT INTO strategy_scorecards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            scorecard_id, spec["strategy"]["strategy_id"], spec["strategy"]["strategy_version"],
            profile.competition_profile_id, profile.competition_profile_version, evaluation_stage,
            spec["start_date"], spec["end_date"], as_of_time, evaluation_method_version, config_sha256,
            experiment_id, None, metrics_json, sha256(metrics_json.encode()).hexdigest(),
            sample_status(metrics), as_of_time,
        ])
        return scorecard_id


def build_backtest_metrics(connection, account_id: str, base_metrics: Mapping[str, object]) -> dict:
    """Return comparable NAV and round-trip metrics without changing replay facts.

    Trade-return metrics use realized round-trip return net of allocated entry
    fees and exit fees. NAV metrics retain all costs and unrealized positions.
    """
    base = dict(base_metrics)
    nav = [Decimal(str(row[0])) for row in self_rows(connection,
        "SELECT total_equity FROM sim_nav_daily WHERE account_id = ? ORDER BY trade_date", [account_id])]
    daily_returns = [(nav[i] / nav[i - 1]) - Decimal("1") for i in range(1, len(nav))]
    quality = _trade_quality(connection, account_id)
    order_count, eligible_orders, filled_orders, pending_orders = self_rows(connection,
        "SELECT COUNT(*), COALESCE(SUM(order_status IN ('FILLED', 'REJECTED')), 0), "
        "COALESCE(SUM(order_status = 'FILLED'), 0), COALESCE(SUM(order_status = 'PENDING'), 0) "
        "FROM sim_order_intents WHERE account_id = ?", [account_id])[0]
    signal_count = self_rows(connection,
        "SELECT COUNT(*) FROM sim_order_intents WHERE account_id = ? AND recommendation_item_id IS NOT NULL", [account_id])[0][0]
    base.update(quality)
    base.update({
        "signal_count": int(signal_count), "order_count": int(order_count),
        "eligible_order_count": int(eligible_orders), "filled_order_count": int(filled_orders),
        "pending_order_count": int(pending_orders),
        "fill_rate": None if not eligible_orders else Decimal(str(filled_orders)) / Decimal(str(eligible_orders)),
        "volatility": _annualized_volatility(daily_returns), "sortino": _sortino(daily_returns),
        "rolling_sharpe_window_days": ROLLING_SHARPE_WINDOW_DAYS,
        "rolling_sharpe_std": _rolling_sharpe_std(daily_returns, ROLLING_SHARPE_WINDOW_DAYS),
        "positive_month_ratio": _positive_month_ratio(connection, account_id),
        "worst_period_return": min(daily_returns) if daily_returns else None,
    })
    return base


def sample_status(metrics: Mapping[str, object]) -> str:
    if metrics.get("nav_observations", 0) < 2:
        return "INCOMPLETE"
    if metrics.get("pending_order_count", 0):
        return "INCOMPLETE"
    if metrics.get("trade_count", 0) < MIN_SUFFICIENT_TRADES:
        return "LOW_SAMPLE"
    return "SUFFICIENT"


def leaderboard(connection, competition_profile_id: str, competition_profile_version: str,
                evaluation_stage: str, include_non_sufficient: bool = False) -> list[dict]:
    """Read comparable scorecards only; no cross-profile or cross-stage blending."""
    rows = self_rows(connection, """
        SELECT strategy_id, strategy_version, evaluation_stage, sample_start, sample_end,
               as_of_time, metrics_json, sample_status, scorecard_id
        FROM strategy_scorecards
        WHERE competition_profile_id = ? AND competition_profile_version = ? AND evaluation_stage = ?
        ORDER BY as_of_time DESC, created_at DESC
    """, [competition_profile_id, competition_profile_version, evaluation_stage])
    latest = {}
    for strategy_id, strategy_version, stage, start, end, as_of, metrics_json, status, scorecard_id in rows:
        key = (strategy_id, strategy_version, start, end)
        if key in latest:
            continue
        metrics = json.loads(metrics_json)
        if not include_non_sufficient and status != "SUFFICIENT":
            continue
        latest[key] = {
            "scorecard_id": scorecard_id, "strategy_id": strategy_id, "strategy_version": strategy_version,
            "stage": stage, "sample_start": str(start), "sample_end": str(end), "as_of_time": str(as_of),
            "sample_status": status,
            **{key: metrics.get(key) for key in (
                "trade_count", "total_return", "excess_return", "win_rate", "expectancy", "profit_factor",
                "sharpe", "max_drawdown", "fill_rate", "positive_month_ratio", "rolling_sharpe_std",
            )},
        }
    return sorted(latest.values(), key=lambda row: (
        -(float(row["sharpe"]) if row["sharpe"] is not None else float("-inf")), row["strategy_id"]
    ))


def competition_coverage(connection, competition_profile_id: str, competition_profile_version: str,
                         evaluation_stage: str, expected_strategies=FIRST_SCORECARD_STRATEGIES) -> dict:
    """Report whether a competition has enough comparable entries to judge the scorecard pipeline."""
    rows = self_rows(connection, """
        SELECT strategy_id, strategy_version, sample_status, MAX(as_of_time)
        FROM strategy_scorecards
        WHERE competition_profile_id = ? AND competition_profile_version = ? AND evaluation_stage = ?
        GROUP BY 1, 2, 3
    """, [competition_profile_id, competition_profile_version, evaluation_stage])
    by_strategy = {}
    for strategy_id, version, status, as_of in rows:
        if strategy_id not in by_strategy or str(as_of) > by_strategy[strategy_id]["as_of_time"]:
            by_strategy[strategy_id] = {"strategy_id": strategy_id, "strategy_version": version,
                                        "sample_status": status, "as_of_time": str(as_of)}
    expected = list(expected_strategies)
    missing = [strategy for strategy in expected if strategy not in by_strategy]
    insufficient = [row for strategy, row in by_strategy.items()
                    if strategy in expected and row["sample_status"] != "SUFFICIENT"]
    return {
        "competition_profile_id": competition_profile_id,
        "competition_profile_version": competition_profile_version,
        "evaluation_stage": evaluation_stage,
        "expected_strategies": expected,
        "present": [by_strategy[strategy] for strategy in expected if strategy in by_strategy],
        "missing_strategies": missing,
        "non_sufficient": insufficient,
        "ready_for_first_comparison": not missing and not insufficient,
    }


def _trade_quality(connection, account_id: str) -> dict:
    sale_rows = self_rows(connection, """
        SELECT ref_event_id,
               SUM(CASE WHEN account_code = '5001' THEN credit_amount - debit_amount ELSE 0 END) AS gross_pnl,
               MAX(CASE WHEN account_code = '1101' THEN credit_amount ELSE 0 END) AS relieved_cost,
               SUM(CASE WHEN account_code IN ('6001', '6002', '6003') THEN debit_amount - credit_amount ELSE 0 END) AS exit_fees
        FROM ledger_journal_entries WHERE account_id = ? AND ref_event_id IS NOT NULL GROUP BY ref_event_id
        HAVING MAX(CASE WHEN account_code = '1101' THEN credit_amount ELSE 0 END) > 0
    """, [account_id])
    buy_fees = {event_id: Decimal(str(fee)) for event_id, fee in self_rows(connection, """
        SELECT ref_event_id, SUM(debit_amount - credit_amount) FROM ledger_journal_entries
        WHERE account_id = ? AND account_code IN ('6001', '6003') AND ref_event_id IS NOT NULL GROUP BY ref_event_id
    """, [account_id])}
    allocations = self_rows(connection, """
        SELECT d.sell_event_id, l.buy_event_id, d.shares_deducted, l.orig_shares
        FROM sim_lot_disposal_events d JOIN sim_position_lots l ON l.lot_id = d.lot_id
        WHERE l.account_id = ?
    """, [account_id])
    entry_fees = {}
    for sell_event, buy_event, shares, original_shares in allocations:
        entry_fees[sell_event] = entry_fees.get(sell_event, Decimal("0")) + buy_fees.get(buy_event, Decimal("0")) * Decimal(str(shares)) / Decimal(str(original_shares))
    returns = []
    for sell_event, gross_pnl, relieved_cost, exit_fees in sale_rows:
        entry_fee = entry_fees.get(sell_event, Decimal("0"))
        denominator = Decimal(str(relieved_cost)) + entry_fee
        returns.append((Decimal(str(gross_pnl)) - Decimal(str(exit_fees)) - entry_fee) / denominator)
    wins, losses = [value for value in returns if value > 0], [value for value in returns if value < 0]
    win_rate = None if not returns else Decimal(len(wins)) / Decimal(len(returns))
    average_win = None if not wins else sum(wins, Decimal("0")) / Decimal(len(wins))
    average_loss = None if not losses else sum(losses, Decimal("0")) / Decimal(len(losses))
    profit_factor = (None if not losses else sum(wins, Decimal("0")) / abs(sum(losses, Decimal("0"))))
    expectancy = None if not returns else sum(returns, Decimal("0")) / Decimal(len(returns))
    return {"trade_count": len(returns), "win_rate": win_rate, "average_win_return": average_win,
            "average_loss_return": average_loss, "profit_factor": profit_factor, "expectancy": expectancy}


def _annualized_volatility(returns):
    if len(returns) < 2:
        return None
    values = [float(value) for value in returns]
    average = sum(values) / len(values)
    return Decimal(str(sqrt(sum((v - average) ** 2 for v in values) / (len(values) - 1)) * sqrt(252)))


def _sortino(returns):
    if not returns:
        return None
    downside = [float(min(value, Decimal("0"))) for value in returns]
    deviation = sqrt(sum(value * value for value in downside) / len(downside))
    return None if deviation == 0 else Decimal(str((sum(float(v) for v in returns) / len(returns)) / deviation * sqrt(252)))


def _rolling_sharpe_std(returns, window):
    if len(returns) < window + 1:
        return None
    values = []
    for end in range(window, len(returns) + 1):
        values.append(_sharpe(returns[end - window:end]))
    values = [float(value) for value in values if value is not None]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return Decimal(str(sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))))


def _sharpe(returns):
    return _sortino(returns) if False else (None if len(returns) < 2 else _plain_sharpe(returns))


def _plain_sharpe(returns):
    values = [float(value) for value in returns]
    mean = sum(values) / len(values)
    deviation = sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))
    return None if deviation == 0 else Decimal(str(mean / deviation * sqrt(252)))


def _positive_month_ratio(connection, account_id):
    rows = self_rows(connection, """
        WITH month_ends AS (
            SELECT date_trunc('month', trade_date) AS month, first(total_equity ORDER BY trade_date) AS first_equity,
                   last(total_equity ORDER BY trade_date) AS last_equity
            FROM sim_nav_daily WHERE account_id = ? GROUP BY 1
        ) SELECT first_equity, last_equity FROM month_ends
    """, [account_id])
    if not rows:
        return None
    return Decimal(sum(Decimal(str(last)) > Decimal(str(first)) for first, last in rows)) / Decimal(len(rows))


def self_rows(connection, query, params):
    return connection.execute(query, params).fetchall()


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
