"""Create an immutable BACKTEST scorecard from a completed strategy experiment."""

import argparse
import json
from datetime import datetime, timezone

from quant_core.database import writer_connection
from quant_core.strategy_scorecard import CompetitionProfile, StrategyScorecardStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--competition-profile-id", required=True)
    parser.add_argument("--competition-profile-version", required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    with writer_connection(args.db) as connection:
        row = connection.execute("SELECT spec_json FROM strategy_experiments WHERE experiment_id = ?", [args.experiment_id]).fetchone()
        if not row:
            raise ValueError("unknown experiment")
        payload = json.loads(row[0])
        # The profile extractor only needs strategy-neutral fields; reconstructing this
        # compact payload avoids accepting caller-provided mutable competition inputs.
        profile_payload = {key: value for key, value in payload.items() if key not in {"strategy", "start_date", "end_date"}}
        profile = CompetitionProfile(args.competition_profile_id, args.competition_profile_version, profile_payload)
        scorecard_id = StrategyScorecardStore(connection).store_experiment_scorecard(
            args.experiment_id, profile, "BACKTEST", now
        )
    print(json.dumps({"scorecard_id": scorecard_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
