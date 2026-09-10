"""Run the auditable A-share daily paper-trading workflow from one entry point."""

import argparse
from dataclasses import asdict
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import duckdb

from quant_core.daily_settlement import settle_frozen_buys, settle_pending_sells
from quant_core.environment import load_env_file
from quant_core.cost_models import load_cost_model
from quant_core.pipeline_audit import PipelineStore


ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")


class DataNotReady(RuntimeError):
    """Raised before any database write when the daily vendor data is incomplete."""


def _trade_dates(trade_date: date) -> tuple[date, date]:
    """Verify authoritative daily data before any workflow stage is allowed to write."""
    load_env_file(ROOT / ".env")
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise DataNotReady("TUSHARE_TOKEN is not configured")
    import tushare as ts

    client = ts.pro_api(token)
    calendar = client.trade_cal(
        exchange="SSE", start_date=trade_date.strftime("%Y%m%d"),
        end_date=(trade_date + timedelta(days=14)).strftime("%Y%m%d"), is_open="1",
    )
    open_dates = sorted(date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:8]}") for value in calendar["cal_date"].astype(str))
    if trade_date not in open_dates:
        raise DataNotReady(f"{trade_date.isoformat()} is not an SSE trading day")
    next_dates = [value for value in open_dates if value > trade_date]
    if not next_dates:
        raise DataNotReady("next SSE trading day is unavailable")
    daily = client.daily(trade_date=trade_date.strftime("%Y%m%d"))
    limits = client.stk_limit(trade_date=trade_date.strftime("%Y%m%d"))
    if daily.empty or limits.empty:
        raise DataNotReady("Tushare daily or stk_limit data is not available yet")
    if not {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}.issubset(daily.columns):
        raise DataNotReady("Tushare daily response is missing required fields")
    if not {"ts_code", "trade_date", "up_limit", "down_limit"}.issubset(limits.columns):
        raise DataNotReady("Tushare stk_limit response is missing required fields")
    return trade_date, next_dates[0]


def _run(script: str, arguments: Iterable[str]) -> object:
    """Run an existing CLI stage without reimplementing its domain logic."""
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *arguments], cwd=ROOT,
        text=True, capture_output=True, check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"{script} failed: {completed.stderr.strip() or completed.stdout.strip()}")
    output = completed.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output.splitlines()[-1])
    except json.JSONDecodeError:
        return {"stdout": output}


def _tracked_tickers(connection, account_id: str) -> list[str]:
    rows = connection.execute(
        "SELECT DISTINCT ticker FROM sim_order_intents WHERE account_id = ? AND order_status = 'PENDING' "
        "UNION SELECT DISTINCT ticker FROM sim_position_lots WHERE account_id = ?",
        [account_id, account_id],
    ).fetchall()
    return sorted(row[0] for row in rows)


def _taxonomy_version(connection, configured: Optional[str]) -> str:
    if configured:
        return configured
    row = connection.execute("SELECT MAX(taxonomy_version) FROM sw_industry_taxonomy").fetchone()
    if row is None or row[0] is None:
        raise ValueError("no SW industry taxonomy is available; provide --taxonomy-version after importing one")
    return row[0]


def _settle(connection, account_id: str, snapshot_id: str, trade_date: date, next_date: date, fee) -> dict:
    sells = settle_pending_sells(connection, account_id, snapshot_id, trade_date, next_date, fee)
    buys = settle_frozen_buys(connection, account_id, snapshot_id, trade_date, next_date, 100, fee)
    return {"sells": [asdict(item) for item in sells], "buys": [asdict(item) for item in buys]}


def _optional_stage(script: str, arguments: list[str]) -> dict:
    try:
        return {"status": "OK", "result": _run(script, arguments)}
    except Exception as error:
        return {"status": "NON_BLOCKING_FAILURE", "reason": str(error)}


