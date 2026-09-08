"""AKShare current-market-cap adapter for the dynamic monitoring universe."""

from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import Mapping, Optional

from .news import NewsDocument


def current_total_market_caps() -> Mapping[str, Decimal]:
    """Return current A-share total market caps keyed by Tushare-style ticker."""
    try:
        import akshare as ak
    except ImportError as error:
        raise RuntimeError("install the data extra: pip install '.[data]'") from error
    try:
        frame = ak.stock_zh_a_spot_em()
    except Exception as error:
        raise RuntimeError("AKShare current-market-cap request failed; retry later or use a saved snapshot") from error
    required = {"代码", "总市值"}
    if not required.issubset(frame.columns):
        raise RuntimeError("AKShare spot response is missing code or total market cap")
    values = {}
    for _, row in frame.iterrows():
        market_cap = _decimal_or_none(row["总市值"])
        if market_cap is None:
            continue
        code = str(row["代码"]).zfill(6)
        values[f"{code}.{_exchange(code)}"] = market_cap
    if not values:
        raise RuntimeError("AKShare spot response contains no usable total market caps")
    return values


def _decimal_or_none(value: object) -> Optional[Decimal]:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def _exchange(code: str) -> str:
    return "SH" if code.startswith(("60", "68")) else "SZ" if code.startswith(("00", "30")) else "BJ"


def stock_news(symbol: str, received_at: datetime) -> tuple[NewsDocument, ...]:
    """Fetch stock-linked news facts; callers archive them before any LLM call."""
    try:
        import akshare as ak
    except ImportError as error:
        raise RuntimeError("install the data extra: pip install '.[data]'") from error
    frame = ak.stock_news_em(symbol=symbol.split(".")[0])
    required = {"新闻标题", "新闻内容", "发布时间", "新闻链接"}
    if not required.issubset(frame.columns):
        raise RuntimeError("AKShare stock news response is missing required fields")
    documents = []
    for index, row in frame.iterrows():
        published_at = _parse_news_time(row["发布时间"])
        documents.append(NewsDocument("akshare_stock_news_em", "STOCK", published_at, received_at,
                                      str(row["新闻标题"]), str(row["新闻内容"]), symbol,
                                      str(index), str(row["新闻链接"])))
    return tuple(documents)


def stock_announcements(notice_date: str, received_at: datetime) -> tuple[NewsDocument, ...]:
    """Fetch the day's A-share notices; filter by ticker only after archiving facts."""
    try:
        import akshare as ak
    except ImportError as error:
        raise RuntimeError("install the data extra: pip install '.[data]'") from error
    frame = ak.stock_notice_report(symbol="全部", date=notice_date.replace("-", ""))
    required = {"代码", "公告标题", "公告日期"}
    if not required.issubset(frame.columns):
        raise RuntimeError("AKShare announcement response is missing required fields")
    url_column = "公告链接" if "公告链接" in frame.columns else "网址" if "网址" in frame.columns else None
    return tuple(NewsDocument("akshare_stock_notice_report", "STOCK", _parse_news_time(row["公告日期"]), received_at,
                              str(row["公告标题"]), str(row["公告标题"]), _ticker_from_code(str(row["代码"])),
                              str(index), str(row[url_column]) if url_column else None) for index, row in frame.iterrows())


def cls_flash_news(received_at: datetime) -> tuple[NewsDocument, ...]:
    """Fetch Cailianpress flashes. The source shape is checked before storage."""
    try:
        import akshare as ak
    except ImportError as error:
        raise RuntimeError("install the data extra: pip install '.[data]'") from error
    frame = ak.stock_info_global_cls()
    required = {"发布时间", "内容"}
    if not required.issubset(frame.columns):
        raise RuntimeError("AKShare Cailianpress response is missing required fields")
    return tuple(NewsDocument("akshare_cls_flash", "MACRO", _parse_news_time(row["发布时间"]), received_at,
                              str(row.get("标题") or row["内容"])[:200], str(row["内容"]), external_id=str(index))
                 for index, row in frame.iterrows())


def _parse_news_time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        from zoneinfo import ZoneInfo
        return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed


def _ticker_from_code(code: str) -> str:
    normalized = code.zfill(6)
    return f"{normalized}.{_exchange(normalized)}"
