"""Immutable input validation for P1 research artifacts."""
import json
from hashlib import sha256
from pathlib import Path


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
    return manifest, digest
