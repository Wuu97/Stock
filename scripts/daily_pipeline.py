"""Run the auditable A-share daily paper-trading workflow from one entry point."""

import argparse
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
import os
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from quant_core.database import read_connection, writer_connection

from quant_core.daily_settlement import settle_frozen_buys, settle_pending_sells
from quant_core.environment import load_env_file
from quant_core.cost_models import load_cost_model
from quant_core.dashboard import load_news_monitor
from quant_core.matching import OpenGapPolicy
from quant_core.pipeline_audit import PipelineStore
from quant_core.portfolio import PortfolioPolicy
from quant_core.news_sources import load_official_rss_sources


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


def _settle(connection, account_id: str, snapshot_id: str, trade_date: date, next_date: date, fee, policy,
            open_gap_policy: OpenGapPolicy) -> dict:
    sells = settle_pending_sells(connection, account_id, snapshot_id, trade_date, next_date, fee)
    buys = settle_frozen_buys(connection, account_id, snapshot_id, trade_date, next_date, 100, fee, policy, open_gap_policy)
    return {"sells": [asdict(item) for item in sells], "buys": [asdict(item) for item in buys]}


def _context(db_path: str, account_id: str, configured_taxonomy: Optional[str]) -> dict:
    with read_connection(db_path) as connection:
        return {"taxonomy_version": _taxonomy_version(connection, configured_taxonomy),
                "tracked_tickers": _tracked_tickers(connection, account_id)}


def _universe_tickers(db_path: str, universe_snapshot_id: str) -> list[str]:
    """Use the frozen candidate universe as the pre-selection news coverage set."""
    with read_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT ticker FROM universe_members WHERE universe_snapshot_id = ? ORDER BY ticker",
            [universe_snapshot_id],
        ).fetchall()
    return [row[0] for row in rows]


