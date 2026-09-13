"""Run four immutable opening-gap experiments in a research-only database."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from quant_core.database import writer_connection

from quant_core.research_snapshot import assert_research_database


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = (
    ("none", "10", "-0.99"),
    ("wide", "0.05", "-0.06"),
    ("default", "0.03", "-0.04"),
    ("conservative", "0.02", "-0.03"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--study-id")
    parser.add_argument("--market-source-channel", default="tushare_history_daily")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--benchmark-ticker", default="000300.SH")
    parser.add_argument("--benchmark-close-csv", required=True)
    parser.add_argument("--universe-group-name", required=True)
    parser.add_argument("--strategy", choices=("baseline", "pure_momentum", "momentum_volume"), default="baseline")
    parser.add_argument("--volume-multiple", default="1.5")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--portfolio-method", choices=("fixed_shares", "equal_weight"), default="equal_weight")
    parser.add_argument("--shares-per-order", type=int, default=100)
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--cash-reserve", default="0.05")
    parser.add_argument("--initial-cash", default="1000000")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    _verify_research_db(args.db)
    study_id = args.study_id or f"open_gap_{args.start_date.replace('-', '')}_{args.end_date.replace('-', '')}_{uuid4().hex[:8]}"
    output_dir = Path(args.output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name, max_up, max_down in SCENARIOS:
        output = output_dir / f"{study_id}_{name}.json"
        command = _experiment_command(args, f"{study_id}_{name}", output, max_up, max_down)
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        if completed.returncode:
            raise RuntimeError(f"{name} scenario failed: {completed.stderr.strip() or completed.stdout.strip()}")
        results.append({"scenario": name, "max_open_gap_up": max_up, "max_open_gap_down": max_down,
                        "experiment": json.loads(completed.stdout), "report": str(output)})
    manifest = {"study_id": study_id, "database": str(Path(args.db).resolve()), "created_at": datetime.now(timezone.utc).isoformat(),
                "scenarios": results}
    manifest_path = output_dir / f"{study_id}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"study_id": study_id, "manifest": str(manifest_path), "scenarios": results}, ensure_ascii=False))


def _verify_research_db(path: str) -> None:
    connection = writer_connection(path, transaction=False)
    try:
        assert_research_database(connection)
    finally:
        connection.close()


def _experiment_command(args, account_id: str, output: Path, max_up: str, max_down: str) -> list[str]:
    return [sys.executable, str(ROOT / "scripts" / "run_strategy_experiment.py"), "--db", args.db,
            "--account-id", account_id, "--market-source-channel", args.market_source_channel,
            "--start-date", args.start_date, "--end-date", args.end_date, "--benchmark-ticker", args.benchmark_ticker,
            "--benchmark-close-csv", args.benchmark_close_csv, "--universe-group-name", args.universe_group_name,
            "--strategy", args.strategy, "--volume-multiple", args.volume_multiple, "--top-n", str(args.top_n),
            "--portfolio-method", args.portfolio_method, "--shares-per-order", str(args.shares_per_order),
            "--max-positions", str(args.max_positions), "--cash-reserve", args.cash_reserve,
            "--initial-cash", args.initial_cash, "--max-open-gap-up", max_up, "--max-open-gap-down", max_down,
            "--output", str(output)]


if __name__ == "__main__":
    main()
