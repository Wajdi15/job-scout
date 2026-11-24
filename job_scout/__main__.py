"""Manage private Job Scout configuration."""

import argparse
import os

from .storage import private_data_dir


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m job_scout")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-data-dir", help="validate private storage location")
    init = commands.add_parser("init-profiles", help="create private search preferences")
    init.add_argument("--candidate-facts", help="absolute path to verified candidate facts YAML")
    validate = commands.add_parser("check-profiles", help="validate private search preferences")
    for command in (check, init, validate):
        command.add_argument("--data-dir", default=os.environ.get("JOB_SCOUT_DATA_DIR"))
    args = parser.parse_args()

    try:
        if args.command == "check-data-dir":
            private_data_dir(args.data_dir)
            print("Private data directory is outside the checkout.")
        else:
            from .preferences import initialize_preferences, load_preferences

            if args.command == "init-profiles":
                initialize_preferences(args.data_dir, args.candidate_facts)
                print("Created private search_preferences.yaml.")
            else:
                profiles = load_preferences(args.data_dir)
                print(f"Validated {len(profiles)} search profiles.")
    except ModuleNotFoundError as error:
        if error.name != "yaml":
            raise
        parser.error("Install dependencies with python -m pip install -r requirements.txt.")
    except (OSError, ValueError) as error:
        parser.error(str(error) if isinstance(error, ValueError) else "Could not access private storage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
