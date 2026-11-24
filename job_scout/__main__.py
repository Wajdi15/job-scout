"""Check the private data location for a Job Scout checkout."""

import argparse
import os

from .storage import private_data_dir


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m job_scout")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-data-dir", help="validate private storage location")
    check.add_argument("--data-dir", default=os.environ.get("JOB_SCOUT_DATA_DIR"))
    args = parser.parse_args()

    try:
        private_data_dir(args.data_dir)
    except ValueError as error:
        parser.error(str(error))

    print("Private data directory is outside the checkout.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
