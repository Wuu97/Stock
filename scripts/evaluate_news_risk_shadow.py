"""Report review coverage and precision for HIGH shadow risk assessments."""

import argparse
import json

from quant_core.database import writer_connection

from quant_core.news_migrations import apply_news_migrations
from quant_core.news_risk_review import review_metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    connection = writer_connection(args.db, transaction=False)
    try:
        apply_news_migrations(connection)
        print(json.dumps(review_metrics(connection), ensure_ascii=False))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
