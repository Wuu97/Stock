from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from quant_core.event_exposure import EventExposure, store_exposures


def test_event_exposures_are_append_only_and_require_review_context():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    connection.execute("INSERT INTO sw_industry_taxonomy VALUES ('sw_v1', '220202', '稀有金属', 3)")
    exposure = EventExposure("600000.SH", "sw_v1", "220202", date(2026, 1, 1), None, Decimal("0.6"),
                             "主营业务经人工复核", "annual report", "analyst")
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    assert store_exposures(connection, "manual_v1", [exposure], now) == 1
    with pytest.raises(Exception):
        store_exposures(connection, "manual_v1", [exposure], now)
