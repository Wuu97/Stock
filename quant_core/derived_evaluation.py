"""Resumable immutable evaluations over completed experiment replay facts only."""
import json
from hashlib import sha256
from uuid import uuid4

from .experiments import calculate_metrics
from .strategy_scorecard import StrategyScorecardStore


def _canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def execution_fingerprint(profile, strategy):
    fields = ("universe_binding", "market_data_binding", "replay_assumptions")
    if any(key not in profile for key in fields): raise ValueError("profile lacks replay binding")
    return sha256(_canonical({**{key: profile[key] for key in fields}, "strategy": strategy}).encode()).hexdigest()


def validate_replay(connection, account_id, start, end, *, experiment_id=None, profile=None, strategy=None):
    """Validate account/experiment/strategy/NAV/calendar/binding lineage; fail closed."""
    spec = None
    if experiment_id:
        row = connection.execute("SELECT account_id,spec_json FROM strategy_experiments WHERE experiment_id=?", [experiment_id]).fetchone()
        if not row or row[0] != account_id: raise ValueError("source experiment/account lineage mismatch")
        spec = json.loads(row[1])
        if str(spec["start_date"]) != str(start) or str(spec["end_date"]) != str(end): raise ValueError("source experiment sample range mismatch")
        if strategy and any(spec["strategy"].get(key) != value for key, value in strategy.items()): raise ValueError("source experiment strategy lineage mismatch")
    dates = [r[0] for r in connection.execute("SELECT trade_date FROM sim_nav_daily WHERE account_id=? ORDER BY trade_date", [account_id]).fetchall()]
    if not dates or len(dates) != len(set(dates)) or str(dates[0]) != str(start) or str(dates[-1]) != str(end): raise ValueError("source replay NAV coverage is incomplete")
    if connection.execute("SELECT 1 FROM sim_order_intents WHERE account_id=? AND order_status='PENDING'", [account_id]).fetchone(): raise ValueError("source replay has pending orders")
    if profile:
        market, universe = profile.get("market_data_binding", {}), profile.get("universe_binding", {})
        if market.get("date_range") and list(map(str, market["date_range"])) != [str(start), str(end)]: raise ValueError("market binding sample range mismatch")
        if universe.get("date_range") and list(map(str, universe["date_range"])) != [str(start), str(end)]: raise ValueError("universe binding sample range mismatch")
        if spec and universe.get("group_id") and universe["group_id"] != spec["universe_reference"]: raise ValueError("source experiment/universe lineage mismatch")
        if market.get("source"):
            actual = [str(r[0]) for r in connection.execute("SELECT DISTINCT trade_date FROM market_data_snapshots WHERE source_channel=? AND trade_date BETWEEN ? AND ? ORDER BY 1", [market["source"], start, end]).fetchall()]
            if actual != [str(day) for day in dates]: raise ValueError("market calendar coverage does not match NAV")
    return True


