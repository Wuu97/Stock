"""Initialize the local DuckDB database."""

import argparse
from pathlib import Path

import duckdb


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    connection = duckdb.connect(args.db)
    connection.execute((Path(__file__).parents[1] / "sql" / "schema.sql").read_text())
    connection.close()


if __name__ == "__main__":
    main()
