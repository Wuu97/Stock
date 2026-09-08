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
    qualified = [
        row for row in features
        if row.close > row.sma and row.volume_ratio >= config.volume_multiple
    ]
    ranked = sorted(qualified, key=lambda row: (row.momentum, row.ticker), reverse=True)[:config.top_n]
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
