"""Run rolling ML training and shared-engine strict execution once strict snapshots are complete."""

import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import duckdb

from quant_core.database import writer_connection

from quant_core.backtest import BacktestConfig, replay_daily_strategy
from quant_core.market_data import MarketDataStore
from quant_core.ml_shadow import MLShadowModel
from quant_core.ml_walk_forward import fit_ridge
from quant_core.models import FeeModel
from quant_core.portfolio import PortfolioPolicy
from quant_core.risk import ExitRule, SignalDecayExitRule
from quant_core.settlement import SettlementService
from quant_core.strategy_research import (MLRidgeMarketGuardScoreProvider, MLRidgeScoreProvider, StrategySpec,
                                          baseline_strategy_spec, ml_ridge_market_guard_strategy_spec,
                                          resolve_score_provider)
from quant_core.trading_status import TradingStatusStore
from quant_core.universe import UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--universe-group-name", default="historical_large_cap_momentum")
    parser.add_argument("--market-source-channel", default="tushare_stk_limit_ml_strict")
    parser.add_argument("--base-market-source-channel", default="tushare_history_daily",
                        help="Full-market OHLC source used for valuation and features; strict snapshots override its limits.")
    parser.add_argument("--trading-status-source-channel", default="tushare_suspend_d",
                        help="Official suspension evidence used when a held ticker has no daily bar.")
    parser.add_argument("--market-regime-guard", action="store_true",
                        help="Research-only: compare ML against a CSI300 risk-on entry gate.")
    parser.add_argument("--market-regime-guard-lookback-days", type=int, default=20)
    parser.add_argument("--guard-exit-policy", choices=("default", "signal_decay"), default="default",
                        help="Research-only exit variant applied only to the guarded ML arm.")
    parser.add_argument("--benchmark-csv", default="data/ml_history_v3/000300_SH_adjusted_closes.csv")
    parser.add_argument("--train-window-days", type=int, default=480)
    parser.add_argument("--test-window-days", type=int, default=60)
    parser.add_argument("--period-offset", type=int, default=0,
                        help="Zero-based rolling-window offset, for resumable local execution.")
    parser.add_argument("--max-periods", type=int,
                        help="Maximum rolling windows to run in this invocation.")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--initial-cash", default="1000000")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if (args.period_offset < 0 or args.max_periods is not None and args.max_periods < 1
            or args.market_regime_guard_lookback_days < 2):
        raise ValueError("period offset and max periods must be positive")
    connection = writer_connection(args.db, transaction=False)
    try:
        benchmark_closes = _benchmark_closes(args.benchmark_csv) if args.market_regime_guard else {}
        rows = _dataset_rows(args.dataset)
        dates = sorted({row["trade_date"] for row in rows if row["label_status"] == "MATURE"})
        windows = list(range(args.train_window_days, len(dates), args.test_window_days))
        windows = windows[args.period_offset:]
        if args.max_periods is not None:
            windows = windows[:args.max_periods]
        if not windows:
            raise ValueError("no rolling windows match the selected offset")
        first_test_day = dates[windows[0]]
        last_test_day = dates[min(windows[-1] + args.test_window_days - 1, len(dates) - 1)]
        # The score features need a 20-trading-day lookback. A 45-calendar-day
        # buffer safely contains it while avoiding a full multi-year market scan.
        base_start_day = first_test_day - timedelta(days=45)
        strict_snapshot_ids = [row[0] for row in connection.execute(
            "SELECT market_snapshot_id FROM market_data_snapshots WHERE source_channel = ? "
            "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            [args.market_source_channel, first_test_day, last_test_day],
        ).fetchall()]
        strict_days = {
            row[0] for row in connection.execute(
                "SELECT trade_date FROM market_data_snapshots WHERE source_channel = ? "
                "AND trade_date BETWEEN ? AND ?",
                [args.market_source_channel, first_test_day, last_test_day],
            ).fetchall()
        }
        base_snapshot_ids = [row[0] for row in connection.execute(
            "SELECT market_snapshot_id FROM market_data_snapshots WHERE source_channel = ? "
            "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            [args.base_market_source_channel, base_start_day, last_test_day],
        ).fetchall()]
        # A strict snapshot contains authoritative price limits for the PIT Top50 only.
        # Keep the full-market daily source underneath it: positions can remain held after
        # leaving the next day's Top50, and still require an official close for NAV.
        # load_bars_many gives later IDs precedence, so strict limits override base bars.
        needed_tickers = [row[0] for row in connection.execute(
            "SELECT DISTINCT m.ticker FROM universe_snapshots u JOIN universe_members m "
            "ON m.universe_snapshot_id = u.universe_snapshot_id WHERE u.group_name = ? "
            "AND u.as_of_trade_date BETWEEN ? AND ? ORDER BY m.ticker",
            [args.universe_group_name, first_test_day, last_test_day],
        ).fetchall()]
        if not needed_tickers:
            raise ValueError("no point-in-time universe members match the selected windows")
        bars = MarketDataStore(connection).load_bars_many_for_tickers(
            [*base_snapshot_ids, *strict_snapshot_ids], needed_tickers
        )
        bar_days = {bar.trade_date for bar in bars}
        periods = []
        for index in windows:
            train_dates, test_dates = dates[index - args.train_window_days:index], dates[index:index + args.test_window_days]
            if not test_dates:
                continue
            missing = [day.isoformat() for day in test_dates if day not in strict_days]
            if missing:
                periods.append({"test_dates": [str(test_dates[0]), str(test_dates[-1])], "status": "BLOCKED_MISSING_STRICT_DATA", "missing_dates": missing})
                continue
            universe = UniverseService(connection).members_by_trade_date(args.universe_group_name, test_dates[0], test_dates[-1])
            suspended_tickers_by_date = TradingStatusStore(connection).suspended_tickers_by_date(
                args.trading_status_source_channel, test_dates[0], test_dates[-1]
            )
            train = [row for row in rows if row["trade_date"] in train_dates and row["label_status"] == "MATURE"]
            fitted = fit_ridge(train, 1.0)
            payload = json.dumps(fitted, default=float, sort_keys=True)
            model = MLShadowModel(train_dates[-1], fitted["means"], fitted["scales"], fitted["coefficients"], fitted["intercept"], sha256(payload.encode()).hexdigest())
            ml_spec = StrategySpec("ml_ridge_walk_forward_v1", "ridge_v1", "ML_RIDGE_EXCESS_RETURN", json.dumps({"top_n": args.top_n}))
            period = {"train_dates": [str(train_dates[0]), str(train_dates[-1])], "test_dates": [str(test_dates[0]), str(test_dates[-1])], "model_sha256": model.artifact_sha256}
            providers = [("ml", ml_spec, MLRidgeScoreProvider(ml_spec, model)),
                         ("baseline", baseline_strategy_spec(Decimal("1.5"), args.top_n), resolve_score_provider(baseline_strategy_spec(Decimal("1.5"), args.top_n)))]
            if args.market_regime_guard:
                guard_spec = ml_ridge_market_guard_strategy_spec(args.top_n, args.market_regime_guard_lookback_days)
                providers.insert(1, ("ml_guard", guard_spec,
                                     MLRidgeMarketGuardScoreProvider(
                                         guard_spec, model, benchmark_closes,
                                         args.market_regime_guard_lookback_days
                                     )))
            for label, spec, provider in providers:
                account_id = f"wf_{label}_{uuid4()}"
                SettlementService(connection).create_account(account_id, f"WF {label}", Decimal(args.initial_cash), test_dates[0])
                exit_rule = _guard_exit_rule(args.guard_exit_policy) if label == "ml_guard" else ExitRule()
                replay = replay_daily_strategy(connection, bars, sorted(bar_days), BacktestConfig(account_id, test_dates[0], test_dates[-1], top_n=args.top_n, strategy_spec=spec, portfolio_policy=PortfolioPolicy.equal_weight(5, Decimal("0.05"))), FeeModel("cost_a_share_2026_v1", Decimal("0.00025"), Decimal("5"), Decimal("0.0005"), Decimal("0.00001"), Decimal("0.001")), exit_rule, universe_by_date=universe, score_provider=provider, suspended_tickers_by_date=suspended_tickers_by_date)
                nav = connection.execute("SELECT total_equity, max_drawdown FROM sim_nav_daily WHERE account_id = ? ORDER BY trade_date DESC LIMIT 1", [account_id]).fetchone()
                period[label] = {"account_id": account_id, "replay": replay,
                                 "final_equity": str(nav[0]) if nav else None,
                                 "max_drawdown": str(nav[1]) if nav else None}
            period["status"] = "COMPLETED"
            periods.append(period)
    finally:
        connection.close()
    report = {"schema_version": "ml_strict_walk_forward_v1", "period_offset": args.period_offset,
              "guard_exit_policy": args.guard_exit_policy,
              "periods": periods, "note": "Every completed period uses the shared strict execution engine; blocked periods have no simulated result."}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"completed": sum(item.get("status") == "COMPLETED" for item in periods), "blocked": sum(item.get("status") != "COMPLETED" for item in periods), "output": args.output}, ensure_ascii=False))


def _dataset_rows(path):
    connection = duckdb.connect(":memory:")
    try:
        cursor = connection.execute("SELECT * FROM read_parquet(?)", [path])
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        connection.close()


def _benchmark_closes(path):
    closes = {}
    with Path(path).open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            closes[date.fromisoformat(row["trade_date"])] = Decimal(row["adj_close"])
    return closes


def _guard_exit_rule(name):
    if name == "default":
        return ExitRule()
    if name == "signal_decay":
        return SignalDecayExitRule()
    raise ValueError(f"unsupported guard exit policy: {name}")


if __name__ == "__main__":
    main()
