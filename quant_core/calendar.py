from datetime import date
from typing import Iterable, Tuple


class TradingCalendar:
    """A fixed local trading calendar; callers provide all known trading dates."""

    def __init__(self, trading_days: Iterable[date]):
        self._days: Tuple[date, ...] = tuple(sorted(set(trading_days)))
        if not self._days:
            raise ValueError("trading calendar cannot be empty")

    def next_trading_day(self, current: date) -> date:
        for trading_day in self._days:
            if trading_day > current:
                return trading_day
        raise ValueError("no next trading day in the fixed calendar")
