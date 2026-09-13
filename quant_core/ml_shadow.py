"""Versioned, inference-only ML helpers for shadow recommendation runs."""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from math import sqrt
from pathlib import Path
from typing import Iterable, Mapping, Tuple

from .models import DayBar


FEATURE_COLUMNS = (
    "momentum_5d", "momentum_20d", "sma20_deviation", "volume_ratio_20d",
    "momentum_20d_percentile", "momentum_20d_zscore",
)
MODEL_SCHEMA_VERSION = "ml_shadow_ridge_v1"


@dataclass(frozen=True)
class MLShadowFeature:
    ticker: str
    close: float
    values: Mapping[str, float]


@dataclass(frozen=True)
class MLShadowModel:
    trained_through_date: date
    feature_means: Mapping[str, float]
    feature_scales: Mapping[str, float]
    coefficients: Mapping[str, float]
    intercept: float
    artifact_sha256: str

    @classmethod
    def load(cls, path: Path) -> "MLShadowModel":
        raw = path.read_bytes()
        payload = json.loads(raw)
        if payload.get("schema_version") != MODEL_SCHEMA_VERSION:
            raise ValueError("unsupported ML shadow model schema")
        if tuple(payload.get("feature_columns", ())) != FEATURE_COLUMNS:
            raise ValueError("ML shadow model feature schema does not match runtime")
        try:
            means = {name: float(payload["feature_means"][name]) for name in FEATURE_COLUMNS}
            scales = {name: float(payload["feature_scales"][name]) for name in FEATURE_COLUMNS}
            coefficients = {name: float(payload["coefficients"][name]) for name in FEATURE_COLUMNS}
            trained = date.fromisoformat(payload["trained_through_date"])
            intercept = float(payload["intercept"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("ML shadow model artifact is incomplete") from error
        if any(scale <= 0 for scale in scales.values()):
            raise ValueError("ML shadow model scales must be positive")
        return cls(trained, means, scales, coefficients, intercept, sha256(raw).hexdigest())

    def predict(self, values: Mapping[str, float]) -> float:
        try:
            return self.intercept + sum(
                self.coefficients[name] * ((float(values[name]) - self.feature_means[name]) / self.feature_scales[name])
                for name in FEATURE_COLUMNS
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("ML shadow feature vector is incomplete") from error


def build_ml_shadow_features(bars: Iterable[DayBar], as_of_trade_date: date,
                             lookback_days: int = 20) -> Tuple[MLShadowFeature, ...]:
    """Build inference features using only bars available at the decision close."""
    if lookback_days < 20:
        raise ValueError("ML shadow inference requires at least 20 lookback days")
    by_ticker = {}
    for bar in bars:
        if bar.trade_date <= as_of_trade_date and bar.status == "TRADING":
            by_ticker.setdefault(bar.ticker, []).append(bar)
    raw = []
    for ticker, history in by_ticker.items():
        window = sorted(history, key=lambda item: item.trade_date)[-lookback_days:]
        if len(window) != lookback_days or window[-1].trade_date != as_of_trade_date:
            continue
        closes = [item.close for item in window]
        volumes = [item.volume for item in window]
        average_volume = sum(volumes) / len(volumes)
        raw.append((ticker, closes[-1], {
            "momentum_5d": float((closes[-1] / closes[-6]) - 1),
            "momentum_20d": float((closes[-1] / closes[0]) - 1),
            "sma20_deviation": float((closes[-1] / (sum(closes) / len(closes))) - 1),
            "volume_ratio_20d": float(volumes[-1] / average_volume) if average_volume else 0.0,
        }))
    momentums = [values["momentum_20d"] for _, _, values in raw]
    percentiles, zscores = _cross_section_statistics(momentums)
    return tuple(MLShadowFeature(ticker, float(close), {
        **values,
        "momentum_20d_percentile": percentiles[index],
        "momentum_20d_zscore": zscores[index],
    }) for index, (ticker, close, values) in enumerate(raw))


def _cross_section_statistics(values):
    if not values:
        return [], []
    ordered = sorted(values)
    percentiles = [(sum(value >= candidate for candidate in ordered) - 0.5) / len(values) for value in values]
    mean = sum(values) / len(values)
    scale = sqrt(sum((value - mean) ** 2 for value in values) / len(values))
    return percentiles, [(value - mean) / scale if scale else 0.0 for value in values]
