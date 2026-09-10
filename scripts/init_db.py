"""Initialize the local DuckDB database."""

import argparse
from pathlib import Path

import duckdb

from quant_core.news_migrations import apply_news_migrations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    connection = duckdb.connect(args.db)
    connection.execute((Path(__file__).parents[1] / "sql" / "schema.sql").read_text())
    apply_news_migrations(connection)
    connection.close()


if __name__ == "__main__":
    main()
