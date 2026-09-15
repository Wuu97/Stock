"""Check whether the first four research controls are comparable in one competition."""

import argparse
import json

from quant_core.database import read_connection
from quant_core.strategy_scorecard import competition_coverage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--competition-profile-id", required=True)
    parser.add_argument("--competition-profile-version", required=True)
    parser.add_argument("--evaluation-stage", choices=("BACKTEST", "SHADOW", "PRODUCTION_SIM"), default="BACKTEST")
    args = parser.parse_args()
    with read_connection(args.db) as connection:
        result = competition_coverage(connection, args.competition_profile_id,
                                      args.competition_profile_version, args.evaluation_stage)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
