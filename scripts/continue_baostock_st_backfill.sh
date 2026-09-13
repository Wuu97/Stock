#!/bin/zsh
# Resume bounded BaoStock ST batches until the configured research range is complete.
set -euo pipefail

PROJECT_DIR="/Users/lynnwuu/Desktop/Gemini/stock/New_tool"
cd "$PROJECT_DIR"

while true; do
  RESULT="$(PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/backfill_baostock_st_history.py \
    --db data/top50/quant.duckdb --start-date 2023-09-12 --end-date 2026-09-11 \
    --max-tickers 20 --sleep-seconds 0.25)"
  echo "$RESULT"
  JSON_RESULT="${RESULT##*$'\n'}"
  REMAINING="$(printf '%s' "$JSON_RESULT" | .venv/bin/python -c 'import json, sys; print(json.load(sys.stdin)["remaining_tickers"])')"
  [[ "$REMAINING" == "0" ]] && exit 0
  sleep 5
done
