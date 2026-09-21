import pytest

from quant_core.profile_bridge import validate_profile_bridge


def test_profile_mismatch_fails_without_exact_hash_bound_bridge():
    with pytest.raises(ValueError, match="profile bridge"):
        validate_profile_bridge(None, "parent", "execution", "prediction", "schedule")
    with pytest.raises(ValueError, match="profile bridge"):
        validate_profile_bridge({"parent_profile_hash":"parent", "execution_profile_hash":"execution", "prediction_artifact_sha256":"wrong", "schedule_sha256":"schedule"}, "parent", "execution", "prediction", "schedule")


def test_unknown_self_authored_bridge_fails_closed():
    with pytest.raises(ValueError):
        validate_profile_bridge({"parent_profile_hash":"parent", "execution_profile_hash":"execution", "prediction_artifact_sha256":"prediction", "schedule_sha256":"schedule"}, "parent", "execution", "prediction", "schedule")


def test_identical_profiles_do_not_need_a_bridge():
    validate_profile_bridge(None, "same", "same", "prediction", "schedule")
