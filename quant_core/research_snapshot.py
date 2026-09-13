"""Create immutable research databases from a quiescent production DuckDB file."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import mkdtemp
from uuid import uuid4

import duckdb

from .database import writer_connection


def create_research_snapshot(production_db: Path, research_db: Path, artifact_root: Path) -> dict:
    """Export/import through DuckDB so a research copy is never a raw file copy.

    Opening the source in write mode is intentional: it fails while another writer
    owns the production file, and therefore establishes a quiescent export window.
    """
    if not production_db.is_file():
        raise ValueError("production database is missing")
    if research_db.exists():
        raise ValueError("research database already exists; create a new immutable snapshot")
    research_db.parent.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    snapshot_id = f"research_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
    export_dir = Path(mkdtemp(prefix=snapshot_id + "_", dir=artifact_root))
    try:
        try:
            source = writer_connection(str(production_db), transaction=False)
        except duckdb.IOException as error:
            raise RuntimeError("production database is busy; wait for its writer to finish before snapshotting") from error
        try:
            source.execute(f"EXPORT DATABASE {_quoted(export_dir)}")
            source_hash = _sha256(production_db)
        finally:
            source.close()
        target = writer_connection(str(research_db), transaction=False)
        try:
            target.execute(f"IMPORT DATABASE {_quoted(export_dir)}")
            target.execute(
                "CREATE TABLE research_snapshot_metadata (snapshot_id VARCHAR PRIMARY KEY, source_database VARCHAR NOT NULL, "
                "source_sha256 VARCHAR NOT NULL, export_manifest_sha256 VARCHAR NOT NULL, created_at TIMESTAMPTZ NOT NULL)"
            )
            export_manifest = _artifact_manifest(export_dir)
            export_manifest_path = export_dir / "snapshot_manifest.json"
            export_manifest_path.write_text(json.dumps(export_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            export_manifest_hash = _sha256(export_manifest_path)
            target.execute("INSERT INTO research_snapshot_metadata VALUES (?, ?, ?, ?, ?)", [
                snapshot_id, str(production_db.resolve()), source_hash, export_manifest_hash, datetime.now(timezone.utc),
            ])
        finally:
            target.close()
        return {
            "snapshot_id": snapshot_id,
            "research_db": str(research_db),
            "source_database": str(production_db.resolve()),
            "source_sha256": source_hash,
            "export_artifact_dir": str(export_dir),
            "export_manifest": str(export_manifest_path),
            "research_db_sha256": _sha256(research_db),
        }
    except Exception:
        # The target path is new for every snapshot, so leaving a failed partial file
        # would only create a misleading research artifact.
        if research_db.exists():
            research_db.unlink()
        raise


def assert_research_database(connection) -> None:
    """Reject studies pointed at the live production database by mistake."""
    exists = connection.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'research_snapshot_metadata'"
    ).fetchone()
    if exists is None or connection.execute("SELECT 1 FROM research_snapshot_metadata LIMIT 1").fetchone() is None:
        raise ValueError("study database is not an immutable research snapshot")


def _artifact_manifest(directory: Path) -> dict:
    return {str(path.relative_to(directory)): _sha256(path) for path in sorted(directory.rglob("*")) if path.is_file()}


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quoted(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"
