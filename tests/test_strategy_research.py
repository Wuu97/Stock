from datetime import date, timedelta
from decimal import Decimal

import pytest

from quant_core.features import build_features
from quant_core.models import DayBar
from quant_core.strategy_research import (BaselineScoreProvider, StrategySpec, baseline_strategy_spec,
                                          momentum_volume_strategy_spec, pure_momentum_strategy_spec, resolve_score_provider)


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
