from datetime import date

import pytest

from quant_core.historical_market_evidence import canonical_daily_evidence


def _daily(ticker="000001.SZ"):
    return [{"ts_code": ticker, "trade_date": "20200102", "open": "10", "high": "11", "low": "9", "close": "10.5", "vol": "1", "amount": "2"}]


def _basic(ticker="000001.SZ", total_mv="100"):
    return [{"ts_code": ticker, "trade_date": "20200102", "total_mv": total_mv}]


def _factor(ticker="000001.SZ"):
    return [{"ts_code": ticker, "trade_date": "20200102", "adj_factor": "1"}]


def _limit(ticker="000001.SZ"):
    return [{"ts_code": ticker, "trade_date": "20200102", "up_limit": "11.55", "down_limit": "9.45"}]


def test_canonical_market_evidence_joins_all_required_daily_facts():
    bars, caps, evidence = canonical_daily_evidence(date(2020, 1, 2), _daily(), _basic(), _factor(), _limit())
    assert bars[0].ticker == "000001.SZ"
    assert caps["000001.SZ"] == 1_000_000
    assert evidence["request_identity"]["trade_date"] == "2020-01-02"


@pytest.mark.parametrize("endpoint", ("basic", "factor"))
def test_market_evidence_rejects_missing_per_ticker_join(endpoint):
    inputs = {"basic": _basic(), "factor": _factor(), "limit": _limit()}
    inputs[endpoint] = []
    with pytest.raises(ValueError, match="incomplete"):
        canonical_daily_evidence(date(2020, 1, 2), _daily(), inputs["basic"], inputs["factor"], inputs["limit"])


def test_market_evidence_rejects_duplicate_provider_key():
    with pytest.raises(ValueError, match="duplicate"):
        canonical_daily_evidence(date(2020, 1, 2), _daily() * 2, _basic(), _factor(), _limit())


def test_price_limit_coverage_is_required_for_execution_candidate_domain_only():
    _, _, evidence = canonical_daily_evidence(date(2020, 1, 2), _daily(), _basic(), _factor(), [])
    assert evidence["coverage_contract"]["price_limits_required_for"] == "current_market_cap_at_or_above_threshold"
    with pytest.raises(ValueError, match="stk_limit is incomplete"):
        canonical_daily_evidence(date(2020, 1, 2), _daily(), _basic(total_mv="8000000"), _factor(), [])
