from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.event_shadow import active_event_adjustments, active_risk_veto_tickers, augment_recommendations
from quant_core.strategy import Recommendation


def _connection():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 8, 8, tzinfo=timezone.utc)
    connection.execute("INSERT INTO sw_industry_taxonomy VALUES ('sw_v1', '220202', '稀有金属', 3)")
    connection.execute("INSERT INTO security_industry_memberships VALUES ('sw_v1', '600000.SH', '220202', '2020-01-01', NULL)")
    connection.execute("INSERT INTO security_event_exposures VALUES ('manual_v1', '600000.SH', 'sw_v1', '220202', '2020-01-01', NULL, 0.60, '主营业务经人工复核', 'fixture', 'reviewer', ?)", [now])
    connection.execute("INSERT INTO macro_event_hypotheses VALUES ('hyp', ?, 'AUTHORITATIVE_OFFICIAL', 1, 'SHADOW_ELIGIBLE', NULL, '{}', 'hash', ?)", [now, now])
    connection.execute("INSERT INTO macro_event_impacts VALUES ('hyp', 'sw_v1', '220202', 'POSITIVE', 0.30, 5, 'may be priced in')")
    return connection, now


def test_active_event_adjustments_respect_membership_and_duration():
    connection, now = _connection()
    adjustments = active_event_adjustments(connection, "sw_v1", date(2026, 9, 10), now)
    assert adjustments["600000.SH"].score == Decimal("0.180")
    assert adjustments["600000.SH"].exposure_weights == (("220202", "0.60000000"),)
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
    assert [item.ticker for item in picks] == ["600001.SH", "600000.SH"]
    assert Decimal(picks[1].reasons["raw_event_score"]) == Decimal("0.180")
    assert Decimal(picks[1].reasons["base_percentile"]) == Decimal("0")


def test_event_adjustment_fails_closed_without_curated_exposure():
    connection, now = _connection()
    connection.execute("DELETE FROM security_event_exposures")
    assert not active_event_adjustments(connection, "sw_v1", date(2026, 9, 10), now)


def test_event_adjustment_uses_the_latest_exposure_version():
    connection, now = _connection()
    connection.execute("INSERT INTO security_event_exposures VALUES ('manual_v2', '600000.SH', 'sw_v1', '220202', '2020-01-01', NULL, 0.25, 'updated review', 'fixture', 'reviewer', ?)", [now.replace(hour=9)])
    adjustments = active_event_adjustments(connection, "sw_v1", date(2026, 9, 10), now.replace(hour=10))
    assert adjustments["600000.SH"].score == Decimal("0.075")


def test_high_risk_disclosure_excludes_only_the_shadow_candidate():
    connection, now = _connection()
    connection.execute("INSERT INTO news_documents (document_id, source_channel, scope, ticker, published_at, received_at, headline, body, content_sha256, evidence_role, created_at) VALUES ('risk-doc', 'fixture', 'STOCK', '600000.SH', ?, ?, '公告', '正文', 'risk-hash', 'EVIDENCE_ELIGIBLE', ?)", [now, now, now])
    connection.execute("INSERT INTO news_assessments VALUES ('risk-assessment', 'risk-doc', 'RISK_VETO', 'fixture', 'model', 'v1', ?, 'hash', ?)", ['{"risk_level":"HIGH"}', now])
    assert active_risk_veto_tickers(connection, now) == ('600000.SH',)
    candidates = [Recommendation('600000.SH', 1, Decimal('0.9'), Decimal('10'), {}), Recommendation('600001.SH', 2, Decimal('0.1'), Decimal('10'), {})]
    shadow = augment_recommendations(candidates, {}, Decimal('0.2'), 2, {'600000.SH'})
    assert [item.ticker for item in shadow] == ['600001.SH']
