from datetime import date
from hashlib import sha256
import json

import pytest

from quant_core.ml_shadow import FEATURE_COLUMNS
from quant_core.ridge_schedule import RidgeModelSchedule


def _model(path, cutoff="2024-01-03"):
    payload = {"schema_version": "ml_shadow_ridge_v1", "trained_through_date": cutoff,
               "label_availability_cutoff_date": cutoff, "feature_columns": list(FEATURE_COLUMNS),
               "feature_means": {name: 0 for name in FEATURE_COLUMNS},
               "feature_scales": {name: 1 for name in FEATURE_COLUMNS},
               "coefficients": {name: 0 for name in FEATURE_COLUMNS}, "intercept": 0}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return sha256(path.read_bytes()).hexdigest()


def test_schedule_rejects_model_without_matching_label_cutoff(tmp_path):
    model = tmp_path / "model.json"
    digest = _model(model, "2024-01-03")
    schedule = tmp_path / "schedule.json"
    schedule.write_text(json.dumps({"schema_version": "ridge_model_schedule_v1", "models": [{
        "model_path": str(model), "model_sha256": digest,
        "training_decision_dates": ["2024-01-01", "2024-01-02"],
        "label_availability_cutoff_date": "2024-01-04",
        "prediction_dates": ["2024-01-05", "2024-01-06"],
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="training cutoff"):
        RidgeModelSchedule.load(schedule)


def test_schedule_loads_only_the_model_valid_for_prediction_date(tmp_path):
    model = tmp_path / "model.json"
    digest = _model(model)
    schedule = tmp_path / "schedule.json"
    schedule.write_text(json.dumps({"schema_version": "ridge_model_schedule_v1", "models": [{
        "model_path": str(model), "model_sha256": digest,
        "training_decision_dates": ["2024-01-01", "2024-01-02"],
        "label_availability_cutoff_date": "2024-01-03",
        "prediction_dates": ["2024-01-04", "2024-01-05"],
    }]}), encoding="utf-8")
    loaded = RidgeModelSchedule.load(schedule)
    assert loaded.model_for(date(2024, 1, 4)).artifact_sha256 == digest
    with pytest.raises(ValueError, match="no frozen Ridge model"):
        loaded.model_for(date(2024, 1, 6))
