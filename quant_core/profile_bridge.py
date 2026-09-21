"""Fail-closed allowance for explicitly audited profile lineage bridges."""


def validate_profile_bridge(bridge, parent_profile_hash, execution_profile_hash, prediction_artifact_sha256, schedule_sha256):
    if parent_profile_hash == execution_profile_hash:
        return
    required = {"parent_profile_hash": parent_profile_hash, "execution_profile_hash": execution_profile_hash,
                "prediction_artifact_sha256": prediction_artifact_sha256, "schedule_sha256": schedule_sha256}
    if not bridge or any(bridge.get(key) != value for key, value in required.items()):
        raise ValueError("profile bridge does not bind this exact source/execution artifact set")
