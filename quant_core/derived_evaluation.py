"""Resumable immutable evaluations over completed experiment replay facts only."""
import json
from hashlib import sha256
from uuid import uuid4

from .experiments import calculate_metrics
from .strategy_scorecard import StrategyScorecardStore


def _canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
def _hash(value): return sha256(_canonical(value).encode()).hexdigest()


def execution_fingerprint(profile, strategy):
    fields = ("universe_binding", "market_data_binding", "replay_assumptions")
    if any(key not in profile for key in fields): raise ValueError("profile lacks replay binding")
    return _hash({**{key: profile[key] for key in fields}, "strategy": strategy})


def load_frozen_profile(connection, profile_id, profile_version):
    """Load the DB authority and reject a profile whose stored content was altered."""
    row = connection.execute("SELECT profile_json,profile_sha256 FROM competition_profiles WHERE competition_profile_id=? AND competition_profile_version=?", [profile_id, profile_version]).fetchone()
    if not row: raise ValueError("frozen competition profile does not exist")
    try: payload = json.loads(row[0])
    except json.JSONDecodeError as error: raise ValueError("frozen competition profile JSON is invalid") from error
    if _hash(payload) != row[1]: raise ValueError("frozen competition profile hash mismatch")
    return payload, row[1]


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
            actual = [str(r[0]) for r in connection.execute("SELECT trade_date FROM market_data_snapshots WHERE source_channel=? AND trade_date BETWEEN ? AND ? ORDER BY 1", [market["source"], start, end]).fetchall()]
            if len(actual) != len(set(actual)) or actual != [str(day) for day in dates]: raise ValueError("market calendar coverage does not match NAV")
    return dates


def validate_benchmark_dates(benchmark_bars, nav_dates):
    bars = list(benchmark_bars); dates = [bar.trade_date for bar in bars]
    if len(dates) != len(set(dates)) or set(dates) != set(nav_dates) or len(dates) != len(nav_dates):
        raise ValueError("benchmark dates do not exactly match source NAV dates")
    return bars


