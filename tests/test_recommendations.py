from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from quant_core.recommendations import store_recommendations
from quant_core.snapshots import SnapshotService
from quant_core.strategy import Recommendation


def test_recommendations_are_written_only_for_frozen_runs():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
    snapshots = SnapshotService(con)
    snapshots.register_market_snapshot("market", date(2026, 8, 31), "fixture", now, now, "m.json", "a" * 64, now)
    snapshots.register_feature_snapshot("feature", date(2026, 8, 31), 20, "f.parquet", "b" * 64, ["market"], now)
    snapshots.freeze_run("run", date(2026, 9, 2), "baseline", "v1", "cost_v1", "feature",
                         datetime(2026, 9, 1, 9, tzinfo=timezone.utc), now)
    pick = Recommendation("600000.SH", 1, Decimal("0.1"), Decimal("10"), {"momentum": "0.1"})
    store_recommendations(con, "run", [pick], now)
    assert con.execute("SELECT ticker, rank_order FROM recommendation_items").fetchone() == ("600000.SH", 1)
    with pytest.raises(ValueError):
        store_recommendations(con, "missing", [pick], now)
