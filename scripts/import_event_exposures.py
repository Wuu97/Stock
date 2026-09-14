"""Import an append-only, manually reviewed company event-exposure registry."""

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path

from quant_core.database import writer_connection
from quant_core.event_exposure import EventExposure, store_exposures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--input", required=True, help="JSON registry with exposure_version and exposures")
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    version = payload.get("exposure_version")
    entries = payload.get("exposures")
    if not isinstance(version, str) or not isinstance(entries, list):
        raise ValueError("registry requires exposure_version and exposures list")
    exposures = tuple(EventExposure(
        ticker=str(item["ticker"]), taxonomy_version=str(item["taxonomy_version"]), industry_code=str(item["industry_code"]),
        valid_from=date.fromisoformat(item["valid_from"]), valid_to=None if item.get("valid_to") is None else date.fromisoformat(item["valid_to"]),
        exposure_weight=__import__("decimal").Decimal(str(item["exposure_weight"])), rationale=str(item["rationale"]),
        source_reference=str(item["source_reference"]), reviewed_by=str(item["reviewed_by"]),
    ) for item in entries)
    with writer_connection(args.db) as connection:
        count = store_exposures(connection, version, exposures, datetime.now(timezone.utc))
    print(json.dumps({"exposure_version": version, "stored": count}, ensure_ascii=False))


if __name__ == "__main__":
    main()
