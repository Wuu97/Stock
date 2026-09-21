"""Immutable input validation for P1 research artifacts."""
import json
from hashlib import sha256
from pathlib import Path
import duckdb

from .snapshots import canonical_hash


def validate_prediction_dataset_manifest(dataset_path, manifest_path):
    dataset, manifest_file = Path(dataset_path), Path(manifest_path)
    if not dataset.is_file() or not manifest_file.is_file():
        raise ValueError("P1 dataset and manifest must both exist")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    digest = sha256(dataset.read_bytes()).hexdigest()
    required = {"schema_version", "label_schema_version", "targets", "columns", "dataset_sha256", "manifest_sha256"}
    if not required.issubset(manifest):
        raise ValueError("P1 dataset manifest is incomplete")
    if manifest["dataset_sha256"] != digest:
        raise ValueError("P1 dataset hash does not match manifest")
    claimed = manifest["manifest_sha256"]
    unsigned = dict(manifest); unsigned.pop("manifest_sha256")
    if canonical_hash(unsigned) != claimed:
        raise ValueError("P1 manifest canonical hash mismatch")
    if manifest["schema_version"] != "prediction_dataset_v1" or manifest["label_schema_version"] != "prediction_label_v1":
        raise ValueError("P1 dataset manifest schema version is unsupported")
    if not isinstance(manifest["columns"], list) or not manifest["columns"] or not isinstance(manifest["targets"], list) or not manifest["targets"]:
        raise ValueError("P1 dataset manifest columns or targets are invalid")
    if any(not isinstance(target, dict) or not isinstance(target.get("horizon_days"), int) or target["horizon_days"] < 1 for target in manifest["targets"]):
        raise ValueError("P1 dataset manifest targets are invalid")
    connection = duckdb.connect(":memory:")
    try:
        cursor = connection.execute("SELECT * FROM read_parquet(?) LIMIT 0", [str(dataset)])
        actual_columns = [column[0] for column in cursor.description]
    finally:
        connection.close()
    if actual_columns != manifest["columns"]:
        raise ValueError("P1 dataset parquet schema does not match manifest columns")
    return manifest, digest
