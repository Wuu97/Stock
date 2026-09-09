from decimal import Decimal

from quant_core.models import FeeModel
from quant_core.portfolio import PortfolioPolicy, construct_buys
from quant_core.strategy import Recommendation


FEE = FeeModel("test", Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))


def _pick(ticker, close):
    return Recommendation(ticker, 1, Decimal("0"), Decimal(close), {})


def test_fixed_shares_preserves_legacy_order_size_without_cash_screening():
    planned = construct_buys([_pick("AAA", "100")], PortfolioPolicy.fixed_shares(100), Decimal("1"), FEE)
    assert planned[0].shares == 100


def test_equal_weight_uses_reference_prices_reserve_and_board_lots():
    planned = construct_buys([_pick("AAA", "100"), _pick("BBB", "50")],
                             PortfolioPolicy.equal_weight(2, Decimal("0.1")), Decimal("100000"), FEE)
    assert [(item.ticker, item.shares, item.estimated_cash) for item in planned] == [
        ("AAA", 400, Decimal("40000.0000")), ("BBB", 900, Decimal("45000.0000")),
    ]


def test_max_positions_counts_existing_holdings_and_cash_is_left_unallocated_when_no_lot_fits():
    planned = construct_buys([_pick("AAA", "1000"), _pick("BBB", "10")], PortfolioPolicy.equal_weight(2),
                             Decimal("50"), FEE, occupied_positions=1)
    assert planned == ()
