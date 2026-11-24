from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from job_scout.jobs import collect_jobs
from job_scout.sources import Batch, Posting, Source, SourceError, fetch_source, parse_sources
from job_scout.storage import PROJECT_ROOT


class SourceTests(unittest.TestCase):
    def test_greenhouse_preserves_source_dates_and_posting_url(self):
        source = Source("greenhouse", "example", "Example")
        content = json.dumps({
            "jobs": [
                {
                    "id": 42,
                    "title": "Data Engineer",
                    "absolute_url": "https://boards.greenhouse.io/example/jobs/42?utm_source=ref",
                    "content": "&lt;p&gt;Build systems&lt;/p&gt;<script>hide this</script>",
                    "first_published": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-03T00:00:00Z",
                    "application_deadline": "2026-01-30",
                },
                {"id": 43, "title": "Another role", "updated_at": "2026-01-03T00:00:00Z"},
                {"id": 44, "title": "Prospect form", "internal_job_id": None},
                {"id": None},
            ]
        }).encode()

        def fetch(url, accept):
            self.assertEqual(url, "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true")
            return content

        batch = fetch_source(source, fetch)
        first, second = batch.postings
        self.assertEqual(first.url, "https://job-boards.greenhouse.io/example/jobs/42")
        self.assertEqual(first.description, "Build systems")
        self.assertEqual(first.published_at, "2026-01-01T00:00:00+00:00")
        self.assertEqual(first.deadline, "2026-01-30")
        self.assertIsNone(second.published_at)
        self.assertEqual(batch.rejected, 1)

    def test_lever_eu_pagination_and_missing_deadline(self):
        source = Source("lever", "Example", "Example", "eu")
        offsets = []

        def fetch(url, accept):
            parts = urlsplit(url)
            self.assertEqual(parts.hostname, "api.eu.lever.co")
            offset = int(parse_qs(parts.query)["skip"][0])
            offsets.append(offset)
            rows = [
                {"id": f"job-{offset + index}", "text": "Engineer", "createdAt": 1_767_225_600_000,
                 "lists": [{"text": "Requirements", "content": "<li>Python</li>"}]}
                for index in range(2 if offset == 0 else 1)
            ]
            return json.dumps(rows).encode()

        with patch("job_scout.sources.PAGE_SIZE", 2):
            batch = fetch_source(source, fetch)
        self.assertEqual(offsets, [0, 2])
        self.assertEqual([item.source_id for item in batch.postings], ["job-0", "job-1", "job-2"])
        self.assertEqual(batch.postings[0].description, "Python")
        self.assertTrue(batch.postings[0].url.startswith("https://jobs.eu.lever.co/"))
        self.assertIsNone(batch.postings[0].published_at)
        self.assertIsNone(batch.postings[0].deadline)

    def test_bath_resolves_official_vacancy_and_rejects_xml_declarations(self):
        source = Source("bath", "research", "Example University")
        xml = b"""<rss><channel><item><title>Research Engineer</title>
        <link>https://www.bath.ac.uk/jobs/rss/click.aspx?ref=test-1</link>
        <pubDate>Mon, 23 Nov 2026 00:00:00 GMT</pubDate>
        <description>&lt;p&gt;Research computing&lt;/p&gt;&lt;p&gt;Closing Date: 04 Dec 2026&lt;/p&gt;</description>
        </item></channel></rss>"""
        batch = fetch_source(source, lambda url, accept: xml)
        job = batch.postings[0]
        self.assertEqual(job.url, "https://www.bath.ac.uk/jobs/Vacancy.aspx?ref=TEST-1")
        self.assertEqual(job.published_at, "2026-11-23T00:00:00+00:00")
        self.assertEqual(job.description, "Research computing Closing Date: 04 Dec 2026")
        self.assertEqual(job.deadline, "2026-12-04")
        with self.assertRaisesRegex(SourceError, "declarations"):
            fetch_source(source, lambda url, accept: b"<!DOCTYPE rss>" + xml)

    def test_watchlist_rejects_urls_as_board_identifiers(self):
        example = PROJECT_ROOT / "examples" / "job-sources.example.yaml"
        text = example.read_text(encoding="utf-8")
        self.assertEqual([source.provider for source in parse_sources(text)], ["greenhouse", "lever", "bath"])
        with self.assertRaisesRegex(ValueError, "without a URL"):
            parse_sources(text.replace("example-observatory", "https://example.invalid"))


class CollectionTests(unittest.TestCase):
    def test_repeated_postings_and_failures_keep_first_seen(self):
        source = Source("greenhouse", "example", "Example")
        mirror = Source("greenhouse", "another-board", "Example")
        failing = Source("bath", "research", "Example University")
        empty = Source("lever", "empty-board", "Example")
        job = Posting("101", "Engineer", "Example", "https://example.invalid/job/101")
        with TemporaryDirectory() as directory:
            first = collect_jobs([source], directory, lambda _: Batch([job]))
            path = Path(directory) / "jobs.sqlite"
            with closing(sqlite3.connect(path)) as db:
                original_seen = db.execute("SELECT first_seen FROM postings").fetchone()[0]

            def fetch(target):
                if target == failing:
                    raise SourceError("HTTP 503")
                if target == empty:
                    return Batch([])
                return Batch([replace(job, title="Data Engineer")], rejected=int(target == mirror))

            checks = collect_jobs([failing, source, mirror, empty], directory, fetch)
            self.assertEqual(first[0].added, 1)
            self.assertEqual([check.status for check in checks], ["error", "ok", "partial", "ok"])
            self.assertEqual(sum(check.added for check in checks), 0)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(
                    db.execute("SELECT title, first_seen FROM postings").fetchall(),
                    [("Data Engineer", original_seen)],
                )
                self.assertEqual(db.execute("SELECT count(*) FROM posting_sources").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT count(*) FROM source_checks").fetchone()[0], 5)

    def test_canonical_link_merge_preserves_both_source_ids(self):
        source = Source("greenhouse", "example", "Example")
        job_a = Posting("101", "Engineer", "Example", "https://example.invalid/job/101")
        job_b = Posting("102", "Engineer", "Example", "https://example.invalid/job/102")
        with TemporaryDirectory() as directory:
            collect_jobs([source], directory, lambda _: Batch([job_a, job_b]))
            collect_jobs([source], directory, lambda _: Batch([replace(job_a, url=job_b.url)]))
            with closing(sqlite3.connect(Path(directory) / "jobs.sqlite")) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM postings").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT count(*) FROM posting_sources").fetchone()[0], 2)
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_collection_rejects_database_symlink_into_checkout(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.sqlite"
            try:
                path.symlink_to(PROJECT_ROOT / "README.md")
            except (NotImplementedError, OSError):
                self.skipTest("File symlinks are unavailable.")
            with self.assertRaisesRegex(ValueError, "outside the checkout"):
                collect_jobs([], directory)
