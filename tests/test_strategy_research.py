from datetime import date, timedelta
from decimal import Decimal

import pytest

from quant_core.features import FeatureRow, build_features
from quant_core.models import DayBar
from quant_core.strategy_research import (BaselineScoreProvider, StrategySpec, baseline_strategy_spec,
                                          bulldozer_overnight_daily_proxy_spec, momentum_volume_strategy_spec,
                                          pure_momentum_strategy_spec, kdj_manual_strategy_spec,
                                          macd_manual_strategy_spec, resolve_score_provider)
from quant_core.bulldozer import BulldozerConfig, build_bulldozer_features


def _bars():
    start = date(2026, 1, 2)
    return [DayBar(start + timedelta(days=index), "600000.SH", Decimal("10"), Decimal("10"), Decimal("10"),
                   Decimal("10") + Decimal(index) / Decimal("10"), 200 if index == 19 else 100,
                   Decimal("1000"), Decimal("20"), Decimal("1")) for index in range(20)]


def test_baseline_provider_is_an_explicit_equivalent_of_the_existing_baseline():
    features = build_features(_bars(), date(2026, 1, 21), 20)
    spec = baseline_strategy_spec(Decimal("1.5"), 1)
    result = BaselineScoreProvider(spec).score(features, date(2026, 1, 21))
    assert result.spec == spec
    assert [item.ticker for item in result.recommendations] == ["600000.SH"]


def test_strategy_spec_and_provider_fail_closed_for_invalid_or_unknown_definitions():
    with pytest.raises(ValueError, match="valid JSON"):
        StrategySpec("x", "v1", "x", "not-json")
    spec = StrategySpec("x", "v1", "UNKNOWN", "{}")
    with pytest.raises(ValueError, match="unsupported"):
        resolve_score_provider(spec)


def test_pure_momentum_and_composite_providers_are_distinct_and_reproducible():
    as_of = date(2026, 1, 21)
    features = build_features(_bars() + [DayBar(as_of - timedelta(days=index), "600001.SH", Decimal("10"), Decimal("10"), Decimal("10"),
                                                Decimal("10") + Decimal(19 - index) / Decimal("20"), 500 if index == 0 else 100,
                                                Decimal("1000"), Decimal("20"), Decimal("1")) for index in range(20)], as_of, 20)
    pure = resolve_score_provider(pure_momentum_strategy_spec(2)).score(features, as_of)
    composite = resolve_score_provider(momentum_volume_strategy_spec(2, Decimal("0.2"))).score(features, as_of)
    assert [item.ticker for item in pure.recommendations] == ["600000.SH", "600001.SH"]
    assert composite.recommendations[0].ticker == "600001.SH"
    assert "volume_percentile" in composite.recommendations[0].reasons


def test_bulldozer_daily_proxy_keeps_only_explicit_ma5_rule_and_records_gaps():
    as_of = date(2026, 1, 21)
    bars = _bars()
    features = build_bulldozer_features(bars, as_of, BulldozerConfig(top_n=1, consecutive_ma5_days=8))
    spec = bulldozer_overnight_daily_proxy_spec(top_n=1, consecutive_ma5_days=8)
    result = resolve_score_provider(spec).score(features, as_of)
    assert [item.ticker for item in result.recommendations] == ["600000.SH"]
    assert result.recommendations[0].reasons["source_rule"] == "连续8天沿着五日均线向上"
    assert "竞价" in result.recommendations[0].reasons["unmodeled_source_rules"]
    assert result.recommendations[0].reasons["td_usage"] == "SHANGHAI_COMPOSITE_DAILY_AUXILIARY_LABEL_NO_UNSPECIFIED_PRECEDENCE"
    assert result.recommendations[0].reasons["rule_applicability"]["auction_orderbook_seal_strength"] == {
        "status": "NOT_APPLICABLE",
        "reason_code": "REALTIME_FIVE_LEVEL_ORDER_BOOK_UNAVAILABLE",
        "effect": "NOT_USED_FOR_SELECTION_OR_ENTRY_OR_EXIT",
    }


def test_manual_kdj_and_macd_controls_are_frozen_research_strategies():
    as_of = date(2026, 1, 31)
    features = [FeatureRow("600000.SH", as_of, Decimal("10"), Decimal("10"), Decimal("100"), Decimal("0"), Decimal("1"),
                           macd=Decimal("1"), macd_signal=Decimal(".5"), previous_macd=Decimal(".8"),
                           kdj_k=Decimal("60"), kdj_d=Decimal("50"), kdj_j=Decimal("80"))]
    kdj = resolve_score_provider(kdj_manual_strategy_spec(1)).score(features, as_of)
    macd = resolve_score_provider(macd_manual_strategy_spec(1)).score(features, as_of)
    assert kdj.recommendations[0].reasons["rule"] == "K>D AND K<80 AND J<100"
    assert macd.recommendations[0].reasons["rule"] == "MACD>SIGNAL AND MACD>0 AND MACD>PREVIOUS_MACD"
