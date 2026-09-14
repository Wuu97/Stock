#!/bin/zsh
set -euo pipefail

ROOT="/Users/lynnwuu/Desktop/Gemini/stock/New_tool"
cd "$ROOT"

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
