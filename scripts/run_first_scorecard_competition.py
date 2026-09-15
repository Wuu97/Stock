"""Run the four frozen Scorecard V1 controls under identical experiment inputs."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from quant_core.database import read_connection
from quant_core.strategy_scorecard import competition_coverage


STRATEGIES = ("baseline", "pure_momentum", "kdj_manual", "macd_manual")


def _commands(args, forwarded):
    forbidden = {"--strategy", "--account-id", "--output", "--db", "--competition-profile-id", "--competition-profile-version"}
    if any(token.split("=", 1)[0] in forbidden for token in forwarded):
        raise ValueError("forwarded experiment arguments must not override db, strategy, account, output, or competition profile")
    runner = str(Path(__file__).with_name("run_strategy_experiment.py"))
    return [
        [sys.executable, runner, "--db", args.db, "--account-id", f"{args.account_prefix}_{strategy}",
         "--strategy", strategy, "--output", str(Path(args.output_dir) / f"{strategy}.json"),
         "--competition-profile-id", args.competition_profile_id,
         "--competition-profile-version", args.competition_profile_version, *forwarded]
        for strategy in STRATEGIES
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--competition-profile-id", required=True)
    parser.add_argument("--competition-profile-version", required=True)
    parser.add_argument("--account-prefix", required=True, help="Fresh account IDs receive _baseline, _pure_momentum, etc.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("experiment_args", nargs=argparse.REMAINDER,
                        help="Pass shared run_strategy_experiment.py arguments after --")
    args = parser.parse_args()
    forwarded = args.experiment_args[1:] if args.experiment_args[:1] == ["--"] else args.experiment_args
    if not forwarded:
        parser.error("provide common experiment arguments after --")
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    commands = _commands(args, forwarded)
    results = []
    for strategy, command in zip(STRATEGIES, commands):
        completed = subprocess.run(command, check=True, text=True, capture_output=True)
        results.append({"strategy": strategy, "result": json.loads(completed.stdout)})
    with read_connection(args.db) as connection:
        coverage = competition_coverage(connection, args.competition_profile_id,
                                        args.competition_profile_version, "BACKTEST")
    print(json.dumps({"runs": results, "coverage": coverage}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
