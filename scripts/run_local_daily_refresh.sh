#!/bin/zsh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  print -u2 "daily refresh bootstrap failed: virtualenv python is missing at $ROOT/.venv/bin/python"
  exit 127
fi
if [[ ! -f "$ROOT/scripts/run_configured_daily_pipeline.py" || ! -f "$ROOT/scripts/run_local_daily_refresh.py" ]]; then
  print -u2 "daily refresh bootstrap failed: required runner script is missing under $ROOT/scripts"
  exit 127
fi

# launchd starts with a minimal environment.  The argument profile is shared by
# local automation and manual runs, preventing same-day audit-config drift.
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
"$ROOT/.venv/bin/python" scripts/run_configured_daily_pipeline.py "$@"

# Publish NAV only after the same completed pipeline has supplied an immutable,
# limit-enriched Tushare snapshot.  No independent fallback vendor is used.
"$ROOT/.venv/bin/python" scripts/run_local_daily_refresh.py \
  --db data/top50/quant.duckdb \
  "$@"

# Streamlit reads this published copy, never the writer-owned primary DB.
# The copy is made only after both writers have exited, so a running dashboard
# cannot block the next scheduled refresh.
/bin/cp "$ROOT/data/top50/quant.duckdb" "$ROOT/data/top50/quant_dashboard.duckdb"
