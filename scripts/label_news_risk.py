"""Append one human review verdict to a shadow disclosure-risk assessment."""

import argparse
from datetime import datetime, timezone

from quant_core.database import writer_connection

from quant_core.news_migrations import apply_news_migrations
from quant_core.news_risk_review import REVIEW_LABELS, record_review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--assessment-id", required=True)
    parser.add_argument("--label", required=True, choices=sorted(REVIEW_LABELS))
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--rationale", required=True)
    args = parser.parse_args()
    connection = writer_connection(args.db, transaction=False)
    try:
        apply_news_migrations(connection)
        review_id = record_review(connection, args.assessment_id, args.label, args.reviewer,
                                  args.rationale, datetime.now(timezone.utc))
    finally:
        connection.close()
    print(review_id)


if __name__ == "__main__":
    main()
