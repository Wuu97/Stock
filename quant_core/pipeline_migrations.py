"""Idempotent schema upgrades for daily pipeline audit records."""


def apply_pipeline_migrations(connection) -> None:
    connection.execute("ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS config_json VARCHAR")
    connection.execute("UPDATE pipeline_runs SET config_json = '{}' WHERE config_json IS NULL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS pipeline_run_stage_attempts ("
        "pipeline_run_id VARCHAR NOT NULL, stage_name VARCHAR NOT NULL, attempt_number INTEGER NOT NULL, "
        "execution_class VARCHAR NOT NULL, stage_status VARCHAR NOT NULL, started_at TIMESTAMPTZ NOT NULL, "
        "completed_at TIMESTAMPTZ, error_text VARCHAR, artifact_reference VARCHAR, "
        "PRIMARY KEY (pipeline_run_id, stage_name, attempt_number))"
    )
