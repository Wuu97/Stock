from datetime import date
from pathlib import Path

import duckdb
import pytest

from quant_core.industry_taxonomy import membership_rows, store_taxonomy_version, taxonomy_rows


def test_sw_taxonomy_version_is_append_only_and_uses_source_dates():
    taxonomy = taxonomy_rows([{"industry_code": "220202", "industry_name": "稀有金属", "level": "L3", "src": "SW2021"}])
    members = membership_rows([{"ts_code": "600000.SH", "l3_code": "850000.SI", "in_date": "20200102", "out_date": None}], {"850000.SI": "220202"})
    assert members == (("600000.SH", "220202", date(2020, 1, 2), None),)
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    store_taxonomy_version(connection, "SW2021_fixture", taxonomy, members)
    assert connection.execute("SELECT ticker, industry_code FROM security_industry_memberships").fetchone() == ("600000.SH", "220202")
    with pytest.raises(ValueError, match="already exists"):
        store_taxonomy_version(connection, "SW2021_fixture", taxonomy, members)


def test_incomplete_source_membership_fails_closed():
    with pytest.raises(ValueError, match="incomplete"):
        membership_rows([{"ts_code": "600000.SH", "l3_code": "unknown", "in_date": "20200102"}], {})
