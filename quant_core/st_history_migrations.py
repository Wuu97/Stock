"""Idempotent storage for resumable third-party historical ST evidence."""


def apply_st_history_migrations(connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS st_history_backfill_runs ("
        "backfill_run_id VARCHAR PRIMARY KEY, source_channel VARCHAR NOT NULL, start_trade_date DATE NOT NULL, "
        "end_trade_date DATE NOT NULL, source_policy_version VARCHAR NOT NULL, created_at TIMESTAMPTZ NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS st_history_backfill_state ("
        "backfill_run_id VARCHAR NOT NULL REFERENCES st_history_backfill_runs(backfill_run_id), ticker VARCHAR NOT NULL, "
        "status VARCHAR NOT NULL, attempt_count INTEGER NOT NULL, row_count INTEGER, raw_artifact_path VARCHAR, "
        "raw_artifact_sha256 VARCHAR, error_text VARCHAR, updated_at TIMESTAMPTZ NOT NULL, "
        "PRIMARY KEY (backfill_run_id, ticker))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS st_history_daily ("
        "backfill_run_id VARCHAR NOT NULL REFERENCES st_history_backfill_runs(backfill_run_id), ticker VARCHAR NOT NULL, "
        "trade_date DATE NOT NULL, is_st BOOLEAN NOT NULL, raw_artifact_path VARCHAR NOT NULL, "
        "raw_artifact_sha256 VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, "
        "PRIMARY KEY (backfill_run_id, ticker, trade_date))"
    )
