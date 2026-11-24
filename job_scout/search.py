"""Run a manual search and report source coverage."""

from datetime import UTC, datetime
import json

from .jobs import collect_jobs
from .preferences import load_preferences
from .ranking import _atomic_write, rank_jobs
from .research import PROVIDERS, load_research
from .sources import load_sources
from .storage import private_data_file


def _candidate_path(candidate_facts, data_dir):
    if candidate_facts is not None:
        return candidate_facts
    default = private_data_file("candidate_facts.yaml", data_dir)
    if default.is_file():
        return default
    if private_data_file("tailor_engine.yaml", data_dir).is_file():
        from .tailoring import load_engine

        return load_engine(data_dir).facts
    raise ValueError("Supply --candidate-facts or configure a private candidate facts file.")


def search(candidate_facts=None, data_dir=None, *, research_file=None):
    """Run one collection and ranking pass. No model or scheduler starts here."""
    start = datetime.now(UTC)
    facts = _candidate_path(candidate_facts, data_dir)
    load_preferences(data_dir)
    from .matching import load_candidate

    load_candidate(facts)
    research = load_research(research_file, now=start) if research_file is not None else None
    has_watchlist = private_data_file("job_sources.yaml", data_dir).is_file()
    if not has_watchlist and research is None:
        raise ValueError("Create a private job_sources.yaml or supply verified public research.")
    checks = collect_jobs(load_sources(data_dir), data_dir) if has_watchlist else []
    if research:
        checks += collect_jobs(research.sources, data_dir, fetch=research.fetch)

    coverage = [
        {"source": check.source, "status": check.status, "seen": check.seen,
         "added": check.added, "rejected": check.rejected}
        for check in checks
    ]
    skipped = sorted(set(PROVIDERS) - (set(research.coverage) if research else set()))
    reviewed, new_matches = rank_jobs(
        facts, data_dir, new_since=start, coverage=coverage, skipped_research=skipped,
    )
    summary = {
        "started_at": start.isoformat(), "reviewed": reviewed,
        "new_postings": sum(check.added for check in checks), "new_matches": new_matches,
        "target_minimum": 30, "display_limit": 50,
        "coverage": coverage, "skipped_research": skipped,
    }
    _atomic_write(private_data_file("search_run.json", data_dir),
                  json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary
