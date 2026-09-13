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


def test_zero_pnl_sale_does_not_write_an_invalid_zero_gain_entry():
    entries = sell_entries(Decimal("100"), Decimal("100"), Decimal("0"), Decimal("0"), Decimal("0"), "512400.SH")
    assert all(entry.account_code != "5001" for entry in entries)
