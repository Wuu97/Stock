"""Validated persistence for versioned, source-backed SW industry membership."""

from datetime import datetime
from typing import Iterable, Mapping


def taxonomy_rows(records: Iterable[Mapping[str, object]]):
    """Convert raw Tushare SW2021 L3 rows into immutable taxonomy records."""
    rows = []
    for record in records:
        code, name, level, source = (str(record.get(field, "")) for field in ("industry_code", "industry_name", "level", "src"))
        if not code or not name or level != "L3" or source != "SW2021":
            raise ValueError("Tushare SW taxonomy row is incomplete or not SW2021 L3")
        rows.append((code, name, 3))
    if not rows or len({row[0] for row in rows}) != len(rows):
        raise ValueError("Tushare SW taxonomy must contain unique L3 industry codes")
    return tuple(rows)


def membership_rows(records: Iterable[Mapping[str, object]], code_by_index: Mapping[str, str]):
    """Use source-provided in/out dates; never infer membership history."""
    rows = []
    for record in records:
        ticker, l3_index, in_date = (str(record.get(field, "") or "") for field in ("ts_code", "l3_code", "in_date"))
        if not ticker or not l3_index or not in_date or l3_index not in code_by_index:
            raise ValueError("Tushare SW membership row is incomplete or references an unknown L3 index")
        try:
            valid_from = datetime.strptime(in_date, "%Y%m%d").date()
            out_date = record.get("out_date")
            valid_to = datetime.strptime(str(out_date), "%Y%m%d").date() if out_date else None
        except ValueError as error:
            raise ValueError("Tushare SW membership dates are invalid") from error
        if valid_to is not None and valid_to < valid_from:
            raise ValueError("Tushare SW membership has an invalid date range")
        rows.append((ticker, code_by_index[l3_index], valid_from, valid_to))
    if not rows:
        raise ValueError("Tushare SW membership response is empty")
    return tuple(rows)


def store_taxonomy_version(connection, version: str, taxonomy, memberships) -> None:
    """Append one complete version atomically; a version is never overwritten."""
    if connection.execute("SELECT 1 FROM sw_industry_taxonomy WHERE taxonomy_version = ?", [version]).fetchone():
        raise ValueError("taxonomy version already exists")
    connection.execute("BEGIN")
    try:
        connection.executemany("INSERT INTO sw_industry_taxonomy VALUES (?, ?, ?, ?)", [(version, *row) for row in taxonomy])
        connection.executemany("INSERT INTO security_industry_memberships VALUES (?, ?, ?, ?, ?)", [(version, *row) for row in memberships])
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
