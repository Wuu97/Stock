from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from quant_core.akshare_source import _parse_news_time


def test_time_only_cls_timestamp_uses_received_shanghai_trade_date():
    received_at = datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc)
    parsed = _parse_news_time("14:35:39", received_at)

    assert parsed == datetime(2026, 9, 9, 14, 35, 39, tzinfo=ZoneInfo("Asia/Shanghai"))
