from os import environ

from quant_core.environment import load_env_file


def test_env_file_loads_missing_values_without_overriding_process_environment(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("# private\nTUSHARE_TOKEN=file-token\nOTHER=value\n", encoding="utf-8")
    monkeypatch.setenv("TUSHARE_TOKEN", "process-token")
    assert load_env_file(path)
    assert environ["TUSHARE_TOKEN"] == "process-token"
    assert environ["OTHER"] == "value"
