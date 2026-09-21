"""Fail-closed allowance for repository-trusted, audited profile bridges."""
from hashlib import sha256
import json


BRIDGE_SCHEMA_VERSION = "profile_bridge_v1"
# No bridge has repository-backed approval evidence in 717dd70.  Add a reviewed
# declaration hash here only with the corresponding immutable bridge artifact.
TRUSTED_BRIDGE_SHA256 = frozenset()


def validate_profile_bridge(bridge, parent_profile_hash, execution_profile_hash, prediction_artifact_sha256, schedule_sha256):
    if parent_profile_hash == execution_profile_hash:
        return
    required = {"schema_version": BRIDGE_SCHEMA_VERSION, "approval_status": "AUDITED_APPROVED", "parent_profile_hash": parent_profile_hash, "execution_profile_hash": execution_profile_hash,
                "prediction_artifact_sha256": prediction_artifact_sha256, "schedule_sha256": schedule_sha256}
    if not bridge or any(bridge.get(key) != value for key, value in required.items()):
        raise ValueError("profile bridge does not bind this exact source/execution artifact set")
    signature = bridge.get("bridge_sha256")
    unsigned = dict(bridge); unsigned.pop("bridge_sha256", None)
    if not signature or sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != signature:
        raise ValueError("profile bridge is not a trusted immutable declaration")
    if signature not in TRUSTED_BRIDGE_SHA256:
        raise ValueError("profile bridge is unknown or not approved")
