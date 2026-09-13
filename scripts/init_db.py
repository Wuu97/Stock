"""Initialize the local DuckDB database."""

import argparse
from pathlib import Path

from quant_core.database import writer_connection
from quant_core.news_migrations import apply_news_migrations
from quant_core.pipeline_migrations import apply_pipeline_migrations
from quant_core.security_migrations import apply_security_migrations
from quant_core.security_eligibility_migrations import apply_security_eligibility_migrations
from quant_core.st_history_migrations import apply_st_history_migrations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    with writer_connection(args.db) as connection:
        connection.execute((Path(__file__).parents[1] / "sql" / "schema.sql").read_text())
        apply_news_migrations(connection)
        apply_pipeline_migrations(connection)
        apply_security_migrations(connection)
        apply_security_eligibility_migrations(connection)
        apply_st_history_migrations(connection)


if __name__ == "__main__":
    main()
