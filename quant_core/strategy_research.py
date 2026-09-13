"""Strategy scoring contracts shared by research replays and future production runs."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from typing import Iterable, Mapping, Protocol, Tuple

from .features import FeatureRow
from .ml_shadow import FEATURE_COLUMNS, MLShadowModel
from .strategy import BaselineConfig, Recommendation, select_baseline


FEATURE_SCHEMA_VERSION = "daily_features_v1"
BASELINE_PROVIDER_TYPE = "BASELINE_MOMENTUM_TREND"
PURE_MOMENTUM_PROVIDER_TYPE = "PURE_MOMENTUM"
MOMENTUM_VOLUME_PROVIDER_TYPE = "MOMENTUM_VOLUME_COMPOSITE"
ML_RIDGE_PROVIDER_TYPE = "ML_RIDGE_EXCESS_RETURN"
ML_RIDGE_MARKET_GUARD_PROVIDER_TYPE = "ML_RIDGE_MARKET_GUARD"


@dataclass(frozen=True)
class StrategySpec:
    """Immutable identity and parameters for one scoring algorithm version."""

    strategy_id: str
    strategy_version: str
    provider_type: str
    parameters_json: str
    feature_schema_version: str = FEATURE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not all((self.strategy_id, self.strategy_version, self.provider_type, self.parameters_json, self.feature_schema_version)):
            raise ValueError("strategy spec fields cannot be empty")
        try:
            parameters = json.loads(self.parameters_json)
        except json.JSONDecodeError as error:
            raise ValueError("strategy parameters must be valid JSON") from error
        if not isinstance(parameters, dict):
            raise ValueError("strategy parameters must be a JSON object")

    @property
    def parameters(self) -> Mapping[str, object]:
        return json.loads(self.parameters_json)


@dataclass(frozen=True)
class StrategyResult:
    """One date's ranking result; it answers what to buy, never how much to buy."""

    spec: StrategySpec
    as_of_trade_date: date
    recommendations: Tuple[Recommendation, ...]


class ScoreProvider(Protocol):
    """Pure scorer boundary. Position sizing and execution remain outside this interface."""

    @property
    def spec(self) -> StrategySpec:
        ...

    def score(self, features: Iterable[FeatureRow], as_of_trade_date: date) -> StrategyResult:
        ...


