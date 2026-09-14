from datetime import date

from quant_core.st_history_audit import namechange_st_at


def test_namechange_st_check_uses_the_active_interval():
    rows = [
        {"name": "Normal", "start_date": "20200101", "end_date": "20201231"},
        {"name": "*ST Sample", "start_date": "20210101", "end_date": "20211231"},
        {"name": "Recovered", "start_date": "20220101", "end_date": ""},
    ]
    assert namechange_st_at(rows, date(2020, 6, 1)) is False
    assert namechange_st_at(rows, date(2021, 6, 1)) is True
    assert namechange_st_at(rows, date(2024, 6, 1)) is False


def test_namechange_st_check_is_inconclusive_when_no_interval_matches():
    assert namechange_st_at([{"name": "Normal", "start_date": "20210101", "end_date": "20211231"}],
                            date(2023, 1, 1)) is None
