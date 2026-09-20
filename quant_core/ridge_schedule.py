"""Immutable, causally valid Ridge model schedules for strict replays."""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

from .ml_shadow import MLShadowModel
from .strategy_research import MLRidgeScoreProvider, StrategyResult
from .features import FeatureRow


SCHEDULE_SCHEMA_VERSION = "ridge_model_schedule_v1"


@dataclass(frozen=True)
class ScheduledModel:
    model_path: Path
    model_sha256: str
    training_decision_start: date
    training_decision_end: date
    label_availability_cutoff_date: date
    prediction_start: date
    prediction_end: date

    def __post_init__(self):
        if (self.training_decision_start > self.training_decision_end
                or self.prediction_start > self.prediction_end
                or self.training_decision_end > self.label_availability_cutoff_date):
            raise ValueError("Ridge schedule date ranges are invalid")


@dataclass(frozen=True)
class RidgeModelSchedule:
    path: Path
    artifact_sha256: str
    entries: tuple[ScheduledModel, ...]

    @classmethod
    def load(cls, path: Path) -> "RidgeModelSchedule":
        raw = path.read_bytes()
        try:
            payload = json.loads(raw)
            if payload["schema_version"] != SCHEDULE_SCHEMA_VERSION:
                raise ValueError("unsupported Ridge model schedule schema")
            entries = tuple(ScheduledModel(
                Path(item["model_path"]), item["model_sha256"],
                date.fromisoformat(item["training_decision_dates"][0]),
                date.fromisoformat(item["training_decision_dates"][1]),
                date.fromisoformat(item["label_availability_cutoff_date"]),
                date.fromisoformat(item["prediction_dates"][0]),
                date.fromisoformat(item["prediction_dates"][1]),
            ) for item in payload["models"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Ridge model schedule is incomplete") from error
        if not entries:
            raise ValueError("Ridge model schedule must contain at least one model")
        prior_end = None
        for entry in entries:
            if prior_end is not None and entry.prediction_start <= prior_end:
                raise ValueError("Ridge model schedule prediction ranges overlap")
            model = MLShadowModel.load(entry.model_path)
            if model.artifact_sha256 != entry.model_sha256:
                raise ValueError("Ridge schedule model hash mismatch")
            if model.trained_through_date != entry.label_availability_cutoff_date:
                raise ValueError("Ridge schedule training cutoff does not match model artifact")
            model_payload = json.loads(entry.model_path.read_text(encoding="utf-8"))
            if model_payload.get("label_availability_cutoff_date") != entry.label_availability_cutoff_date.isoformat():
                raise ValueError("Ridge model artifact lacks the required label-availability cutoff")
            prior_end = entry.prediction_end
        return cls(path, sha256(raw).hexdigest(), entries)

    def model_for(self, as_of_trade_date: date) -> MLShadowModel:
        for entry in self.entries:
            if entry.prediction_start <= as_of_trade_date <= entry.prediction_end:
                return MLShadowModel.load(entry.model_path)
        raise ValueError("no frozen Ridge model is valid for this prediction date")


@dataclass(frozen=True)
class ScheduledMLRidgeScoreProvider:
    """Score only with the immutable model explicitly valid on each decision day."""

    spec: object
    schedule: RidgeModelSchedule

    def score(self, features: Iterable[FeatureRow], as_of_trade_date: date) -> StrategyResult:
        model = self.schedule.model_for(as_of_trade_date)
        return MLRidgeScoreProvider(self.spec, model).score(features, as_of_trade_date)
