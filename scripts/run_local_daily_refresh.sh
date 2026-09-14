#!/bin/zsh
set -euo pipefail

ROOT="/Users/lynnwuu/Desktop/Gemini/stock/New_tool"
cd "$ROOT"

# launchd starts with a minimal environment, so invoke the project's virtualenv
# and the auditable production entry points explicitly.  They run serially
# because DuckDB permits only one cross-process writer for this database.
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
PIPELINE_STATUS=0
"$ROOT/.venv/bin/python" scripts/daily_pipeline.py \
  --db data/top50/quant.duckdb \
  --account-id top50_forward_account \
  --taxonomy-version SW2021_TUSHARE_20260908 \
  --news-source-config config/news_sources.json \
  --gdelt-query geopolitics \
  --gdelt-query "export control" \
  --gdelt-query "natural disaster" \
  --gdelt-query "monetary policy" \
  "$@" || PIPELINE_STATUS=$?

# Always refresh and value every ACTIVE simulated account, even if a
# non-account strategy stage failed.  This script is idempotent for an
# already-recorded end-of-day snapshot.
"$ROOT/.venv/bin/python" scripts/run_local_daily_refresh.py \
  --db data/top50/quant.duckdb \
  "$@"

# Streamlit reads this published copy, never the writer-owned primary DB.
# The copy is made only after both writers have exited, so a running dashboard
# cannot block the next scheduled refresh.
/bin/cp "$ROOT/data/top50/quant.duckdb" "$ROOT/data/top50/quant_dashboard.duckdb"

exit "$PIPELINE_STATUS"
