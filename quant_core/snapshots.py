"""Snapshot metadata and causal-time validation for offline daily workflows."""

from datetime import date, datetime, time
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
CALL_AUCTION_START = time(9, 15)


def canonical_hash(payload: Mapping) -> str:
    """Hash canonical JSON so equivalent manifests always produce the same digest."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def write_manifest(path: Path, artifacts: Mapping[str, str], previous_hash: str = "") -> str:
    """Write a deterministic manifest that records immutable artifact hashes."""
    payload = {"artifacts": dict(sorted(artifacts.items())), "previous_hash": previous_hash}
    payload["manifest_sha256"] = canonical_hash(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return payload["manifest_sha256"]


def aggregate_manifest_hash(manifest_hashes: Iterable[str]) -> str:
    ordered = list(manifest_hashes)
    if not ordered:
        raise ValueError("feature snapshots require at least one market manifest")
    return canonical_hash({"input_manifests": ordered})


def validate_causal_window(max_source_published_at: datetime, effective_as_of: datetime,
                           target_trade_date: date) -> Optional[str]:
    """Return the invalid reason, if any, without relying on the machine clock."""
    if max_source_published_at.tzinfo is None or effective_as_of.tzinfo is None:
        raise ValueError("causal timestamps must include a timezone")
    if effective_as_of <= max_source_published_at:
        return "DATA_NOT_READY"
    deadline = datetime.combine(target_trade_date, CALL_AUCTION_START, tzinfo=SHANGHAI)
    if effective_as_of.astimezone(SHANGHAI) >= deadline:
        return "LOOKAHEAD_VIOLATION"
    return None


class SnapshotService:
    """Persistence shell for immutable snapshot metadata and recommendation freeze state."""

    def __init__(self, connection):
        self.connection = connection

    def register_market_snapshot(self, snapshot_id: str, trade_date: date, source_channel: str,
                                 source_published_at: datetime, received_at: datetime,
                                 manifest_path: str, manifest_sha256: str, created_at: datetime) -> None:
        self.connection.execute(
            "INSERT INTO market_data_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [snapshot_id, trade_date, source_channel, source_published_at, received_at,
             manifest_path, manifest_sha256, created_at],
        )

    def register_feature_snapshot(self, snapshot_id: str, as_of_trade_date: date, lookback_days: int,
                                  artifact_path: str, artifact_sha256: str,
                                  input_snapshot_ids: Sequence[str], created_at: datetime) -> None:
        if not input_snapshot_ids:
            raise ValueError("feature snapshots require at least one input")
        rows = self.connection.execute(
            "SELECT market_snapshot_id, manifest_sha256, source_published_at FROM market_data_snapshots WHERE market_snapshot_id IN (" + ",".join("?" for _ in input_snapshot_ids) + ")",
            list(input_snapshot_ids),
        ).fetchall()
        if len(rows) != len(input_snapshot_ids):
            raise ValueError("feature input snapshot is missing")
        by_id = {row[0]: row for row in rows}
        ordered = [by_id[snapshot_id] for snapshot_id in input_snapshot_ids]
        input_hash = aggregate_manifest_hash(row[1] for row in ordered)
        latest_publication = max(row[2] for row in ordered)
        self.connection.execute(
            "INSERT INTO feature_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [snapshot_id, as_of_trade_date, lookback_days, input_hash, artifact_path,
             artifact_sha256, latest_publication, created_at],
        )
        self.connection.executemany(
            "INSERT INTO feature_snapshot_inputs VALUES (?, ?)",
            [(snapshot_id, source_id) for source_id in input_snapshot_ids],
        )

    def freeze_run(self, run_id: str, target_trade_date: date, strategy_id: str, strategy_version: str,
                   cost_model_version: str, feature_snapshot_id: str, effective_as_of: datetime,
                   created_at: datetime) -> str:
        row = self.connection.execute(
            "SELECT max_source_published_at FROM feature_snapshots WHERE feature_snapshot_id = ?",
            [feature_snapshot_id],
        ).fetchone()
        if row is None:
            raise ValueError("feature snapshot is missing")
        reason = validate_causal_window(row[0], effective_as_of, target_trade_date)
        status = "INVALID" if reason else "FROZEN"
        self.connection.execute(
            "INSERT INTO recommendation_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [run_id, target_trade_date, strategy_id, strategy_version, cost_model_version,
             feature_snapshot_id, effective_as_of, status, reason, created_at],
        )
        return status
