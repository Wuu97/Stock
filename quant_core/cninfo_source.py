"""Direct adapter for official CNINFO listed-company disclosure metadata."""

from dataclasses import dataclass
from dataclasses import replace
from io import BytesIO
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pypdf import PdfReader

from .news import NewsDocument


CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC_BASE_URL = "https://static.cninfo.com.cn/"
MAX_ANNOUNCEMENT_TEXT_CHARS = 120_000


@dataclass(frozen=True)
class CninfoFetch:
    documents: tuple[NewsDocument, ...]
    request_key_sha256: str
    artifact_path: str
    artifact_sha256: str
    attempts: tuple[tuple[int, int, float, Optional[str]], ...]


@dataclass(frozen=True)
class CninfoPdfFetch:
    document: NewsDocument
    request_key_sha256: str
    artifact_path: str
    artifact_sha256: str
    attempts: tuple[tuple[int, int, float, Optional[str]], ...]


def cninfo_announcements_cached(notice_date: date, tickers: tuple[str, ...], received_at: datetime,
                                cache_dir: Path) -> CninfoFetch:
    """Archive official disclosures for a bounded, user-relevant A-share ticker set."""
    if received_at.tzinfo is None:
        raise ValueError("received_at must include a timezone")
    codes = tuple(sorted({_code(ticker) for ticker in tickers}))
    if not codes:
        raise ValueError("CNINFO requires at least one A-share ticker")
    request_key = sha256(f"cninfo_announcements_{notice_date:%Y%m%d}_{','.join(codes)}".encode("utf-8")).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = cache_dir / f"cninfo_{request_key}.json"
    if artifact_path.exists():
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        return _result(payload, received_at, request_key, artifact_path, ((1, 200, 0.0, "CACHE_HIT"),))
    try:
        payload = _fetch_all_pages(notice_date, codes, cache_dir)
    except Exception as error:
        raise RuntimeError("CNINFO announcement request failed") from error
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return _result(payload, received_at, request_key, artifact_path, ((1, 200, 0.0, None),))


