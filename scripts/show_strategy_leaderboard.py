"""Print a read-only, profile-scoped Strategy Scorecard leaderboard."""

import argparse
import json

from quant_core.database import read_connection
from quant_core.strategy_scorecard import leaderboard


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--competition-profile-id", required=True)
    parser.add_argument("--competition-profile-version", required=True)
    parser.add_argument("--evaluation-stage", choices=("BACKTEST", "SHADOW", "PRODUCTION_SIM"), required=True)
    parser.add_argument("--include-non-sufficient", action="store_true")
    args = parser.parse_args()
    with read_connection(args.db) as connection:
        rows = leaderboard(connection, args.competition_profile_id, args.competition_profile_version,
                           args.evaluation_stage, args.include_non_sufficient)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
