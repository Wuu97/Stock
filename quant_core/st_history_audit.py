"""Deterministic helpers for sampling BaoStock ST evidence against Tushare names."""

from datetime import date
from typing import Mapping, Optional, Sequence


def namechange_st_at(rows: Sequence[Mapping[str, object]], trade_date: date) -> Optional[bool]:
    """Return the active Tushare name's ST flag, or ``None`` when no interval matches.

    Tushare's ``namechange`` is only an independent consistency proxy: a name can
    change for reasons other than ST status.  It must never overwrite the archived
    BaoStock daily supplier fact.
    """
    candidates = []
    for row in rows:
        raw_start = str(row.get("start_date") or "")
        if len(raw_start) != 8:
            continue
        start = date(int(raw_start[:4]), int(raw_start[4:6]), int(raw_start[6:]))
        raw_end = str(row.get("end_date") or "")
        end = date.max if not raw_end else date(int(raw_end[:4]), int(raw_end[4:6]), int(raw_end[6:]))
        if start <= trade_date <= end:
            candidates.append((start, str(row.get("name") or "")))
    if not candidates:
        return None
    _, name = max(candidates, key=lambda item: item[0])
    return "ST" in name.upper()
