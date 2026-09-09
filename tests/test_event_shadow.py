from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.event_shadow import active_event_adjustments, augment_recommendations
from quant_core.strategy import Recommendation


def _connection():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 8, 8, tzinfo=timezone.utc)
    connection.execute("INSERT INTO sw_industry_taxonomy VALUES ('sw_v1', '220202', '稀有金属', 3)")
    connection.execute("INSERT INTO security_industry_memberships VALUES ('sw_v1', '600000.SH', '220202', '2020-01-01', NULL)")
    connection.execute("INSERT INTO macro_event_hypotheses VALUES ('hyp', ?, 'AUTHORITATIVE_OFFICIAL', 1, 'SHADOW_ELIGIBLE', NULL, '{}', 'hash', ?)", [now, now])
    connection.execute("INSERT INTO macro_event_impacts VALUES ('hyp', 'sw_v1', '220202', 'POSITIVE', 0.30, 5, 'may be priced in')")
    return connection, now


def test_active_event_adjustments_respect_membership_and_duration():
    connection, now = _connection()
    adjustments = active_event_adjustments(connection, "sw_v1", date(2026, 9, 10), now)
    assert adjustments["600000.SH"].score == Decimal("0.30")
    assert adjustments["600000.SH"].hypothesis_ids == ("hyp",)
    assert not active_event_adjustments(connection, "sw_v1", date(2026, 9, 20), now)


def test_event_overlay_is_shadow_re_rank_with_explainable_components():
    connection, now = _connection()
    adjustments = active_event_adjustments(connection, "sw_v1", date(2026, 9, 10), now)
    candidates = [
        Recommendation("600001.SH", 1, Decimal("0.10"), Decimal("10"), {}),
        Recommendation("600000.SH", 2, Decimal("0.05"), Decimal("10"), {}),
    ]
    picks = augment_recommendations(candidates, adjustments, Decimal("0.5"), 2)
    assert [item.ticker for item in picks] == ["600000.SH", "600001.SH"]
    assert Decimal(picks[0].reasons["event_score"]) == Decimal("0.30")
