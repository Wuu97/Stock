from types import SimpleNamespace

import pytest

from scripts.run_first_scorecard_competition import _commands


def test_first_competition_commands_share_every_input_except_strategy_account_and_output():
    args = SimpleNamespace(db="data.duckdb", account_prefix="cmp", output_dir="out",
                           competition_profile_id="large_cap", competition_profile_version="v1")
    commands = _commands(args, ["--start-date", "2020-01-01", "--end-date", "2021-01-01"])
    assert len(commands) == 4
    assert all("--competition-profile-id" in command for command in commands)
    assert {command[command.index("--strategy") + 1] for command in commands} == {
        "baseline", "pure_momentum", "kdj_manual", "macd_manual"
    }


def test_first_competition_refuses_shared_arguments_that_break_comparability():
    args = SimpleNamespace(db="data.duckdb", account_prefix="cmp", output_dir="out",
                           competition_profile_id="large_cap", competition_profile_version="v1")
    with pytest.raises(ValueError, match="must not override"):
        _commands(args, ["--strategy", "baseline"])
