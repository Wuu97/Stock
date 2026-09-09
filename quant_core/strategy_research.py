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


def baseline_strategy_spec(volume_multiple: Decimal, top_n: int,
                           strategy_id: str = "historical_momentum_v1") -> StrategySpec:
    """Build the previous replay baseline as an explicit immutable specification."""
    parameters = {"top_n": top_n, "volume_multiple": str(volume_multiple)}
    return StrategySpec(strategy_id, "baseline_v1", BASELINE_PROVIDER_TYPE,
                        json.dumps(parameters, sort_keys=True, separators=(",", ":")))


def resolve_score_provider(spec: StrategySpec) -> ScoreProvider:
    """Resolve only registered providers; unknown research specs fail closed."""
    if spec.provider_type == BASELINE_PROVIDER_TYPE:
        return BaselineScoreProvider(spec)
    raise ValueError(f"unsupported score provider: {spec.provider_type}")
