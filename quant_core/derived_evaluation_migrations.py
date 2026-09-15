"""Transactional, idempotent schema migration for Derived Evaluation Lineage V1."""


_EVALUATIONS_SQL = """
CREATE TABLE IF NOT EXISTS strategy_evaluations (
 evaluation_id VARCHAR PRIMARY KEY, source_experiment_id VARCHAR NOT NULL REFERENCES strategy_experiments(experiment_id),
 strategy_id VARCHAR NOT NULL, strategy_version VARCHAR NOT NULL, source_profile_id VARCHAR NOT NULL,
 source_profile_version VARCHAR NOT NULL, source_profile_hash VARCHAR NOT NULL, evaluation_profile_id VARCHAR NOT NULL,
 evaluation_profile_version VARCHAR NOT NULL, evaluation_profile_hash VARCHAR NOT NULL, benchmark_dataset_hash VARCHAR NOT NULL,
 execution_fingerprint VARCHAR NOT NULL, evaluation_execution_fingerprint VARCHAR NOT NULL, execution_equivalent BOOLEAN NOT NULL,
 benchmark_identifier VARCHAR NOT NULL, benchmark_version VARCHAR NOT NULL, sample_start DATE NOT NULL, sample_end DATE NOT NULL,
 evaluation_method_version VARCHAR NOT NULL, status VARCHAR NOT NULL CHECK (status IN ('PENDING','RESULT_COMPLETE','SCORECARD_COMPLETE','FAILED')),
 created_at TIMESTAMPTZ NOT NULL,
 UNIQUE(source_experiment_id,evaluation_profile_id,evaluation_profile_version,benchmark_dataset_hash,evaluation_method_version)
);
CREATE TABLE IF NOT EXISTS strategy_evaluation_results (
 evaluation_id VARCHAR PRIMARY KEY REFERENCES strategy_evaluations(evaluation_id), metrics_json VARCHAR NOT NULL,
 metrics_sha256 VARCHAR NOT NULL, created_at TIMESTAMPTZ NOT NULL
);
"""

_SCORECARDS_SQL = """
CREATE TABLE strategy_scorecards (
 scorecard_id VARCHAR PRIMARY KEY, strategy_id VARCHAR NOT NULL, strategy_version VARCHAR NOT NULL,
 competition_profile_id VARCHAR NOT NULL, competition_profile_version VARCHAR NOT NULL,
 evaluation_stage VARCHAR NOT NULL CHECK (evaluation_stage IN ('BACKTEST','SHADOW','PRODUCTION_SIM')),
 sample_start DATE NOT NULL, sample_end DATE NOT NULL, as_of_time TIMESTAMPTZ NOT NULL,
 evaluation_method_version VARCHAR NOT NULL, config_sha256 VARCHAR NOT NULL,
 source_experiment_id VARCHAR REFERENCES strategy_experiments(experiment_id),
 source_run_id VARCHAR REFERENCES recommendation_runs(run_id),
 source_evaluation_id VARCHAR REFERENCES strategy_evaluations(evaluation_id),
 metrics_json VARCHAR NOT NULL, metrics_sha256 VARCHAR NOT NULL,
 sample_status VARCHAR NOT NULL CHECK (sample_status IN ('SUFFICIENT','LOW_SAMPLE','INCOMPLETE','INVALID')),
 created_at TIMESTAMPTZ NOT NULL,
 FOREIGN KEY (competition_profile_id,competition_profile_version) REFERENCES competition_profiles(competition_profile_id,competition_profile_version),
 CHECK (sample_start <= sample_end),
 CHECK ((source_experiment_id IS NOT NULL)::INTEGER + (source_run_id IS NOT NULL)::INTEGER + (source_evaluation_id IS NOT NULL)::INTEGER = 1),
 UNIQUE(source_experiment_id,evaluation_method_version), UNIQUE(source_run_id,evaluation_method_version),
 UNIQUE(source_evaluation_id,evaluation_method_version)
);
"""


def _columns(connection, table):
    return {row[0] for row in connection.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=?", [table]).fetchall()}


def apply_derived_evaluation_migrations(connection) -> None:
    """Upgrade in-place. DDL and row copy are one transaction, never truncating data."""
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(_EVALUATIONS_SQL)
        columns = _columns(connection, "strategy_scorecards")
        if not columns:
            connection.execute(_SCORECARDS_SQL)
        elif "source_evaluation_id" not in columns:
            # DuckDB cannot alter CHECK constraints. Rebuild this one schema object
            # in-place while preserving every existing scorecard row and identity.
            connection.execute("ALTER TABLE strategy_scorecards RENAME TO strategy_scorecards__pre_derived_v1")
            connection.execute(_SCORECARDS_SQL)
            connection.execute("""INSERT INTO strategy_scorecards
              (scorecard_id,strategy_id,strategy_version,competition_profile_id,competition_profile_version,evaluation_stage,
               sample_start,sample_end,as_of_time,evaluation_method_version,config_sha256,source_experiment_id,source_run_id,
               source_evaluation_id,metrics_json,metrics_sha256,sample_status,created_at)
              SELECT scorecard_id,strategy_id,strategy_version,competition_profile_id,competition_profile_version,evaluation_stage,
               sample_start,sample_end,as_of_time,evaluation_method_version,config_sha256,source_experiment_id,source_run_id,
               NULL,metrics_json,metrics_sha256,sample_status,created_at FROM strategy_scorecards__pre_derived_v1""")
            connection.execute("DROP TABLE strategy_scorecards__pre_derived_v1")
        else:
            # A partial prior migration which added the column but retained the
            # two-source CHECK is unsafe; force an explicit operator review.
            table_sql = connection.execute("SELECT sql FROM duckdb_tables() WHERE table_name='strategy_scorecards'").fetchone()[0]
            if "source_evaluation_id IS NOT NULL" not in table_sql:
                raise RuntimeError("partial derived-evaluation scorecard migration detected")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS strategy_scorecards_evaluation_lineage_idx ON strategy_scorecards(source_evaluation_id,evaluation_method_version)")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
