"""Concurrent-safe, resumable audit state for the daily orchestration shell."""

from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Callable
from uuid import uuid4

import duckdb

from .snapshots import canonical_hash


class PipelineStore:
    def __init__(self, db_path: str, artifact_dir: Path):
        self.db_path, self.artifact_dir = db_path, artifact_dir

    def start_or_resume(self, trade_date: date, account_id: str, config: dict, effective_as_of: datetime) -> tuple[str, datetime, bool]:
        config_json, config_hash = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")), canonical_hash(config)
        connection = duckdb.connect(self.db_path)
        try:
            row = connection.execute("SELECT pipeline_run_id, effective_as_of_timestamp, run_status, config_sha256 FROM pipeline_runs WHERE trade_date = ? AND account_id = ?", [trade_date, account_id]).fetchone()
            if row is None:
                try:
                    run_id = str(uuid4())
                    connection.execute("INSERT INTO pipeline_runs (pipeline_run_id, trade_date, account_id, config_sha256, config_json, effective_as_of_timestamp, run_status, started_at) VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?)", [run_id, trade_date, account_id, config_hash, config_json, effective_as_of, datetime.now(timezone.utc)])
                    return run_id, effective_as_of, False
                except duckdb.ConstraintException:
                    row = connection.execute("SELECT pipeline_run_id, effective_as_of_timestamp, run_status, config_sha256 FROM pipeline_runs WHERE trade_date = ? AND account_id = ?", [trade_date, account_id]).fetchone()
            run_id, cutoff, status, stored_hash = row
            if stored_hash != config_hash:
                raise ValueError("daily pipeline configuration differs from the existing run")
            if status not in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}:
                connection.execute("UPDATE pipeline_runs SET run_status = 'RUNNING', completed_at = NULL, error_text = NULL WHERE pipeline_run_id = ?", [run_id])
            return run_id, cutoff, status in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}
        finally:
            connection.close()

    def run_stage(self, run_id: str, name: str, execution_class: str, operation: Callable[[], object]) -> object:
        connection = duckdb.connect(self.db_path)
        try:
            summary = connection.execute("SELECT stage_status, artifact_reference FROM pipeline_run_stages WHERE pipeline_run_id = ? AND stage_name = ?", [run_id, name]).fetchone()
            if summary and summary[0] == "SUCCEEDED":
                return json.loads(Path(summary[1]).read_text(encoding="utf-8"))
            connection.execute("UPDATE pipeline_run_stage_attempts SET stage_status = 'FAILED', completed_at = ?, error_text = COALESCE(error_text, 'INTERRUPTED_BY_RESUME') WHERE pipeline_run_id = ? AND stage_name = ? AND stage_status = 'RUNNING'", [datetime.now(timezone.utc), run_id, name])
            attempt = connection.execute("SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM pipeline_run_stage_attempts WHERE pipeline_run_id = ? AND stage_name = ?", [run_id, name]).fetchone()[0]
            now = datetime.now(timezone.utc)
            connection.execute("INSERT OR REPLACE INTO pipeline_run_stages VALUES (?, ?, ?, 'RUNNING', ?, NULL, NULL, NULL)", [run_id, name, execution_class, now])
            connection.execute("INSERT INTO pipeline_run_stage_attempts VALUES (?, ?, ?, ?, 'RUNNING', ?, NULL, NULL, NULL)", [run_id, name, attempt, execution_class, now])
        finally:
            connection.close()
        try:
            result = operation()
            path = self.artifact_dir / run_id / f"{name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, default=str, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            self._complete_stage(run_id, name, attempt, "SUCCEEDED", str(path), None)
            return result
        except Exception as error:
            self._complete_stage(run_id, name, attempt, "FAILED", None, str(error))
            if execution_class == "BLOCKING":
                self._fail_run(run_id, str(error))
                raise
            return {"status": "NON_BLOCKING_FAILURE", "reason": str(error)}

    def finish(self, run_id: str) -> None:
        connection = duckdb.connect(self.db_path)
        try:
            failures = connection.execute("SELECT COUNT(*) FROM pipeline_run_stages WHERE pipeline_run_id = ? AND stage_status = 'FAILED' AND execution_class = 'NON_BLOCKING'", [run_id]).fetchone()[0]
            connection.execute("UPDATE pipeline_runs SET run_status = ?, completed_at = ?, error_text = NULL WHERE pipeline_run_id = ?", ["COMPLETED_WITH_WARNINGS" if failures else "COMPLETED", datetime.now(timezone.utc), run_id])
        finally:
            connection.close()

    def _complete_stage(self, run_id: str, name: str, attempt: int, status: str, artifact: str, error: str) -> None:
        connection = duckdb.connect(self.db_path)
        try:
            now = datetime.now(timezone.utc)
            connection.execute("UPDATE pipeline_run_stage_attempts SET stage_status = ?, completed_at = ?, artifact_reference = ?, error_text = ? WHERE pipeline_run_id = ? AND stage_name = ? AND attempt_number = ?", [status, now, artifact, error, run_id, name, attempt])
            connection.execute("UPDATE pipeline_run_stages SET stage_status = ?, completed_at = ?, artifact_reference = ?, error_text = ? WHERE pipeline_run_id = ? AND stage_name = ?", [status, now, artifact, error, run_id, name])
        finally:
            connection.close()

    def _fail_run(self, run_id: str, error: str) -> None:
        connection = duckdb.connect(self.db_path)
        try:
            connection.execute("UPDATE pipeline_runs SET run_status = 'FAILED', completed_at = ?, error_text = ? WHERE pipeline_run_id = ?", [datetime.now(timezone.utc), error, run_id])
        finally:
            connection.close()
