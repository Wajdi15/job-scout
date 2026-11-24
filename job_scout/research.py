"""Import fresh, source-backed public findings from a Codex research session."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from .sources import Batch, Posting, Source, SourceError, canonical_url
from .storage import private_path


PROVIDERS = {
    "workable": ("apply.workable.com", r"/[a-z0-9_-]+/j/([a-z0-9]+)/?(?:apply/?)?"),
    "smartrecruiters": ("jobs.smartrecruiters.com", r"/[a-z0-9_-]+/(\d+)(?:-[^/]+)?/?"),
    "euraxess": ("euraxess.ec.europa.eu", r"/jobs/(\d+)/?"),
    "inria": ("jobs.inria.fr", r"/public/classic/[a-z]+/offres/(\d{4}-\d+)/?"),
    "fraunhofer": ("jobs.fraunhofer.de", r"/job/[^/]+/(\d+)/?"),
    "academictransfer": ("www.academictransfer.com", r"/(?:en|nl)/jobs/(\d+)(?:/[^/]+)?/?"),
    "ashby": ("jobs.ashbyhq.com", r"/[a-z0-9_-]+/([a-f0-9-]{36})/?(?:application/?)?"),
}
RESEARCH_PROVIDER_NAMES = {"euraxess", "inria", "fraunhofer", "academictransfer"}
RESEARCH_MARKER = ":research:"
MAX_BYTES = 4 * 1024 * 1024
MAX_AGE = timedelta(hours=6)
STATUS = {"ok", "partial", "error"}


@dataclass(frozen=True)
class Research:
    checked_at: datetime
    coverage: dict[str, str]
    postings: dict[str, list[Posting]]
    rejected: dict[str, int]

    @property
    def sources(self):
        return [Source(provider, "public-web", provider, "research") for provider in self.coverage]

    def fetch(self, source: Source) -> Batch:
        provider = source.provider
        status = self.coverage[provider]
        if status == "error":
            raise SourceError("Public research could not verify this source.")
        return Batch(self.postings[provider], self.rejected[provider], partial=status == "partial")


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is not None:
            return stamp.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        pass
    raise ValueError("Research must include an ISO timestamp with a timezone.")


def posting_identity(provider, value):
    """Accept individual posting URLs on our supported public sites."""
    if provider not in PROVIDERS or not isinstance(value, str):
        return None
    safe = canonical_url(value)
    if safe is None:
        return None
    parts = urlsplit(safe)
    host, pattern = PROVIDERS[provider]
    if parts.hostname != host:
        return None
    match = re.fullmatch(pattern, parts.path, re.I)
    if match is None:
        return None
    path = parts.path.rstrip("/") or "/"
    if provider == "workable" and path.lower().endswith("/apply"):
        path = path[:-len("/apply")]
    if provider == "ashby" and path.lower().endswith("/application"):
        path = path[:-len("/application")]
    return match.group(1).casefold(), urlunsplit(("https", host, path, "", ""))


def _optional_date(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("Use ISO dates in research findings.")
    if len(value) == 10:
        return date.fromisoformat(value).isoformat()
    return _timestamp(value).isoformat()


def _required_text(value, limit):
    if not isinstance(value, str):
        raise ValueError("Verify the required public text fields.")
    text = " ".join(value.split())
    if not text or len(text) > limit:
        raise ValueError("A public text field is empty or too long.")
    return text


def _job(provider, row, now):
    if row.get("open") is not True:
        raise ValueError("The official listing was not confirmed open.")
    identity = posting_identity(provider, row.get("url"))
    if identity is None:
        raise ValueError("Expected an official individual posting URL.")
    deadline = _optional_date(row.get("deadline"))
    if deadline and (date.fromisoformat(deadline) < now.astimezone().date() if len(deadline) == 10
                     else _timestamp(deadline) < now):
        raise ValueError("The posting has passed its stated deadline.")
    identifier, url = identity
    fields = {
        key: _required_text(row[key], 400) if row.get(key) else ""
        for key in ("location", "workplace", "role_type")
    }
    description = _required_text(row.get("summary"), 6000)
    return Posting(
        identifier, _required_text(row.get("title"), 400), _required_text(row.get("employer"), 400),
        url, description=description, published_at=_optional_date(row.get("published_at")),
        deadline=deadline, **fields,
    )


def load_research(filename: str | Path, *, now=None, max_age=MAX_AGE) -> Research:
    """Validate a private bundle before changing the jobs database."""
    path = private_path(filename)
    if not path.is_file():
        raise ValueError("The private research file does not exist.")
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("The research file exceeds the size limit.")
    try:
        report = json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("The research file must contain valid JSON.") from None
    if not isinstance(report, dict) or type(report.get("version")) is not int or report["version"] != 1:
        raise ValueError("The research file needs version: 1.")
    now = now or datetime.now(UTC)
    stamp = _timestamp(report.get("checked_at"))
    if stamp < now - max_age or stamp > now + timedelta(minutes=5):
        raise ValueError("The research verification is stale or dated in the future.")
    coverage = report.get("coverage")
    if (
        not isinstance(coverage, dict) or not coverage
        or any(key not in PROVIDERS or not isinstance(value, str) or value not in STATUS
               for key, value in coverage.items())
    ):
        raise ValueError("Use a supported source and status for research coverage.")
    rows = report.get("postings")
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError("Research postings must be a list of up to 500 entries.")
    seen = {name: set() for name in coverage}
    postings = {name: [] for name in coverage}
    rejected = {name: 0 for name in coverage}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("source"), str) or row["source"] not in coverage:
            raise ValueError("Each posting needs its source in the coverage list.")
        provider = row["source"]
        if coverage[provider] == "error":
            raise ValueError("An unavailable source cannot supply verified postings.")
        try:
            job = _job(provider, row, now)
        except ValueError:
            rejected[provider] += 1
            continue
        if job.source_id not in seen[provider]:
            seen[provider].add(job.source_id)
            postings[provider].append(job)
    return Research(stamp, coverage, postings, rejected)
