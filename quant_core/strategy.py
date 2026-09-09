"""First explainable baseline: trend, volume confirmation, then momentum ranking."""

from dataclasses import dataclass
from typing import Iterable, List

from .features import FeatureRow


@dataclass(frozen=True)
class BaselineConfig:
    strategy_id: str
    volume_multiple: float
    top_n: int


@dataclass(frozen=True)
class Recommendation:
    ticker: str
    rank: int
    score: object
    close: object
    reasons: dict


def select_baseline(features: Iterable[FeatureRow], config: BaselineConfig) -> List[Recommendation]:
    return rank_baseline(features, config)[:config.top_n]


def rank_baseline(features: Iterable[FeatureRow], config: BaselineConfig) -> List[Recommendation]:
    """Rank every rule-qualified security; portfolio selection remains a separate concern."""
    qualified = [
        row for row in features
        if row.close > row.sma and row.volume_ratio >= config.volume_multiple
    ]
    ranked = sorted(qualified, key=lambda row: (row.momentum, row.ticker), reverse=True)
    return [
        Recommendation(
            ticker=row.ticker,
            rank=index,
            score=row.momentum,
            close=row.close,
            reasons={
                "close": str(row.close),
                "sma": str(row.sma),
                "momentum": str(row.momentum),
                "volume_ratio": str(row.volume_ratio),
            },
        )
        for index, row in enumerate(ranked, start=1)
    ]
