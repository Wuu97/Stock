from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pytest

from quant_core.security_master import SecurityMasterStore, security_name_rows
from quant_core.security_eligibility import SecurityEligibilityStore, listing_rows
from quant_core.baostock_st_source import baostock_code


def test_security_master_normalizes_names_and_preserves_provenance():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    rows = security_name_rows(({"ts_code": "600000.SH", "name": "浦发银行"}, {"ts_code": "600000.SH", "name": "浦发银行"}))
    now = datetime.now(timezone.utc)
    assert SecurityMasterStore(connection).upsert(rows, "fixture", "fixture.json", "a" * 64, now, now) == 1
    assert connection.execute("SELECT security_name, source_channel FROM security_master").fetchone() == ("浦发银行", "fixture")


def test_security_master_rejects_incomplete_vendor_rows():
    with pytest.raises(ValueError):
        security_name_rows(({"ts_code": "600000.SH", "name": ""},))


def test_listing_snapshot_keeps_delisted_securities_and_listing_dates():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    rows = listing_rows((
        {"ts_code": "600000.SH", "list_date": "19991110", "delist_date": "nan", "list_status": "L"},
        {"ts_code": "600001.SH", "list_date": "19900101", "delist_date": "20200101", "list_status": "D"},
    ))
    now = datetime.now(timezone.utc)
    assert SecurityEligibilityStore(connection).store_listing_snapshot(
        "listing_fixture", "fixture", "fixture.json", "b" * 64, now, rows, now,
    ) == 2
    assert connection.execute(
        "SELECT ticker, list_date, delist_date, list_status FROM security_listing_values ORDER BY ticker"
    ).fetchall() == [
        ("600000.SH", date(1999, 11, 10), None, "L"),
        ("600001.SH", date(1990, 1, 1), date(2020, 1, 1), "D"),
    ]


def test_baostock_code_preserves_exchange_and_rejects_unsupported_market():
    assert baostock_code("600000.SH") == "sh.600000"
    assert baostock_code("000001.SZ") == "sz.000001"
    with pytest.raises(ValueError):
        baostock_code("430001.BJ")
