"""Manage private Job Scout configuration."""

import argparse
import os
import sqlite3

from .storage import private_data_dir


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m job_scout")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-data-dir", help="validate private storage location")
    init = commands.add_parser("init-profiles", help="create private search preferences")
    init.add_argument("--candidate-facts", help="absolute path to verified candidate facts YAML")
    validate = commands.add_parser("check-profiles", help="validate private search preferences")
    collect = commands.add_parser("collect-jobs", help="fetch configured public job sources")
    rank = commands.add_parser("rank-jobs", help="rank collected postings against private evidence")
    rank.add_argument("--candidate-facts", help="absolute path to verified candidate facts YAML")
    for command in (check, init, validate, collect, rank):
        command.add_argument("--data-dir", default=os.environ.get("JOB_SCOUT_DATA_DIR"))
    args = parser.parse_args()

    try:
        if args.command == "check-data-dir":
            private_data_dir(args.data_dir)
            print("Private data directory is outside the checkout.")
        elif args.command == "rank-jobs":
            from .ranking import rank_jobs

            reviewed, shortlisted = rank_jobs(args.candidate_facts, args.data_dir)
            print(f"Reviewed {reviewed} jobs and shortlisted {shortlisted}.")
            print("Saved ranked_jobs.html and ranked_jobs.json in private storage.")
        elif args.command == "collect-jobs":
            from .jobs import collect_jobs
            from .sources import load_sources

            checks = collect_jobs(load_sources(args.data_dir), args.data_dir)
            for check in checks:
                detail = f" ({check.detail})" if check.detail else ""
                print(f"{check.source}: {check.status}, {check.seen} saved or refreshed, "
                      f"{check.added} new, {check.rejected} rejected{detail}")
            return 0 if all(check.status == "ok" for check in checks) else 1
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
    except sqlite3.Error:
        parser.error("Could not update the private jobs database.")
    except (OSError, ValueError) as error:
        parser.error(str(error) if isinstance(error, ValueError) else "Could not access private storage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
