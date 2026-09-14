"""Deterministic TD Sequential (九转) setup labels for any completed-bar timeframe.

The label models the completed-close comparison sequence. The source document's
separate high/low breakout confirmation for a completed ninth bar is deliberately
not inferred by this close-only helper.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class TDSequentialState:
    """Latest completed setup state; ``count`` is capped at the documented ninth bar."""

    direction: str
    count: int
    run_length: int

    @property
    def label(self) -> str:
        return f"{self.direction}_{self.count}" if self.direction != "NONE" else "NONE"


def td_sequential_state(closes: Iterable[Decimal]) -> TDSequentialState:
    """Compare each completed close with the completed close four bars earlier.

    ``UP`` is the document's upward/high-side sequence and ``DOWN`` is its
    downward/low-side sequence. Equal closes break a setup. This function does
    not infer reversal, trade size, or precedence over another signal.
    """
    values = tuple(Decimal(str(value)) for value in closes)
    direction, run_length = "NONE", 0
    for index in range(4, len(values)):
        comparison = "UP" if values[index] > values[index - 4] else "DOWN" if values[index] < values[index - 4] else "NONE"
        if comparison == "NONE":
            direction, run_length = "NONE", 0
        elif comparison == direction:
            run_length += 1
        else:
            direction, run_length = comparison, 1
    return TDSequentialState(direction, min(run_length, 9), run_length)
