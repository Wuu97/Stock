from hashlib import sha256
import json

import pytest

from quant_core.p1_lineage import validate_prediction_dataset_manifest
from quant_core.snapshots import canonical_hash
import duckdb


def test_p1_manifest_rejects_dataset_hash_mismatch(tmp_path):
    dataset = tmp_path / "x.parquet"; dataset.write_bytes(b"fixture")
    manifest = tmp_path / "x.parquet.manifest.json"
    manifest.write_text(json.dumps({"schema_version":"x", "label_schema_version":"x", "targets":[], "columns":[], "dataset_sha256":"wrong", "manifest_sha256":"x"}))
    with pytest.raises(ValueError, match="hash"):
        validate_prediction_dataset_manifest(dataset, manifest)


def test_p1_manifest_accepts_matching_hash(tmp_path):
    dataset = tmp_path / "x.parquet"
    connection = duckdb.connect(":memory:"); connection.execute("CREATE TABLE x (trade_date DATE, ticker VARCHAR)"); connection.execute("COPY x TO ? (FORMAT PARQUET)", [str(dataset)]); connection.close()
    manifest = tmp_path / "x.parquet.manifest.json"
    value={"schema_version":"prediction_dataset_v1", "label_schema_version":"prediction_label_v1", "targets":[{"horizon_days":5}], "columns":["trade_date","ticker"], "dataset_sha256":sha256(dataset.read_bytes()).hexdigest()}; value["manifest_sha256"]=canonical_hash(value)
    manifest.write_text(json.dumps(value))
    assert validate_prediction_dataset_manifest(dataset, manifest)[1] == sha256(dataset.read_bytes()).hexdigest()


def test_p1_manifest_rejects_tamper_missing_fields_and_schema_mismatch(tmp_path):
    dataset = tmp_path / "x.parquet"; connection=duckdb.connect(":memory:"); connection.execute("CREATE TABLE x (trade_date DATE, ticker VARCHAR)"); connection.execute("COPY x TO ? (FORMAT PARQUET)",[str(dataset)]); connection.close()
    base={"schema_version":"prediction_dataset_v1", "label_schema_version":"prediction_label_v1", "targets":[{"horizon_days":5}], "columns":["trade_date","ticker"], "dataset_sha256":sha256(dataset.read_bytes()).hexdigest()}; base["manifest_sha256"]=canonical_hash(base)
    manifest=tmp_path/"x.manifest.json"
    for broken in (dict(base, schema_version="wrong"), {key:value for key,value in base.items() if key != "targets"}, dict(base, columns=["ticker"])):
        manifest.write_text(json.dumps(broken))
        with pytest.raises(ValueError): validate_prediction_dataset_manifest(dataset, manifest)
