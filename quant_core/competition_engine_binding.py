"""Immutable evidence missing from the original Competition Profile schema."""
from hashlib import sha256
import json


def canonical_snapshot_set(rows):
    """Order-independent evidence for one inseparable trading-status dataset."""
    canonical = sorted({(str(i), str(source), str(start), str(end), str(raw_hash))
                        for i, source, start, end, raw_hash in rows})
    if not canonical or len(canonical) != len(rows):
        raise ValueError("trading-status snapshot set contains duplicates or is empty")
    return sha256(json.dumps(canonical, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(), canonical


def validate_engine_binding(connection, binding, start, end):
    required = {"feature_lookback_days", "trading_status_source", "trading_status_snapshot_ids", "trading_status_dataset_hash"}
    if not isinstance(binding, dict) or required - set(binding) or int(binding["feature_lookback_days"]) < 2:
        raise ValueError("engine binding is incomplete")
    ids = sorted(binding["trading_status_snapshot_ids"])
    if ids != sorted(set(ids)) or not ids: raise ValueError("trading-status snapshot IDs are invalid")
    placeholders = ','.join('?' for _ in ids)
    rows = connection.execute("SELECT trading_status_snapshot_id,source_channel,start_trade_date,end_trade_date,raw_artifact_sha256 FROM trading_status_snapshots WHERE trading_status_snapshot_id IN (" + placeholders + ") ORDER BY 1", ids).fetchall()
    digest, evidence = canonical_snapshot_set(rows)
    if len(rows) != len(ids) or any(r[1] != binding["trading_status_source"] or r[2] > start or r[3] < end for r in rows) or digest != binding["trading_status_dataset_hash"]:
        raise ValueError("trading-status snapshot set does not match frozen evidence")
    return evidence
