"""Instrument-specific execution rules resolved from auditable reference data."""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Optional

from .models import FeeModel


@dataclass(frozen=True)
class SecurityExecutionRules:
    instrument_type: str
    settlement_cycle: str
    board_lot: int
    price_tick: Decimal
    price_limit_ratio: Optional[Decimal]
    sell_stamp_duty_rate: Optional[Decimal]

    def fee(self, base_fee: FeeModel) -> FeeModel:
        """Apply an instrument override only when reference data explicitly supplies one."""
        return base_fee if self.sell_stamp_duty_rate is None else replace(base_fee, stamp_duty_rate=self.sell_stamp_duty_rate)


# Unknown securities still get A-share trading constraints, but fees remain governed
# by the versioned FeeModel instead of silently injecting a hard-coded tax rate.
DEFAULT_A_SHARE_RULES = SecurityExecutionRules("A_SHARE", "T1", 100, Decimal("0.01"), Decimal("0.10"), None)


def execution_rules(connection, ticker: str) -> SecurityExecutionRules:
    """Resolve a ticker's rules; legacy/unclassified symbols retain the A-share default."""
    row = connection.execute(
        "SELECT instrument_type, settlement_cycle, board_lot, price_tick, price_limit_ratio, sell_stamp_duty_rate "
        "FROM security_master WHERE ticker = ?", [ticker]
    ).fetchone()
    if row is None:
        return DEFAULT_A_SHARE_RULES
    instrument_type, cycle, lot, tick, limit_ratio, stamp_rate = row
    return SecurityExecutionRules(str(instrument_type), str(cycle), int(lot), Decimal(str(tick)),
                                  None if limit_ratio is None else Decimal(str(limit_ratio)), Decimal(str(stamp_rate)))
