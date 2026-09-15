from datetime import datetime, timezone
from pathlib import Path

import duckdb

from quant_core.derived_evaluation_migrations import apply_derived_evaluation_migrations


def _legacy_connection():
    c = duckdb.connect(":memory:"); c.execute(Path("sql/schema.sql").read_text())
    c.execute("DROP TABLE strategy_scorecards")
    c.execute("""CREATE TABLE strategy_scorecards (
      scorecard_id VARCHAR PRIMARY KEY,strategy_id VARCHAR NOT NULL,strategy_version VARCHAR NOT NULL,
      competition_profile_id VARCHAR NOT NULL,competition_profile_version VARCHAR NOT NULL,evaluation_stage VARCHAR NOT NULL,
      sample_start DATE NOT NULL,sample_end DATE NOT NULL,as_of_time TIMESTAMPTZ NOT NULL,evaluation_method_version VARCHAR NOT NULL,
      config_sha256 VARCHAR NOT NULL,source_experiment_id VARCHAR,source_run_id VARCHAR,metrics_json VARCHAR NOT NULL,
      metrics_sha256 VARCHAR NOT NULL,sample_status VARCHAR NOT NULL,created_at TIMESTAMPTZ NOT NULL,
      CHECK ((source_experiment_id IS NOT NULL) <> (source_run_id IS NOT NULL)),
      UNIQUE(source_experiment_id,evaluation_method_version),UNIQUE(source_run_id,evaluation_method_version))""")
    return c


def test_legacy_scorecards_migrate_in_place_idempotently_without_row_changes():
    c = _legacy_connection(); now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    c.execute("INSERT INTO sim_accounts VALUES ('a','a','CNY',1,'r','f','ACTIVE',?)", [now])
    c.execute("INSERT INTO strategy_experiments VALUES ('e','a','{}','cfg',?)", [now])
    c.execute("INSERT INTO competition_profiles VALUES ('p','v1','{}','h',?)", [now])
    c.execute("INSERT INTO strategy_scorecards VALUES ('s','baseline_v1','v1','p','v1','BACKTEST','2026-01-01','2026-01-02',?,'m','cfg','e',NULL,'{}','h','LOW_SAMPLE',?)", [now, now])
    before = c.execute("SELECT * FROM strategy_scorecards").fetchall()
    apply_derived_evaluation_migrations(c)
    assert c.execute("SELECT * EXCLUDE(source_evaluation_id) FROM strategy_scorecards").fetchall() == before
    assert c.execute("SELECT source_evaluation_id FROM strategy_scorecards").fetchone()[0] is None
    apply_derived_evaluation_migrations(c)
    assert c.execute("SELECT count(*) FROM strategy_scorecards").fetchone()[0] == 1
    assert {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='strategy_scorecards'").fetchall()} >= {'source_evaluation_id'}
    assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='strategy_evaluation_results'").fetchone()[0] == 1
