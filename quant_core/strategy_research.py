"""Strategy scoring contracts shared by research replays and future production runs."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json
from typing import Iterable, Mapping, Protocol, Tuple

from .features import FeatureRow
from .strategy import BaselineConfig, Recommendation, select_baseline


FEATURE_SCHEMA_VERSION = "daily_features_v1"
BASELINE_PROVIDER_TYPE = "BASELINE_MOMENTUM_TREND"
PURE_MOMENTUM_PROVIDER_TYPE = "PURE_MOMENTUM"
MOMENTUM_VOLUME_PROVIDER_TYPE = "MOMENTUM_VOLUME_COMPOSITE"


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
        ranked = sorted(scored, key=lambda item: (item[1], item[0].ticker), reverse=True)[:top_n]
        picks = tuple(Recommendation(row.ticker, index, score, row.close, {
            "momentum": str(row.momentum), "volume_ratio": str(row.volume_ratio),
            "momentum_percentile": str(momentum_ranks[row.ticker]), "volume_percentile": str(volume_ranks[row.ticker]),
            "momentum_weight": str(momentum_weight), "provider": MOMENTUM_VOLUME_PROVIDER_TYPE,
        }) for index, (row, score) in enumerate(ranked, start=1))
        return StrategyResult(self.spec, as_of_trade_date, picks)


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
    raise ValueError(f"unsupported score provider: {spec.provider_type}")


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
