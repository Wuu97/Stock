"""Apply the formal Derived Evaluation Lineage V1 in-place migration."""
import argparse

from quant_core.database import writer_connection
from quant_core.derived_evaluation_migrations import apply_derived_evaluation_migrations


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--db", required=True)
    args = parser.parse_args()
    with writer_connection(args.db, transaction=False) as connection:
        apply_derived_evaluation_migrations(connection)


if __name__ == "__main__": main()