def _run_news_sources(db_path: str, trade_date: date, tracked_tickers: list[str], rss_feeds: list[str],
                      gdelt_queries: list[str]) -> dict:
    """Run each source independently so a rate-limited discovery feed cannot hide other evidence."""
    sources: list[tuple[str, list[str]]] = [("cls", ["--db", db_path, "--source", "cls"])]
    if tracked_tickers:
        arguments = ["--db", db_path, "--source", "cninfo", "--date", trade_date.isoformat()]
        for ticker in tracked_tickers:
            arguments.extend(("--cninfo-ticker", ticker))
        sources.append(("cninfo", arguments))
    for channel_url in rss_feeds:
        channel, separator, url = channel_url.partition("=")
        if not separator or not channel or not url:
            raise ValueError("--rss-feed must be CHANNEL=URL")
        sources.append((f"rss:{channel}", ["--db", db_path, "--source", "rss", "--source-channel", channel,
                                           "--feed-url", url]))
    for query in gdelt_queries:
        sources.append((f"gdelt:{query}", ["--db", db_path, "--source", "gdelt", "--query", query]))

    outcomes = {}
    for name, arguments in sources:
        try:
            outcomes[name] = {"status": "SUCCEEDED", "result": _run("ingest_news.py", arguments)}
        except Exception as error:
            outcomes[name] = {"status": "FAILED", "reason": str(error)}
    return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/top50/quant.duckdb")
    parser.add_argument("--account-id", default="top50_forward_account")
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--taxonomy-version")
    parser.add_argument("--group-name", default="all_a_large_cap_momentum")
    parser.add_argument("--cost-model-version", default="cost_a_share_2026_v1")
    parser.add_argument("--cost-model-config", default="config/cost_models.yaml")
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--cash-reserve", type=Decimal, default=Decimal("0.05"))
    parser.add_argument("--max-open-gap-up", type=Decimal, default=Decimal("0.03"))
    parser.add_argument("--max-open-gap-down", type=Decimal, default=Decimal("-0.04"))
    parser.add_argument("--ml-shadow-model", help="Versioned model artifact; enables a non-trading ML shadow run")
    parser.add_argument("--ml-shadow-top-n", type=int, default=5)
    parser.add_argument("--report-dir", default="data/top50/reports")
    parser.add_argument("--rss-feed", action="append", default=[], metavar="CHANNEL=URL")
    parser.add_argument("--news-source-config", default="config/news_sources.json",
                        help="Versioned official RSS source registry")
    parser.add_argument("--gdelt-query", action="append", default=[])
    parser.add_argument("--skip-monitoring-research", action="store_true",
                        help="Skip the separate full-market monitoring-group research branch.")
    args = parser.parse_args()

    trade_date, next_date = _trade_dates(date.fromisoformat(args.trade_date))
    now = datetime.now(SHANGHAI)
    _run("init_db.py", ["--db", args.db])
    fee = load_cost_model(args.cost_model_version, ROOT / args.cost_model_config)
    policy = PortfolioPolicy.equal_weight(args.max_positions, args.cash_reserve)
    open_gap_policy = OpenGapPolicy(args.max_open_gap_up, args.max_open_gap_down)
    run_config = {"arguments": vars(args), "cost_model": {"version": fee.version, "commission_rate": str(fee.commission_rate),
                  "min_commission": str(fee.min_commission), "stamp_duty_rate": str(fee.stamp_duty_rate),
                  "transfer_fee_rate": str(fee.transfer_fee_rate), "slippage_rate": str(fee.slippage_rate)},
                  "cost_model_config_sha256": sha256((ROOT / args.cost_model_config).read_bytes()).hexdigest()}
    audit = PipelineStore(args.db, Path(args.report_dir) / "pipeline_runs")
    run_id, effective_as_of, completed = audit.start_or_resume(trade_date, args.account_id, run_config, now)
    if completed:
        print(json.dumps({"pipeline_run_id": run_id, "status": "ALREADY_COMPLETED"}, ensure_ascii=False))
        return
    context = audit.run_stage(run_id, "context", "BLOCKING", lambda: _context(args.db, args.account_id, args.taxonomy_version))
    taxonomy_version, tracked_tickers = context["taxonomy_version"], context["tracked_tickers"]

    refresh_args = ["--db", args.db, "--trade-date", trade_date.isoformat(), "--group-name", args.group_name]
    for ticker in tracked_tickers:
        refresh_args.extend(("--include-ticker", ticker))
    refresh = audit.run_stage(run_id, "refresh_universe", "BLOCKING", lambda: _run("refresh_daily_dynamic_universe.py", refresh_args))
    candidate_tickers = _universe_tickers(args.db, refresh["universe_snapshot_id"])
    news_tickers = sorted(set([*tracked_tickers, *candidate_tickers]))
    raw_snapshot = refresh["market_snapshot_id"]
    enriched_snapshot = f"{raw_snapshot}_limits"
    audit.run_stage(run_id, "enrich_limits", "BLOCKING", lambda: _run("enrich_snapshot_with_tushare_limits.py", ["--db", args.db, "--base-snapshot-id", raw_snapshot,
         "--snapshot-id", enriched_snapshot, "--exempt-ticker", "000300.SH"]))

    def settle_stage():
        with writer_connection(args.db, transaction=False) as connection:
            return _settle(connection, args.account_id, enriched_snapshot, trade_date, next_date, fee, policy, open_gap_policy)
    settlement = audit.run_stage(run_id, "settlement", "BLOCKING", settle_stage)
    exits = audit.run_stage(run_id, "exit_signals", "BLOCKING", lambda: _run("generate_exit_orders.py", ["--db", args.db, "--account-id", args.account_id,
             "--market-snapshot-id", enriched_snapshot, "--as-of-date", trade_date.isoformat(),
             "--next-trading-date", next_date.isoformat()]))

    official_rss_sources = load_official_rss_sources(ROOT / args.news_source_config)
    configured_rss_feeds = [f"{source.channel}={source.feed_url}" for source in official_rss_sources]
    rss_feeds = list(dict.fromkeys([*configured_rss_feeds, *args.rss_feed]))
    official_domains = [source.official_domain for source in official_rss_sources]
    def news_stage():
        sources = _run_news_sources(args.db, trade_date, news_tickers, rss_feeds, args.gdelt_query)
        cninfo = sources.get("cninfo", {}).get("result", {})
        return {"sources": sources, "coverage": {"candidate_ticker_count": len(candidate_tickers),
                "coverage_ticker_count": len(news_tickers), "cninfo_documents": cninfo.get("fetched", 0),
                "cninfo_pdf": cninfo.get("pdf", {})}}
    news = audit.run_stage(run_id, "news", "NON_BLOCKING", news_stage)
    def news_health_stage():
        with read_connection(args.db) as connection:
            return load_news_monitor(connection)["health"]
    news_health = audit.run_stage(run_id, "news_health", "NON_BLOCKING", news_health_stage)
    def risk_veto_shadow_stage():
        if not news_tickers:
            return {"mode": "SHADOW_ONLY", "status": "SKIPPED_NO_NEWS_COVERAGE_TICKERS"}
        arguments = ["--db", args.db, "--effective-as-of", effective_as_of.isoformat()]
        for ticker in news_tickers:
            arguments.extend(("--ticker", ticker))
        return _run("analyze_ticker_news.py", arguments)
    risk_veto_shadow = audit.run_stage(run_id, "risk_veto_shadow", "NON_BLOCKING", risk_veto_shadow_stage)
    macro = audit.run_stage(run_id, "macro_hypothesis", "NON_BLOCKING", lambda: _run("derive_macro_hypothesis.py", ["--db", args.db, "--latest-hours", "24",
                             "--effective-as-of", effective_as_of.isoformat(), "--taxonomy-version", taxonomy_version,
                             *[value for domain in official_domains for value in ("--official-domain", domain)]]))

    strategy = audit.run_stage(run_id, "strategy", "BLOCKING", lambda: _run("run_daily_strategy.py", ["--db", args.db, "--market-snapshot-id", enriched_snapshot,
                     "--as-of-date", trade_date.isoformat(), "--target-date", next_date.isoformat(),
                     "--effective-as-of", effective_as_of.isoformat(), "--universe-snapshot-id", refresh["universe_snapshot_id"],
                     "--event-shadow", "--taxonomy-version", taxonomy_version]))
    def monitoring_research_stage():
        if args.skip_monitoring_research:
            return {"status": "SKIPPED_BY_CONFIGURATION"}
        return _run("run_daily_monitoring_research.py", ["--db", args.db, "--trade-date", trade_date.isoformat(),
                    "--target-date", next_date.isoformat(), "--effective-as-of", effective_as_of.isoformat()])
    monitoring_research = audit.run_stage(run_id, "monitoring_research", "NON_BLOCKING", monitoring_research_stage)
    def ml_shadow_stage():
        if not args.ml_shadow_model:
            return {"status": "SKIPPED_NO_MODEL"}
        return _run("run_ml_shadow_strategy.py", ["--db", args.db, "--market-snapshot-id", enriched_snapshot,
                    "--as-of-date", trade_date.isoformat(), "--target-date", next_date.isoformat(),
                    "--effective-as-of", effective_as_of.isoformat(), "--universe-snapshot-id", refresh["universe_snapshot_id"],
                    "--model", args.ml_shadow_model, "--top-n", str(args.ml_shadow_top_n)])
    ml_shadow = audit.run_stage(run_id, "ml_shadow", "NON_BLOCKING", ml_shadow_stage)
    evaluation = audit.run_stage(run_id, "shadow_evaluation", "NON_BLOCKING", lambda: _run("evaluate_shadow_tracks.py", ["--db", args.db, "--all-paired", "--market-snapshot-id",
                       enriched_snapshot, "--benchmark-ticker", "000300.SH", "--cost-model-version", fee.version,
                       "--cost-model-config", str(ROOT / args.cost_model_config),
                       "--max-open-gap-up", str(args.max_open_gap_up), "--max-open-gap-down", str(args.max_open_gap_down)]))
    report_path = Path(args.report_dir) / f"daily_{trade_date.isoformat()}.json"
    audit.run_stage(run_id, "daily_report", "NON_BLOCKING", lambda: _run("generate_daily_report.py", ["--db", args.db, "--trade-date", next_date.isoformat(),
         "--account-id", args.account_id, "--output", str(report_path)]))
    payload = {"trade_date": trade_date.isoformat(), "next_trade_date": next_date.isoformat(),
               "pipeline_run_id": run_id, "effective_as_of": effective_as_of.isoformat(), "market_snapshot_id": enriched_snapshot,
               "universe_snapshot_id": refresh["universe_snapshot_id"], "settlement": settlement, "exit_signals": exits,
               "news": news, "news_candidate_ticker_count": len(candidate_tickers), "news_coverage_ticker_count": len(news_tickers),
               "news_health": news_health, "risk_veto_shadow": risk_veto_shadow, "macro": macro, "strategy": strategy,
               "monitoring_research": monitoring_research, "ml_shadow": ml_shadow, "evaluation": evaluation,
               "report_path": str(report_path)}
    pipeline_path = Path(args.report_dir) / f"pipeline_{trade_date.isoformat()}.json"
    pipeline_path.parent.mkdir(parents=True, exist_ok=True)
    pipeline_path.write_text(json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    audit.finish(run_id)
    payload["pipeline_report_path"] = str(pipeline_path)
    print(json.dumps(payload, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
