from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.models import DayBar, FeeModel
from quant_core.track_evaluation import evaluate_tracks
from quant_core.dashboard import load_track_evaluations


def _bar(day, ticker, open_price, close):
    return DayBar(day, ticker, open_price, open_price + 1, open_price - 1, close, 1000, Decimal("1000"),
                  open_price + 2, open_price - 2)


def test_track_evaluation_compares_production_and_shadow_without_orders():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now, target = datetime(2026, 9, 1, tzinfo=timezone.utc), date(2026, 9, 2)
    connection.execute("INSERT INTO feature_snapshots VALUES ('feature', '2026-09-01', 20, 'input', 'artifact', 'hash', ?, ?)", [now, now])
    for run_id in ("base", "shadow"):
        connection.execute("INSERT INTO recommendation_runs VALUES (?, ?, 'strategy', 'v', 'cost', 'feature', ?, 'FROZEN', NULL, ?)", [run_id, target, now, now])
    connection.execute("INSERT INTO recommendation_run_modes VALUES ('shadow', 'SHADOW', ?)", [now])
    connection.execute("INSERT INTO recommendation_items VALUES ('base-item', 'base', 'AAA', 1, 1, 10, '{}', ?), ('shadow-item', 'shadow', 'BBB', 1, 1, 10, '{}', ?)", [now, now])
    bars = [_bar(target, "AAA", Decimal("10"), Decimal("10")), _bar(target, "BBB", Decimal("10"), Decimal("10")),
            _bar(target, "BENCH", Decimal("100"), Decimal("100"))]
    for offset in range(1, 21):
        day = target + timedelta(days=offset)
        bars.extend((_bar(day, "AAA", Decimal("11"), Decimal("11")),
                     _bar(day, "BBB", Decimal("9"), Decimal("9")),
                     _bar(day, "BENCH", Decimal("100"), Decimal("100"))))
    fee = FeeModel("cost", Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
    summary = evaluate_tracks(connection, ["base", "shadow"], bars, "BENCH", fee, "track_v1")
    assert summary["PRODUCTION"]["t1_average_return"] == Decimal("0.1")
    assert summary["SHADOW"]["t1_average_return"] == Decimal("-0.1")
    assert connection.execute("SELECT count(*) FROM sim_order_intents").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM track_evaluation_details").fetchone()[0] == 2
    comparison = load_track_evaluations(connection)
    assert {row["mode"] for row in comparison} == {"PRODUCTION", "SHADOW"}


def test_track_evaluation_waits_for_a_complete_t20_path():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now, target = datetime(2026, 9, 1, tzinfo=timezone.utc), date(2026, 9, 2)
    connection.execute("INSERT INTO feature_snapshots VALUES ('feature', '2026-09-01', 20, 'input', 'artifact', 'hash', ?, ?)", [now, now])
    connection.execute("INSERT INTO recommendation_runs VALUES ('base', ?, 'strategy', 'v', 'cost', 'feature', ?, 'FROZEN', NULL, ?)", [target, now, now])
    connection.execute("INSERT INTO recommendation_items VALUES ('item', 'base', 'AAA', 1, 1, 10, '{}', ?)", [now])
    bars = [_bar(target, "AAA", Decimal("10"), Decimal("10")), _bar(target, "BENCH", Decimal("100"), Decimal("100")),
            _bar(target + timedelta(days=1), "AAA", Decimal("11"), Decimal("11")),
            _bar(target + timedelta(days=1), "BENCH", Decimal("100"), Decimal("100"))]
    fee = FeeModel("cost", Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
    summary = evaluate_tracks(connection, ["base"], bars, "BENCH", fee, "track_v1")
    assert summary["PRODUCTION"]["pending_maturity_count"] == 1
    assert connection.execute("SELECT count(*) FROM performance_evaluations").fetchone()[0] == 0