def _fetch_all_pages(notice_date: date, codes: tuple[str, ...], cache_dir: Path) -> dict[str, Any]:
    org_ids = _stock_org_ids(cache_dir)
    pages = []
    page_size = 30
    for code in codes:
        try:
            stock = f"{code},{org_ids[code]}"
        except KeyError as error:
            raise RuntimeError(f"CNINFO stock directory has no entry for {code}") from error
        first = _post_page(notice_date, 1, stock)
        pages.append(first)
        total = int(first.get("totalAnnouncement", 0))
        for page_number in range(2, (total + page_size - 1) // page_size + 1):
            pages.append(_post_page(notice_date, page_number, stock))
    return {"notice_date": notice_date.isoformat(), "pages": pages}


def _stock_org_ids(cache_dir: Path) -> dict[str, str]:
    path = cache_dir / "cninfo_stock_directory.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        request = Request("https://www.cninfo.com.cn/new/data/szse_stock.json", headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    entries = payload.get("stockList", [])
    result = {str(item["code"]).zfill(6): str(item["orgId"]) for item in entries
              if item.get("code") and item.get("orgId")}
    if not result:
        raise RuntimeError("CNINFO stock directory is empty")
    return result


def _post_page(notice_date: date, page_number: int, stock: str) -> dict[str, Any]:
    day = notice_date.isoformat()
    payload = {
        "pageNum": str(page_number), "pageSize": "30", "column": "szse", "tabName": "fulltext",
        "plate": "", "stock": stock, "searchkey": "", "secid": "", "category": "", "trade": "",
        "seDate": f"{day}~{day}", "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    request = Request(
        CNINFO_QUERY_URL,
        data=urlencode(payload).encode("utf-8"),
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise RuntimeError("CNINFO response is not a JSON object")
    if result.get("announcements") is None:
        result["announcements"] = []
    if not isinstance(result["announcements"], list):
        raise RuntimeError("CNINFO response is missing announcement records")
    return result


def _result(payload: dict[str, Any], received_at: datetime, request_key: str, artifact_path: Path,
            attempts: tuple[tuple[int, int, float, Optional[str]], ...]) -> CninfoFetch:
    artifact_hash = sha256(artifact_path.read_bytes()).hexdigest()
    announcements = [item for page in payload.get("pages", []) for item in page.get("announcements", [])]
    documents = tuple(_document(item, received_at, artifact_path, artifact_hash) for item in announcements)
    return CninfoFetch(documents, request_key, str(artifact_path), artifact_hash, attempts)


def cninfo_pdf_document_cached(document: NewsDocument, cache_dir: Path) -> CninfoPdfFetch:
    """Archive and extract the official disclosure PDF behind one CNINFO notice."""
    if document.source_url is None or not document.source_url.startswith(CNINFO_STATIC_BASE_URL):
        raise ValueError("CNINFO PDF extraction requires an official static.cninfo.com.cn URL")
    request_key = sha256(document.source_url.encode("utf-8")).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"cninfo_pdf_{request_key}.pdf"
    cache_hit = path.exists()
    if not cache_hit:
        try:
            payload = _get_pdf_bytes(document.source_url)
        except Exception as error:
            raise RuntimeError("CNINFO PDF request failed") from error
        path.write_bytes(payload)
    payload = path.read_bytes()
    if not payload.startswith(b"%PDF"):
        raise RuntimeError("CNINFO disclosure artifact is not a PDF")
    try:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(payload)).pages).strip()
    except Exception as error:
        raise RuntimeError("CNINFO PDF text extraction failed") from error
    if not text:
        raise RuntimeError("CNINFO PDF contains no extractable text")
    artifact_hash = sha256(payload).hexdigest()
    body = f"{document.headline}\n\n{text[:MAX_ANNOUNCEMENT_TEXT_CHARS]}"
    return CninfoPdfFetch(replace(document, body=body, raw_artifact_path=str(path), raw_artifact_sha256=artifact_hash),
                          request_key, str(path), artifact_hash,
                          ((1, 200, 0.0, "CACHE_HIT" if cache_hit else None),))


def _get_pdf_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def _document(item: dict[str, Any], received_at: datetime, artifact_path: Path, artifact_hash: str) -> NewsDocument:
    code = str(item.get("secCode", "")).zfill(6)
    title = str(item.get("announcementTitle", "")).strip()
    if not code.isdigit() or not title:
        raise RuntimeError("CNINFO announcement has no stock code or title")
    published_at = datetime.fromtimestamp(int(item["announcementTime"]) / 1000, tz=timezone.utc)
    adjunct_url = str(item.get("adjunctUrl", "")).lstrip("/")
    if not adjunct_url:
        raise RuntimeError("CNINFO announcement has no official document URL")
    source_url = CNINFO_STATIC_BASE_URL + adjunct_url
    return NewsDocument(
        source_channel="cninfo_announcements", scope="STOCK", published_at=published_at, received_at=received_at,
        headline=title, body=title, ticker=_ticker(code), external_id=str(item.get("announcementId") or adjunct_url),
        source_url=source_url, raw_artifact_path=str(artifact_path), raw_artifact_sha256=artifact_hash,
        publisher="cninfo.com.cn", source_type="OFFICIAL_DISCLOSURE", canonical_url=source_url,
        language="zh", evidence_role="EVIDENCE_ELIGIBLE",
    )


def _ticker(code: str) -> str:
    if code.startswith(("60", "68", "90")):
        exchange = "SH"
    elif code.startswith(("00", "30")):
        exchange = "SZ"
    elif code.startswith(("4", "8")):
        exchange = "BJ"
    else:
        raise RuntimeError(f"unsupported CNINFO stock code: {code}")
    return f"{code}.{exchange}"


def _code(ticker: str) -> str:
    code, separator, _ = ticker.partition(".")
    if not separator or not code.isdigit() or len(code) != 6:
        raise ValueError(f"CNINFO ticker must use six-digit A-share format: {ticker}")
    return code