class DerivedEvaluationStore:
    def __init__(self, connection): self.connection = connection

    def create_or_get(self, *, source_experiment_id, source_profile_id, source_profile_version, source_profile, source_hash, evaluation_profile_id, evaluation_profile_version, evaluation_profile, evaluation_hash, benchmark_dataset_hash, benchmark_identifier, benchmark_version, evaluation_method_version, created_at):
        source = self.connection.execute("SELECT spec_json FROM strategy_experiments WHERE experiment_id=?", [source_experiment_id]).fetchone()
        if not source: raise ValueError("unknown source experiment")
        spec = json.loads(source[0]); strategy = spec["strategy"]
        source_fp, evaluation_fp = execution_fingerprint(source_profile, strategy), execution_fingerprint(evaluation_profile, strategy)
        existing = self.connection.execute("SELECT evaluation_id,source_profile_hash,evaluation_profile_hash,execution_fingerprint,evaluation_execution_fingerprint,benchmark_identifier,benchmark_version FROM strategy_evaluations WHERE source_experiment_id=? AND evaluation_profile_id=? AND evaluation_profile_version=? AND benchmark_dataset_hash=? AND evaluation_method_version=?", [source_experiment_id, evaluation_profile_id, evaluation_profile_version, benchmark_dataset_hash, evaluation_method_version]).fetchone()
        immutable = (source_hash, evaluation_hash, source_fp, evaluation_fp, benchmark_identifier, benchmark_version)
        if existing:
            if tuple(existing[1:]) != immutable: raise ValueError("derived evaluation identity conflicts with immutable lineage")
            return existing[0]
        eid = str(uuid4())
        self.connection.execute("INSERT INTO strategy_evaluations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [eid, source_experiment_id, strategy["strategy_id"], strategy["strategy_version"], source_profile_id, source_profile_version, source_hash, evaluation_profile_id, evaluation_profile_version, evaluation_hash, benchmark_dataset_hash, source_fp, evaluation_fp, source_fp == evaluation_fp, benchmark_identifier, benchmark_version, spec["start_date"], spec["end_date"], evaluation_method_version, "PENDING", created_at])
        return eid

    def store_result(self, evaluation_id, metrics, created_at):
        payload, digest = _canonical(metrics), _hash(metrics)
        old = self.connection.execute("SELECT metrics_sha256 FROM strategy_evaluation_results WHERE evaluation_id=?", [evaluation_id]).fetchone()
        if old:
            if old[0] != digest: raise ValueError("evaluation result conflicts with immutable result")
            return
        self.connection.execute("INSERT INTO strategy_evaluation_results VALUES (?,?,?,?)", [evaluation_id, payload, digest, created_at])
        self.connection.execute("UPDATE strategy_evaluations SET status='RESULT_COMPLETE' WHERE evaluation_id=? AND status='PENDING'", [evaluation_id])


class DerivedEvaluationRunner:
    def __init__(self, connection): self.connection, self.store = connection, DerivedEvaluationStore(connection)

    def run(self, *, source_experiment_id, source_profile_id, source_profile_version, evaluation_profile_id, evaluation_profile_version, benchmark_bars, benchmark_dataset_hash, benchmark_identifier, benchmark_version, evaluation_method_version, created_at):
        source_profile, source_hash = load_frozen_profile(self.connection, source_profile_id, source_profile_version)
        evaluation_profile, evaluation_hash = load_frozen_profile(self.connection, evaluation_profile_id, evaluation_profile_version)
        binding = evaluation_profile.get("benchmark_binding", {})
        if binding and (binding.get("dataset_hash") != benchmark_dataset_hash or binding.get("identifier") != benchmark_identifier or binding.get("version") != benchmark_version): raise ValueError("benchmark does not match frozen evaluation profile")
        source = self.connection.execute("SELECT account_id,spec_json FROM strategy_experiments WHERE experiment_id=?", [source_experiment_id]).fetchone()
        if not source: raise ValueError("unknown source experiment")
        spec = json.loads(source[1]); nav_dates = validate_replay(self.connection, source[0], spec["start_date"], spec["end_date"], experiment_id=source_experiment_id, profile=source_profile, strategy=spec["strategy"])
        bars = validate_benchmark_dates(benchmark_bars, nav_dates)
        eid = self.store.create_or_get(source_experiment_id=source_experiment_id, source_profile_id=source_profile_id, source_profile_version=source_profile_version, source_profile=source_profile, source_hash=source_hash, evaluation_profile_id=evaluation_profile_id, evaluation_profile_version=evaluation_profile_version, evaluation_profile=evaluation_profile, evaluation_hash=evaluation_hash, benchmark_dataset_hash=benchmark_dataset_hash, benchmark_identifier=benchmark_identifier, benchmark_version=benchmark_version, evaluation_method_version=evaluation_method_version, created_at=created_at)
        status, account_id = self.connection.execute("SELECT e.status,x.account_id FROM strategy_evaluations e JOIN strategy_experiments x ON x.experiment_id=e.source_experiment_id WHERE e.evaluation_id=?", [eid]).fetchone()
        if status == "PENDING":
            if not self.connection.execute("SELECT execution_equivalent FROM strategy_evaluations WHERE evaluation_id=?", [eid]).fetchone()[0]: raise ValueError("evaluation profile changes replay execution fingerprint")
            self.store.store_result(eid, calculate_metrics(self.connection, account_id, bars), created_at)
        scorecard_id = StrategyScorecardStore(self.connection).store_evaluation_scorecard(eid, created_at)
        self.connection.execute("UPDATE strategy_evaluations SET status='SCORECARD_COMPLETE' WHERE evaluation_id=?", [eid])
        return eid, scorecard_id
