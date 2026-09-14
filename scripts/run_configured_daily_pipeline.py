"""Run the single daily pipeline using the versioned local argument profile."""

import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _configured_arguments(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not all(isinstance(value, str) for value in arguments):
        raise ValueError("daily pipeline argument profile requires a string arguments list")
    return arguments


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/daily_pipeline_arguments.json")
    args, forwarded = parser.parse_known_args()
    command = [sys.executable, str(ROOT / "scripts" / "daily_pipeline.py"),
               *_configured_arguments(ROOT / args.config), *forwarded]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
