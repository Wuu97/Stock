"""ETF adapters that preserve the distinction between research and executable bars."""

from datetime import datetime
from decimal import Decimal
from typing import Iterable, Mapping, Tuple

from .models import DayBar


def fund_daily_to_bars(rows: Iterable[Mapping[str, object]]) -> Tuple[DayBar, ...]:
    """Normalize Tushare fund-daily rows for research only.

    This endpoint does not provide official daily limit prices. Limit fields stay
    null, so strict execution rejects these bars rather than deriving a limit.
    """
    bars = []
    for row in rows:
        required = ("ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount")
        if any(key not in row or row[key] is None for key in required):
            raise ValueError("fund_daily row is missing required OHLCV fields")
        bars.append(DayBar(
            datetime.strptime(str(row["trade_date"]), "%Y%m%d").date(), str(row["ts_code"]),
            Decimal(str(row["open"])), Decimal(str(row["high"])), Decimal(str(row["low"])), Decimal(str(row["close"])),
            int(Decimal(str(row["vol"])) * Decimal("100")), Decimal(str(row["amount"])) * Decimal("1000"),
            None, None,
        ))
    return tuple(bars)
