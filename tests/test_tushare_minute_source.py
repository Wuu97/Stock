from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from quant_core.tushare_minute_source import minute_records_to_bars, realtime_minute_records_to_bars


def test_minute_records_are_normalized_as_completed_shanghai_bars():
    bars = minute_records_to_bars("600000.SH", "5min", ({
        "ts_code": "600000.SH", "trade_time": "2026-09-11 09:35:00", "open": "10.00", "close": "10.10",
        "high": "10.20", "low": "9.90", "vol": "100", "amount": "1000",
    },))
    assert bars[0].bar_start_at == datetime(2026, 9, 11, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert bars[0].bar_end_at == datetime(2026, 9, 11, 9, 35, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert bars[0].close == Decimal("10.10")


def test_60_minute_records_are_supported():
    bars = minute_records_to_bars("600000.SH", "60min", ({
        "ts_code": "600000.SH", "trade_time": "2026-09-11 10:30:00", "open": "10", "close": "10",
        "high": "10", "low": "10", "vol": "100", "amount": "1000",
    },))
    assert bars[0].bar_start_at == datetime(2026, 9, 11, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_realtime_records_use_completed_bar_time_and_support_multiple_tickers():
    bars = realtime_minute_records_to_bars("15min", (
        {"ts_code": "000001.SZ", "time": "2026-09-11 09:45:00", "open": "10", "close": "10.1", "high": "10.2", "low": "9.9", "vol": "100", "amount": "1000"},
        {"ts_code": "600000.SH", "time": "2026-09-11 09:45:00", "open": "8", "close": "8.1", "high": "8.2", "low": "7.9", "vol": "100", "amount": "800"},
    ))
    assert [bar.ticker for bar in bars] == ["000001.SZ", "600000.SH"]
    assert bars[0].bar_start_at == datetime(2026, 9, 11, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
