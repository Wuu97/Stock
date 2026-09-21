from hashlib import sha256
import json

import pytest

from quant_core.p1_lineage import validate_prediction_dataset_manifest


def test_p1_manifest_rejects_dataset_hash_mismatch(tmp_path):
    dataset = tmp_path / "x.parquet"; dataset.write_bytes(b"fixture")
    manifest = tmp_path / "x.parquet.manifest.json"
    manifest.write_text(json.dumps({"schema_version":"x", "label_schema_version":"x", "targets":[], "columns":[], "dataset_sha256":"wrong", "manifest_sha256":"x"}))
    with pytest.raises(ValueError, match="hash"):
        validate_prediction_dataset_manifest(dataset, manifest)


def test_p1_manifest_accepts_matching_hash(tmp_path):
    dataset = tmp_path / "x.parquet"; dataset.write_bytes(b"fixture")
    manifest = tmp_path / "x.parquet.manifest.json"
    manifest.write_text(json.dumps({"schema_version":"x", "label_schema_version":"x", "targets":[], "columns":[], "dataset_sha256":sha256(b"fixture").hexdigest(), "manifest_sha256":"x"}))
    assert validate_prediction_dataset_manifest(dataset, manifest)[1] == sha256(b"fixture").hexdigest()
