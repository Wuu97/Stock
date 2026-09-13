from datetime import date, timedelta
import json
from pathlib import Path

import pytest

from quant_core.ml_shadow import FEATURE_COLUMNS, MLShadowModel, build_ml_shadow_features
from quant_core.models import DayBar
from quant_core.features import FeatureRow
from quant_core.strategy_research import MLRidgeScoreProvider, ml_ridge_strategy_spec


def _bar(day, ticker, close, volume):
    return DayBar(day, ticker, close, close, close, close, volume, close * volume, None, None)


def _model_payload():
    return {
        "schema_version": "ml_shadow_ridge_v1",
        "trained_through_date": "2026-01-20",
        "feature_columns": list(FEATURE_COLUMNS),
        "feature_means": {name: 0 for name in FEATURE_COLUMNS},
        "feature_scales": {name: 1 for name in FEATURE_COLUMNS},
        "coefficients": {name: 0 for name in FEATURE_COLUMNS},
        "intercept": 0.01,
    }


def test_ml_shadow_features_only_use_as_of_and_earlier_bars():
    start = date(2026, 1, 1)
    days = [start + timedelta(days=index) for index in range(21)]
    bars = []
    for index, day in enumerate(days):
        bars.extend((_bar(day, "AAA", 10 + index, 100 + index), _bar(day, "BBB", 30 - index, 200 - index)))
    baseline = build_ml_shadow_features(bars, days[19])
    changed_future = [bar if bar.trade_date != days[20] else _bar(bar.trade_date, bar.ticker, 999, 999)
                      for bar in bars]
    assert build_ml_shadow_features(changed_future, days[19]) == baseline


def test_ml_shadow_model_validates_schema_and_scores_feature_vector(tmp_path):
    path = Path(tmp_path) / "model.json"
    path.write_text(json.dumps(_model_payload()), encoding="utf-8")
    model = MLShadowModel.load(path)
    assert model.trained_through_date == date(2026, 1, 20)
    assert model.predict({name: 0 for name in FEATURE_COLUMNS}) == pytest.approx(0.01)


def test_ml_provider_refuses_to_score_on_or_before_training_cutoff(tmp_path):
    path = Path(tmp_path) / "model.json"
    path.write_text(json.dumps(_model_payload()), encoding="utf-8")
    model = MLShadowModel.load(path)
    spec = ml_ridge_strategy_spec(str(path), model.artifact_sha256, 1)
    row = FeatureRow("AAA", date(2026, 1, 20), 10, 9, 100, 0.1, 2, 0.02)
    with pytest.raises(ValueError, match="training cutoff"):
        MLRidgeScoreProvider(spec, model).score([row], date(2026, 1, 20))
