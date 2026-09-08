from decimal import Decimal

import pytest

from quant_core.ledger import ZERO, buy_entries, initial_funding, sell_entries, validate_entries
from quant_core.models import JournalEntry


def test_initial_funding_and_trade_entries_are_balanced():
    for entries in (
        initial_funding(Decimal("1000000")),
        buy_entries(Decimal("100000"), Decimal("25"), Decimal("1"), "600519.SH"),
        sell_entries(Decimal("44000"), Decimal("40000"), Decimal("11"), Decimal("22"), Decimal("0.44"), "600519.SH"),
    ):
        assert sum(entry.debit for entry in entries) == sum(entry.credit for entry in entries)


def test_unbalanced_and_negative_journals_are_rejected():
    with pytest.raises(ValueError):
        validate_entries([JournalEntry("1001", Decimal("1"), ZERO, "bad")])
    with pytest.raises(ValueError):
        validate_entries([
            JournalEntry("1001", Decimal("-1"), ZERO, "bad"),
            JournalEntry("3001", ZERO, Decimal("-1"), "bad"),
        ])
