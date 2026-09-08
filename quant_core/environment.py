"""Minimal local environment-file loader for optional data-provider credentials."""

from os import environ
from pathlib import Path


def load_env_file(path: Path) -> bool:
    """Load simple KEY=VALUE entries without replacing values already in the process environment."""
    if not path.is_file():
        return False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.isidentifier():
            raise ValueError(f"invalid environment entry in {path}: {raw_line}")
        environ.setdefault(key, value.strip().strip("\"'") )
    return True
