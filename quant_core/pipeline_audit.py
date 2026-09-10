"""Resumable, append-safe audit state for the daily orchestration shell."""

from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

import duckdb

from .snapshots import canonical_hash


class PipelineStore:
    def __init__(self, db_path: str, artifact_dir: Path):
        self.db_path, self.artifact_dir = db_path, artifact_dir

    def _connection(self):
        return duckdb.connect(self.db_path)

    def start_or_resume(self, trade_date: date, account_id: str, config: dict, effective_as_of: datetime) -> tuple[str, datetime]:
        connection = self._connection()
        try:
            row = connection.execute(
            "SELECT pipeline_run_id, effective_as_of_timestamp, run_status FROM pipeline_runs WHERE trade_date = ? AND account_id = ?",
            [trade_date, account_id],
            ).fetchone()
            if row:
                run_id, cutoff, status = row
                if status in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}:
                    return run_id, cutoff
                connection.execute("UPDATE pipeline_runs SET run_status = 'RUNNING', completed_at = NULL, error_text = NULL WHERE pipeline_run_id = ?", [run_id])
                return run_id, cutoff
            now, run_id = datetime.now(timezone.utc), str(uuid4())
            connection.execute(
            "INSERT INTO pipeline_runs VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, NULL, NULL)",
            [run_id, trade_date, account_id, canonical_hash(config), effective_as_of, now],
            )
            return run_id, effective_as_of
        finally:
            connection.close()

    def run_stage(self, run_id: str, name: str, execution_class: str, operation: Callable[[], object]) -> object:
        connection = self._connection()
        try:
            row = connection.execute(
            "SELECT stage_status, artifact_reference FROM pipeline_run_stages WHERE pipeline_run_id = ? AND stage_name = ?", [run_id, name]
            ).fetchone()
            if row and row[0] == "SUCCEEDED":
                return json.loads(Path(row[1]).read_text(encoding="utf-8"))
            now = datetime.now(timezone.utc)
            connection.execute("DELETE FROM pipeline_run_stages WHERE pipeline_run_id = ? AND stage_name = ?", [run_id, name])
            connection.execute("INSERT INTO pipeline_run_stages VALUES (?, ?, ?, 'RUNNING', ?, NULL, NULL, NULL)",
                                [run_id, name, execution_class, now])
        finally:
            connection.close()
        try:
            result = operation()
            path = self.artifact_dir / run_id / f"{name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, default=str, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            connection = self._connection()
            connection.execute("UPDATE pipeline_run_stages SET stage_status = 'SUCCEEDED', completed_at = ?, artifact_reference = ? WHERE pipeline_run_id = ? AND stage_name = ?",
                                    [datetime.now(timezone.utc), str(path), run_id, name])
            connection.close()
            return result
        except Exception as error:
            connection = self._connection()
            connection.execute("UPDATE pipeline_run_stages SET stage_status = 'FAILED', completed_at = ?, error_text = ? WHERE pipeline_run_id = ? AND stage_name = ?",
                                    [datetime.now(timezone.utc), str(error), run_id, name])
            if execution_class == "BLOCKING":
                connection.execute("UPDATE pipeline_runs SET run_status = 'FAILED', completed_at = ?, error_text = ? WHERE pipeline_run_id = ?",
                                        [datetime.now(timezone.utc), str(error), run_id])
                connection.close()
                raise
            connection.close()
            return {"status": "NON_BLOCKING_FAILURE", "reason": str(error)}

    def finish(self, run_id: str) -> None:
        connection = self._connection()
        failures = connection.execute("SELECT COUNT(*) FROM pipeline_run_stages WHERE pipeline_run_id = ? AND stage_status = 'FAILED' AND execution_class = 'NON_BLOCKING'", [run_id]).fetchone()[0]
        status = "COMPLETED_WITH_WARNINGS" if failures else "COMPLETED"
        connection.execute("UPDATE pipeline_runs SET run_status = ?, completed_at = ?, error_text = NULL WHERE pipeline_run_id = ?",
                                [status, datetime.now(timezone.utc), run_id])
        connection.close()
