from datetime import date, datetime, timezone
from hashlib import sha256
import importlib.util
from pathlib import Path

import duckdb
import pytest

from quant_core.st_history_migrations import apply_st_history_migrations


_SPEC = importlib.util.spec_from_file_location(
    "backfill_baostock_st_history", Path("scripts/backfill_baostock_st_history.py")
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _setup(path):
    connection = duckdb.connect(str(path))
    apply_st_history_migrations(connection)
    now = datetime.now(timezone.utc)
    connection.execute("INSERT INTO st_history_backfill_runs VALUES ('run', 'baostock', ?, ?, 'baostock_is_st_supplier_fact_v1', ?)",
                       [date(2020, 1, 1), date(2020, 1, 2), now])
    connection.execute("INSERT INTO st_history_backfill_state VALUES ('run', 'AAA.SZ', 'RUNNING', 1, NULL, NULL, NULL, NULL, ?)", [now])
    connection.close()


def test_st_backfill_is_write_once_and_preserves_provider_conflicts(tmp_path):
    db, artifact = tmp_path / "evidence.duckdb", tmp_path / "AAA_SZ.raw.json"
    _setup(db)
    original = b'{"rows":[["2020-01-02",false]],"ticker":"AAA.SZ"}'
    original_hash = sha256(original).hexdigest()
    _MODULE._store_raw_artifact(artifact, original, original_hash)
    assert _MODULE._finish_attempt(str(db), "run", "AAA.SZ", [(date(2020, 1, 2), False)], artifact, original_hash)
    # Same content is an idempotent resume and does not recreate facts.
    assert not _MODULE._finish_attempt(str(db), "run", "AAA.SZ", [(date(2020, 1, 2), False)], artifact, original_hash)
    revised = b'{"rows":[["2020-01-02",true]],"ticker":"AAA.SZ"}'
    with pytest.raises(RuntimeError, match="EVIDENCE_CONFLICT"):
        _MODULE._store_raw_artifact(artifact, revised, sha256(revised).hexdigest())
    assert artifact.read_bytes() == original
    assert list((tmp_path / "conflicts").iterdir())
    connection = duckdb.connect(str(db), read_only=True)
    assert connection.execute("SELECT is_st FROM st_history_daily WHERE backfill_run_id='run'").fetchall() == [(False,)]
    connection.close()
