"""Persist frozen, explainable recommendations without changing strategy state."""

from datetime import datetime
import json
from typing import Iterable
from uuid import uuid4

from .strategy import Recommendation


def store_recommendations(connection, run_id: str, recommendations: Iterable[Recommendation], created_at: datetime) -> None:
    status = connection.execute("SELECT run_status FROM recommendation_runs WHERE run_id = ?", [run_id]).fetchone()
    if status is None or status[0] != "FROZEN":
        raise ValueError("recommendations require a frozen run")
    rows = [
        (str(uuid4()), run_id, item.ticker, item.rank, item.score, item.close,
         json.dumps(item.reasons, sort_keys=True), created_at)
        for item in recommendations
    ]
    if rows:
        connection.executemany("INSERT INTO recommendation_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
