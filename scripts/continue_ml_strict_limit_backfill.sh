#!/bin/zsh
# Continue local strict-limit backfill until the configured historical range is complete.
set -euo pipefail

PROJECT_DIR="/Users/lynnwuu/Desktop/Gemini/stock/New_tool"
DB_PATH="$PROJECT_DIR/data/top50/quant.duckdb"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

cd "$PROJECT_DIR"
while true; do
  PENDING="$($PYTHON_BIN -c "import duckdb; c=duckdb.connect('$DB_PATH', read_only=True); total=c.execute(\"select count(distinct as_of_trade_date) from universe_snapshots where group_name='historical_large_cap_momentum' and as_of_trade_date between date '2025-09-30' and date '2026-05-29'\").fetchone()[0]; done=c.execute(\"select count(*) from market_data_snapshots where market_snapshot_id like 'ml_strict_limits_%'\").fetchone()[0]; print(max(total-done, 0))")"
  if [[ "$PENDING" == "0" ]]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') strict-limit backfill complete"
    exit 0
  fi
  echo "$(date '+%Y-%m-%d %H:%M:%S') remaining trading days: $PENDING"
  "$PYTHON_BIN" scripts/backfill_ml_strict_limits.py --db data/top50/quant.duckdb \
    --start-date 2025-09-30 --end-date 2026-05-29 --max-days 1 --sleep-seconds 0
  sleep 8
done