@dataclass(frozen=True)
class BaselineScoreProvider:
    """Adapter that preserves the original trend/volume/momentum ranking exactly."""

    spec: StrategySpec

    def score(self, features: Iterable[FeatureRow], as_of_trade_date: date) -> StrategyResult:
        parameters = self.spec.parameters
        try:
            volume_multiple = Decimal(str(parameters["volume_multiple"]))
            top_n = int(parameters["top_n"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("baseline strategy requires volume_multiple and top_n") from error
        if top_n <= 0 or volume_multiple < 0:
            raise ValueError("baseline strategy parameters are invalid")
        picks = select_baseline(features, BaselineConfig(self.spec.strategy_id, volume_multiple, top_n))
        return StrategyResult(self.spec, as_of_trade_date, tuple(picks))


@dataclass(frozen=True)
class PureMomentumScoreProvider:
    """Rank every eligible feature row by close-to-close momentum only."""

    spec: StrategySpec

    def score(self, features: Iterable[FeatureRow], as_of_trade_date: date) -> StrategyResult:
        top_n = _positive_top_n(self.spec.parameters)
        ranked = sorted(features, key=lambda row: (row.momentum, row.ticker), reverse=True)[:top_n]
        picks = tuple(Recommendation(row.ticker, index, row.momentum, row.close, {
            "momentum": str(row.momentum), "provider": PURE_MOMENTUM_PROVIDER_TYPE,
        }) for index, row in enumerate(ranked, start=1))
        return StrategyResult(self.spec, as_of_trade_date, picks)


@dataclass(frozen=True)
class MomentumVolumeScoreProvider:
    """Combine cross-sectional momentum and volume-ratio ranks without position sizing."""

    spec: StrategySpec

    def score(self, features: Iterable[FeatureRow], as_of_trade_date: date) -> StrategyResult:
        rows = tuple(features)
        parameters = self.spec.parameters
        top_n = _positive_top_n(parameters)
        try:
            momentum_weight = Decimal(str(parameters["momentum_weight"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("momentum-volume strategy requires momentum_weight") from error
        if not Decimal("0") <= momentum_weight <= Decimal("1"):
            raise ValueError("momentum_weight must be between zero and one")
        volume_weight = Decimal("1") - momentum_weight
        momentum_ranks = _percentile_ranks(rows, "momentum")
        volume_ranks = _percentile_ranks(rows, "volume_ratio")
        scored = [(
            row, momentum_weight * momentum_ranks[row.ticker] + volume_weight * volume_ranks[row.ticker]
        ) for row in rows]
        ranked = sorted(scored, key=lambda item: (-item[1], item[0].ticker))[:top_n]
        picks = tuple(Recommendation(row.ticker, index, score, row.close, {
            "momentum": str(row.momentum), "volume_ratio": str(row.volume_ratio),
            "momentum_percentile": str(momentum_ranks[row.ticker]), "volume_percentile": str(volume_ranks[row.ticker]),
            "momentum_weight": str(momentum_weight), "provider": MOMENTUM_VOLUME_PROVIDER_TYPE,
        }) for index, (row, score) in enumerate(ranked, start=1))
        return StrategyResult(self.spec, as_of_trade_date, picks)


@dataclass(frozen=True)
class MLRidgeScoreProvider:
    """Rank a frozen Ridge artifact without allowing it to select position size or execution."""

    spec: StrategySpec
    model: MLShadowModel

    def score(self, features: Iterable[FeatureRow], as_of_trade_date: date) -> StrategyResult:
        if self.model.trained_through_date >= as_of_trade_date:
            raise ValueError("ML model cannot score on or before its training cutoff")
        top_n = _positive_top_n(self.spec.parameters)
        rows = tuple(features)
        momentums = [float(row.momentum) for row in rows]
        percentiles, zscores = _float_cross_section_statistics(momentums)
        scored = []
        for index, row in enumerate(rows):
            values = {
                "momentum_5d": float(row.momentum_5d), "momentum_20d": float(row.momentum),
                "sma20_deviation": float((row.close / row.sma) - Decimal("1")) if row.sma else 0.0,
                "volume_ratio_20d": float(row.volume_ratio),
                "momentum_20d_percentile": percentiles[index], "momentum_20d_zscore": zscores[index],
            }
            scored.append((row, self.model.predict(values)))
        ranked = sorted(scored, key=lambda item: (item[1], item[0].ticker), reverse=True)[:top_n]
        picks = tuple(Recommendation(row.ticker, index, Decimal(str(score)), row.close, {
            "provider": ML_RIDGE_PROVIDER_TYPE, "predicted_excess_return_5d": str(score),
            "model_sha256": self.model.artifact_sha256,
        }) for index, (row, score) in enumerate(ranked, start=1))
        return StrategyResult(self.spec, as_of_trade_date, picks)


@dataclass(frozen=True)
class MLRidgeMarketGuardScoreProvider:
    """Fail closed on new ML entries unless the benchmark is in a risk-on trend.

    This is a research-only entry gate. It deliberately leaves exits and existing
    positions to the shared replay engine, so the experiment changes only new buys.
    """

    spec: StrategySpec
    model: MLShadowModel
    benchmark_closes: Mapping[date, Decimal]
    lookback_days: int = 20

    def score(self, features: Iterable[FeatureRow], as_of_trade_date: date) -> StrategyResult:
        if not self._is_risk_on(as_of_trade_date):
            return StrategyResult(self.spec, as_of_trade_date, ())
        base_spec = StrategySpec(self.spec.strategy_id, self.spec.strategy_version,
                                 ML_RIDGE_PROVIDER_TYPE, self.spec.parameters_json)
        result = MLRidgeScoreProvider(base_spec, self.model).score(features, as_of_trade_date)
        return StrategyResult(self.spec, as_of_trade_date, result.recommendations)

    def _is_risk_on(self, as_of_trade_date: date) -> bool:
        days = sorted(day for day in self.benchmark_closes if day <= as_of_trade_date)[-self.lookback_days:]
        if len(days) != self.lookback_days or days[-1] != as_of_trade_date:
            return False
        closes = [self.benchmark_closes[day] for day in days]
        return closes[-1] > sum(closes, Decimal("0")) / self.lookback_days and closes[-1] > closes[0]


def baseline_strategy_spec(volume_multiple: Decimal, top_n: int,
                           strategy_id: str = "historical_momentum_v1") -> StrategySpec:
    """Build the previous replay baseline as an explicit immutable specification."""
    parameters = {"top_n": top_n, "volume_multiple": str(volume_multiple)}
    return StrategySpec(strategy_id, "baseline_v1", BASELINE_PROVIDER_TYPE,
                        json.dumps(parameters, sort_keys=True, separators=(",", ":")))


def pure_momentum_strategy_spec(top_n: int) -> StrategySpec:
    return StrategySpec("pure_momentum_v1", "v1", PURE_MOMENTUM_PROVIDER_TYPE,
                        json.dumps({"top_n": top_n}, sort_keys=True, separators=(",", ":")))


def momentum_volume_strategy_spec(top_n: int, momentum_weight: Decimal = Decimal("0.7")) -> StrategySpec:
    return StrategySpec("momentum_volume_composite_v1", "v1", MOMENTUM_VOLUME_PROVIDER_TYPE,
                        json.dumps({"top_n": top_n, "momentum_weight": str(momentum_weight)}, sort_keys=True, separators=(",", ":")))


def resolve_score_provider(spec: StrategySpec) -> ScoreProvider:
    """Resolve only registered providers; unknown research specs fail closed."""
    if spec.provider_type == BASELINE_PROVIDER_TYPE:
        return BaselineScoreProvider(spec)
    if spec.provider_type == PURE_MOMENTUM_PROVIDER_TYPE:
        return PureMomentumScoreProvider(spec)
    if spec.provider_type == MOMENTUM_VOLUME_PROVIDER_TYPE:
        return MomentumVolumeScoreProvider(spec)
    if spec.provider_type == ML_RIDGE_PROVIDER_TYPE:
        parameters = spec.parameters
        try:
            model = MLShadowModel.load(Path(str(parameters["model_path"])))
            expected_hash = str(parameters["model_sha256"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("ML Ridge strategy requires model_path and model_sha256") from error
        if model.artifact_sha256 != expected_hash:
            raise ValueError("ML Ridge model artifact hash does not match strategy specification")
        return MLRidgeScoreProvider(spec, model)
    raise ValueError(f"unsupported score provider: {spec.provider_type}")


def ml_ridge_strategy_spec(model_path: str, model_sha256: str, top_n: int) -> StrategySpec:
    return StrategySpec("ml_ridge_excess_return_shadow_v1", "ridge_v1", ML_RIDGE_PROVIDER_TYPE,
                        json.dumps({"model_path": model_path, "model_sha256": model_sha256, "top_n": top_n},
                                   sort_keys=True, separators=(",", ":")))


def ml_ridge_market_guard_strategy_spec(top_n: int, lookback_days: int = 20) -> StrategySpec:
    if lookback_days < 2:
        raise ValueError("market guard lookback must be at least two days")
    return StrategySpec("ml_ridge_market_guard_research_v1", "ridge_guard_v1", ML_RIDGE_MARKET_GUARD_PROVIDER_TYPE,
                        json.dumps({"top_n": top_n, "benchmark": "000300.SH", "lookback_days": lookback_days,
                                    "entry_rule": "close_above_sma20_and_positive_20d_return"},
                                   sort_keys=True, separators=(",", ":")))


def _positive_top_n(parameters: Mapping[str, object]) -> int:
    try:
        top_n = int(parameters["top_n"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("strategy requires a positive top_n") from error
    if top_n <= 0:
        raise ValueError("strategy requires a positive top_n")
    return top_n


def _percentile_ranks(rows: Iterable[FeatureRow], field: str) -> Mapping[str, Decimal]:
    values = sorted({Decimal(str(getattr(row, field))) for row in rows})
    if not values:
        return {}
    denominator = Decimal(max(len(values) - 1, 1))
    by_value = {value: Decimal(index) / denominator for index, value in enumerate(values)}
    return {row.ticker: by_value[Decimal(str(getattr(row, field)))] for row in rows}


def _float_cross_section_statistics(values):
    if not values:
        return [], []
    ordered = sorted(values)
    percentiles = [(sum(value >= candidate for candidate in ordered) - 0.5) / len(values) for value in values]
    mean = sum(values) / len(values)
    scale = sum((value - mean) ** 2 for value in values) ** 0.5 / len(values) ** 0.5
    return percentiles, [(value - mean) / scale if scale else 0.0 for value in values]