def _context(db_path: str, account_id: str, configured_taxonomy: Optional[str]) -> dict:
    connection = duckdb.connect(db_path)
    try:
        return {"taxonomy_version": _taxonomy_version(connection, configured_taxonomy),
                "tracked_tickers": _tracked_tickers(connection, account_id)}
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/top50/quant.duckdb")
    parser.add_argument("--account-id", default="top50_forward_account")
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--taxonomy-version")
    parser.add_argument("--group-name", default="all_a_large_cap_momentum")
    parser.add_argument("--cost-model-version", default="cost_a_share_2026_v1")
    parser.add_argument("--cost-model-config", default="config/cost_models.yaml")
    parser.add_argument("--report-dir", default="data/top50/reports")
    parser.add_argument("--rss-feed", action="append", default=[], metavar="CHANNEL=URL")
    parser.add_argument("--gdelt-query", action="append", default=[])
    args = parser.parse_args()

    trade_date, next_date = _trade_dates(date.fromisoformat(args.trade_date))
    now = datetime.now(SHANGHAI)
    _run("init_db.py", ["--db", args.db])
    audit = PipelineStore(args.db, Path(args.report_dir) / "pipeline_runs")
    run_id, effective_as_of = audit.start_or_resume(trade_date, args.account_id, vars(args), now)
    context = audit.run_stage(run_id, "context", "BLOCKING", lambda: _context(args.db, args.account_id, args.taxonomy_version))
    taxonomy_version, tracked_tickers = context["taxonomy_version"], context["tracked_tickers"]
    fee = load_cost_model(args.cost_model_version, ROOT / args.cost_model_config)

    refresh_args = ["--db", args.db, "--trade-date", trade_date.isoformat(), "--group-name", args.group_name]
    for ticker in tracked_tickers:
        refresh_args.extend(("--include-ticker", ticker))
    refresh = audit.run_stage(run_id, "refresh_universe", "BLOCKING", lambda: _run("refresh_daily_dynamic_universe.py", refresh_args))
    raw_snapshot = refresh["market_snapshot_id"]
    enriched_snapshot = f"{raw_snapshot}_limits"
    audit.run_stage(run_id, "enrich_limits", "BLOCKING", lambda: _run("enrich_snapshot_with_tushare_limits.py", ["--db", args.db, "--base-snapshot-id", raw_snapshot,
         "--snapshot-id", enriched_snapshot, "--exempt-ticker", "000300.SH"]))

    def settle_stage():
        connection = duckdb.connect(args.db)
        try:
            return _settle(connection, args.account_id, enriched_snapshot, trade_date, next_date, fee)
        finally:
            connection.close()
    settlement = audit.run_stage(run_id, "settlement", "BLOCKING", settle_stage)
    exits = audit.run_stage(run_id, "exit_signals", "BLOCKING", lambda: _run("generate_exit_orders.py", ["--db", args.db, "--account-id", args.account_id,
             "--market-snapshot-id", enriched_snapshot, "--as-of-date", trade_date.isoformat(),
             "--next-trading-date", next_date.isoformat()]))

    def news_stage():
        news = {"cls": _optional_stage("ingest_news.py", ["--db", args.db, "--source", "cls"])}
        for channel_url in args.rss_feed:
            channel, separator, url = channel_url.partition("=")
            if not separator or not channel or not url:
                raise ValueError("--rss-feed must be CHANNEL=URL")
            news[channel] = _optional_stage("ingest_news.py", ["--db", args.db, "--source", "rss", "--source-channel", channel,
                                                                 "--feed-url", url])
        for query in args.gdelt_query:
            news[f"gdelt:{query}"] = _optional_stage("ingest_news.py", ["--db", args.db, "--source", "gdelt", "--query", query])
        return news
    news = audit.run_stage(run_id, "news", "NON_BLOCKING", news_stage)
    macro = audit.run_stage(run_id, "macro_hypothesis", "NON_BLOCKING", lambda: _optional_stage("derive_macro_hypothesis.py", ["--db", args.db, "--latest-hours", "24",
                             "--effective-as-of", effective_as_of.isoformat(), "--taxonomy-version", taxonomy_version,
                             "--official-domain", "news.un.org"]))

    strategy = audit.run_stage(run_id, "strategy", "BLOCKING", lambda: _run("run_daily_strategy.py", ["--db", args.db, "--market-snapshot-id", enriched_snapshot,
                     "--as-of-date", trade_date.isoformat(), "--target-date", next_date.isoformat(),
                     "--effective-as-of", effective_as_of.isoformat(), "--universe-snapshot-id", refresh["universe_snapshot_id"],
                     "--event-shadow", "--taxonomy-version", taxonomy_version]))
    evaluation = audit.run_stage(run_id, "shadow_evaluation", "NON_BLOCKING", lambda: _run("evaluate_shadow_tracks.py", ["--db", args.db, "--all-paired", "--market-snapshot-id",
                       enriched_snapshot, "--benchmark-ticker", "000300.SH", "--cost-model-version", fee.version,
                       "--cost-model-config", str(ROOT / args.cost_model_config)]))
    report_path = Path(args.report_dir) / f"daily_{trade_date.isoformat()}.json"
    audit.run_stage(run_id, "daily_report", "NON_BLOCKING", lambda: _run("generate_daily_report.py", ["--db", args.db, "--trade-date", next_date.isoformat(),
         "--account-id", args.account_id, "--output", str(report_path)]))
    payload = {"trade_date": trade_date.isoformat(), "next_trade_date": next_date.isoformat(),
               "pipeline_run_id": run_id, "effective_as_of": effective_as_of.isoformat(), "market_snapshot_id": enriched_snapshot,
               "universe_snapshot_id": refresh["universe_snapshot_id"], "settlement": settlement, "exit_signals": exits,
               "news": news, "macro": macro, "strategy": strategy, "evaluation": evaluation,
               "report_path": str(report_path)}
    pipeline_path = Path(args.report_dir) / f"pipeline_{trade_date.isoformat()}.json"
    pipeline_path.parent.mkdir(parents=True, exist_ok=True)
    pipeline_path.write_text(json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    audit.finish(run_id)
    payload["pipeline_report_path"] = str(pipeline_path)
    print(json.dumps(payload, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
