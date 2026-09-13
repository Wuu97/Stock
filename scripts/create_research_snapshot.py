"""Create a separate immutable research DuckDB from the production database."""

import argparse
import json
from pathlib import Path

from quant_core.research_snapshot import create_research_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-db", default="data/top50/quant.duckdb")
    parser.add_argument("--research-db", required=True)
    parser.add_argument("--artifact-root", default="data/research/snapshots")
    args = parser.parse_args()
    result = create_research_snapshot(Path(args.production_db), Path(args.research_db), Path(args.artifact_root))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
