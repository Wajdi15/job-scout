"""Private SQLite storage and source check history."""

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import os
import sqlite3

from .research import RESEARCH_MARKER
from .sources import Batch, Posting, Source, SourceError, fetch_source
from .storage import private_data_file


DATABASE_NAME = "jobs.sqlite"
SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    employer TEXT NOT NULL,
    description TEXT NOT NULL,
    location TEXT NOT NULL,
    workplace TEXT NOT NULL,
    role_type TEXT NOT NULL,
    published_at TEXT,
    deadline TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posting_sources (
    source TEXT NOT NULL,
    posting_id TEXT NOT NULL,
    job_id INTEGER NOT NULL REFERENCES postings(id),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (source, posting_id)
);
CREATE INDEX IF NOT EXISTS posting_sources_job_id ON posting_sources(job_id);
CREATE TABLE IF NOT EXISTS source_checks (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL,
    seen INTEGER NOT NULL,
    added INTEGER NOT NULL,
    rejected INTEGER NOT NULL,
    detail TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Check:
    source: str
    status: str
    seen: int
    added: int
    rejected: int = 0
    detail: str = ""


def _one(connection, query, parameters):
    return connection.execute(query, parameters).fetchone()


def _upsert(connection, source: str, item: Posting, seen: str) -> int:
    identity = _one(
        connection, "SELECT job_id FROM posting_sources WHERE source = ? AND posting_id = ?",
        (source, item.source_id),
    )
    canonical = _one(connection, "SELECT id FROM postings WHERE url = ?", (item.url,))
    values = (
        item.url, item.title, item.employer, item.description, item.location,
        item.workplace, item.role_type, item.published_at, item.deadline,
    )
    added = 0
    if identity is None and canonical is None:
        cursor = connection.execute(
            """
            INSERT INTO postings (
                url, title, employer, description, location, workplace, role_type,
                published_at, deadline, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*values, seen, seen),
        )
        job_id = cursor.lastrowid
        added = 1
    else:
        job_id = (canonical or identity)[0]
        if identity and canonical and identity[0] != job_id:
            obsolete = identity[0]
            connection.execute(
                """
                UPDATE postings
                SET first_seen = min(first_seen, (SELECT first_seen FROM postings WHERE id = ?))
                WHERE id = ?
                """,
                (obsolete, job_id),
            )
            connection.execute("UPDATE posting_sources SET job_id = ? WHERE job_id = ?", (job_id, obsolete))
            connection.execute("DELETE FROM postings WHERE id = ?", (obsolete,))
        has_feed_record = RESEARCH_MARKER in source and _one(
            connection,
            "SELECT 1 FROM posting_sources WHERE job_id = ? AND source NOT LIKE ? LIMIT 1",
            (job_id, f"%{RESEARCH_MARKER}%"),
        )
        if has_feed_record:
            connection.execute(
                "UPDATE postings SET first_seen = min(first_seen, ?), last_seen = max(last_seen, ?) WHERE id = ?",
                (seen, seen, job_id),
            )
        else:
            connection.execute(
                """
                UPDATE postings
                SET url = ?, title = ?, employer = ?, description = ?, location = ?, workplace = ?, role_type = ?,
                    published_at = coalesce(?, published_at), deadline = ?,
                    first_seen = min(first_seen, ?), last_seen = max(last_seen, ?)
                WHERE id = ?
                """,
                (*values, seen, seen, job_id),
            )
    connection.execute(
        """
        INSERT INTO posting_sources (source, posting_id, job_id, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (source, posting_id) DO UPDATE SET
            job_id = excluded.job_id,
            first_seen = min(first_seen, excluded.first_seen),
            last_seen = max(last_seen, excluded.last_seen)
        """,
        (source, item.source_id, job_id, seen, seen),
    )
    return added


def collect_jobs(
    sources: list[Source], data_dir=None,
    fetch: Callable[[Source], Batch] = fetch_source,
) -> list[Check]:
    """Fetch configured sources and commit each result independently."""
    path = private_data_file(DATABASE_NAME, data_dir)
    for suffix in ("-journal", "-wal", "-shm"):
        private_data_file(DATABASE_NAME + suffix, path.parent)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        pass
    checks = []
    with closing(sqlite3.connect(path, timeout=15)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        for source in sources:
            try:
                batch = fetch(source)
                error = ""
            except SourceError as issue:
                batch = Batch([])
                error = str(issue)
            seen = datetime.now(UTC).isoformat()
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                added = sum(_upsert(connection, source.key, item, seen) for item in batch.postings)
                status = "error" if error else "partial" if batch.rejected or batch.partial else "ok"
                check = Check(source.key, status, len(batch.postings), added, batch.rejected, error)
                connection.execute(
                    """
                    INSERT INTO source_checks (source, checked_at, status, seen, added, rejected, detail)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (check.source, seen, status, check.seen, added, check.rejected, error),
                )
                checks.append(check)
    return checks