class DerivedEvaluationStore:
    def __init__(self, connection): self.connection = connection

    def create_or_get(self, *, source_experiment_id, source_profile, evaluation_profile, benchmark_dataset_hash, benchmark_identifier, benchmark_version, evaluation_method_version, created_at):
        source = self.connection.execute("SELECT spec_json FROM strategy_experiments WHERE experiment_id=?", [source_experiment_id]).fetchone()
        if not source: raise ValueError("unknown source experiment")
        spec, strategy = json.loads(source[0]), json.loads(source[0])["strategy"]
        source_hash, evaluation_hash = sha256(_canonical(source_profile).encode()).hexdigest(), sha256(_canonical(evaluation_profile).encode()).hexdigest()
        source_fp, evaluation_fp = execution_fingerprint(source_profile, strategy), execution_fingerprint(evaluation_profile, strategy)
        profile_id, profile_version = evaluation_profile.get("profile_id"), evaluation_profile.get("profile_version")
        if not profile_id or not profile_version: raise ValueError("evaluation profile identity is required")
        existing = self.connection.execute("SELECT evaluation_id,source_profile_hash,evaluation_profile_hash,execution_fingerprint,evaluation_execution_fingerprint,benchmark_identifier,benchmark_version FROM strategy_evaluations WHERE source_experiment_id=? AND evaluation_profile_id=? AND evaluation_profile_version=? AND benchmark_dataset_hash=? AND evaluation_method_version=?", [source_experiment_id, profile_id, profile_version, benchmark_dataset_hash, evaluation_method_version]).fetchone()
        immutable = (source_hash, evaluation_hash, source_fp, evaluation_fp, benchmark_identifier, benchmark_version)
        if existing:
            if tuple(existing[1:]) != immutable: raise ValueError("derived evaluation identity conflicts with immutable lineage")
            return existing[0]
        evaluation_id = str(uuid4())
        self.connection.execute("INSERT INTO strategy_evaluations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [evaluation_id, source_experiment_id, strategy["strategy_id"], strategy["strategy_version"], source_profile.get("profile_id", profile_id), source_profile.get("profile_version", profile_version), source_hash, profile_id, profile_version, evaluation_hash, benchmark_dataset_hash, source_fp, evaluation_fp, source_fp == evaluation_fp, benchmark_identifier, benchmark_version, spec["start_date"], spec["end_date"], evaluation_method_version, "PENDING", created_at])
        return evaluation_id

    def store_result(self, evaluation_id, metrics, created_at):
        payload, digest = _canonical(metrics), sha256(_canonical(metrics).encode()).hexdigest()
        old = self.connection.execute("SELECT metrics_sha256 FROM strategy_evaluation_results WHERE evaluation_id=?", [evaluation_id]).fetchone()
        if old:
            if old[0] != digest: raise ValueError("evaluation result conflicts with immutable result")
            return
        self.connection.execute("INSERT INTO strategy_evaluation_results VALUES (?,?,?,?)", [evaluation_id, payload, digest, created_at])
        self.connection.execute("UPDATE strategy_evaluations SET status='RESULT_COMPLETE' WHERE evaluation_id=? AND status='PENDING'", [evaluation_id])


class DerivedEvaluationRunner:
    def __init__(self, connection): self.connection, self.store = connection, DerivedEvaluationStore(connection)

    def run(self, *, source_experiment_id, source_profile, evaluation_profile, benchmark_bars, benchmark_dataset_hash, benchmark_identifier, benchmark_version, evaluation_method_version, created_at):
        evaluation_id = self.store.create_or_get(source_experiment_id=source_experiment_id, source_profile=source_profile, evaluation_profile=evaluation_profile, benchmark_dataset_hash=benchmark_dataset_hash, benchmark_identifier=benchmark_identifier, benchmark_version=benchmark_version, evaluation_method_version=evaluation_method_version, created_at=created_at)
        status, account_id, start, end, sid, sv = self.connection.execute("SELECT e.status,x.account_id,e.sample_start,e.sample_end,e.strategy_id,e.strategy_version FROM strategy_evaluations e JOIN strategy_experiments x ON x.experiment_id=e.source_experiment_id WHERE e.evaluation_id=?", [evaluation_id]).fetchone()
        if status == "PENDING":
            validate_replay(self.connection, account_id, start, end, experiment_id=source_experiment_id, profile=source_profile, strategy={"strategy_id": sid, "strategy_version": sv})
            if not self.connection.execute("SELECT execution_equivalent FROM strategy_evaluations WHERE evaluation_id=?", [evaluation_id]).fetchone()[0]: raise ValueError("evaluation profile changes replay execution fingerprint")
            self.store.store_result(evaluation_id, calculate_metrics(self.connection, account_id, benchmark_bars), created_at)
        scorecard_id = StrategyScorecardStore(self.connection).store_evaluation_scorecard(evaluation_id, created_at)
        self.connection.execute("UPDATE strategy_evaluations SET status='SCORECARD_COMPLETE' WHERE evaluation_id=?", [evaluation_id])
        return evaluation_id, scorecard_id
