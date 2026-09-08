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
    columns = {row[1] for row in connection.execute("PRAGMA table_info('news_documents')").fetchall()}
    for name in ("raw_artifact_path", "raw_artifact_sha256"):
        if name not in columns:
            connection.execute(f"ALTER TABLE news_documents ADD COLUMN {name} VARCHAR")
    connection.close()


if __name__ == "__main__":
    main()
