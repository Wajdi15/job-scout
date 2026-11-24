from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from job_scout.jobs import collect_jobs
from job_scout.research import load_research
from job_scout.search import search
from job_scout.sources import Batch, Posting, Source, fetch_source
from job_scout.storage import PROJECT_ROOT
from job_scout.tailoring import select_job
from test_tailoring import NOW, setup_private_fixture


def row(source="inria", url="https://jobs.inria.fr/public/classic/en/offres/2026-00001"):
    return {"source": source, "url": url, "open": True, "title": "Fictional Research Engineer",
            "employer": "Example University", "summary": "Requirements: Python and automated testing."}


def bundle(root, rows, *, name="research_jobs.json", checked=None, coverage=None):
    path = root / name
    path.write_text(json.dumps({
        "version": 1, "checked_at": (checked or datetime.now(UTC)).isoformat(),
        "coverage": coverage or {"inria": "ok"}, "postings": rows,
    }), encoding="utf-8")
    return path


class PublicResearchTests(unittest.TestCase):
    def test_rejects_non_posting_urls_closed_items_and_stale_inputs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [row(), {**row(), "open": False},
                       row(url="https://jobs.inria.fr/public/classic/en/offres"),
                       row(url="https://example.invalid/jobs/123"),
                       {**row(), "deadline": "2026-01-01"}]
            p = bundle(root, entries, checked=NOW)
            research = load_research(p, now=NOW)
            self.assertEqual(len(research.postings["inria"]), 1)
            self.assertEqual(research.rejected["inria"], 4)
            with self.assertRaisesRegex(ValueError, "stale"):
                load_research(p, now=NOW + timedelta(hours=7))
            with self.assertRaisesRegex(ValueError, "outside the checkout"):
                load_research(PROJECT_ROOT / "scratch.json", now=NOW)

    def test_deduplicates_alternate_posting_urls_and_preserves_feed_text(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            url = "https://jobs.smartrecruiters.com/example/12345-fictional"
            source = Source("smartrecruiters", "example", "Example")
            original = Posting("12345", "Engineer", "Example", url, description="Official full text.")
            collect_jobs([source], root, lambda _: Batch([original]))
            research = load_research(bundle(root, [
                row("smartrecruiters", url + "?utm_source=fixture"),
                row("smartrecruiters", url + "?ref=fixture"),
            ], coverage={"smartrecruiters": "ok"}))
            self.assertEqual(len(research.postings["smartrecruiters"]), 1)
            check, = collect_jobs(research.sources, root, fetch=research.fetch)
            self.assertEqual(check.added, 0)
            with closing(sqlite3.connect(root / "jobs.sqlite")) as db:
                self.assertEqual(db.execute("SELECT description FROM postings").fetchall(),
                                 [("Official full text.",)])
                self.assertEqual(db.execute("SELECT count(*) FROM posting_sources").fetchone()[0], 2)

    def test_smartrecruiters_uses_official_detail_and_reports_partial_board(self):
        source = Source("smartrecruiters", "example", "Example")
        urls = []

        def fetch(url, accept):
            urls.append(url)
            if "?" in url:
                return b'{"totalFound":900,"content":[{"id":"12345"}]}'
            return json.dumps({
                "active": True, "visibility": "PUBLIC", "name": "Engineer",
                "postingUrl": "https://jobs.smartrecruiters.com/example/12345-engineer",
                "jobAd": {"sections": {"qualifications": {"text": "<p>Python</p>"}}},
            }).encode()

        batch = fetch_source(source, fetch)
        self.assertTrue(batch.partial)
        self.assertEqual(batch.postings[0].description, "Requirements: Python")
        self.assertEqual(urls[-1], "https://api.smartrecruiters.com/v1/companies/example/postings/12345")

    def test_search_imports_ranks_and_counts_new_jobs_only(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "search_preferences.yaml").write_text(
                "version: 1\nprofiles:\n  industry: {}\n  research: {}\n", encoding="utf-8"
            )
            (root / "candidate_facts.yaml").write_text(
                "experience:\n- evidence:\n  - id: EXAMPLE-E1\n    statement: Built Python tests and automated testing.\n",
                encoding="utf-8",
            )
            p = bundle(root, [row()], coverage={"inria": "ok", "workable": "error"})
            summary = search(data_dir=root, research_file=p)
            self.assertEqual(summary["new_postings"], 1)
            self.assertEqual(summary["new_matches"], 1)
            self.assertIn("ashby", summary["skipped_research"])
            self.assertIn("error", {check["status"] for check in summary["coverage"]})
            report = json.loads((root / "ranked_jobs.json").read_text(encoding="utf-8"))
            self.assertEqual(report["new_shortlist"], [1])
            self.assertTrue(report["profiles"]["research"][0]["public_page_summary"])
            self.assertEqual(search(data_dir=root, research_file=p)["new_matches"], 0)
            self.assertTrue((root / "search_run.json").is_file())

    def test_web_only_handoff_requires_fresh_matching_verification(self):
        with TemporaryDirectory() as directory:
            _, root = setup_private_fixture(Path(directory).resolve())
            p = bundle(root, [row()], checked=NOW)
            research = load_research(p, now=NOW)
            collect_jobs(research.sources, root, fetch=research.fetch)
            import_url = research.postings["inria"][0].url
            (root / "ranked_jobs.json").write_text(json.dumps({
                "generated_at": NOW.isoformat(),
                "profiles": {"research": [{"id": 2, "url": import_url}]},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "research-verification"):
                select_job(2, root, now=NOW)
            with self.assertRaisesRegex(ValueError, "stale"):
                select_job(2, root, now=NOW + timedelta(minutes=31), verification=p)
            other = bundle(root, [row(url="https://jobs.inria.fr/public/classic/en/offres/2026-00002")],
                           name="other.json", checked=NOW + timedelta(minutes=1))
            with self.assertRaisesRegex(ValueError, "did not confirm"):
                select_job(2, root, now=NOW + timedelta(minutes=1), verification=other)
            verified = bundle(root, [row()], name="recheck.json", checked=NOW + timedelta(minutes=1))
            app = select_job(2, root, now=NOW + timedelta(minutes=1), verification=verified)
            self.assertIn("Summary of the verified public posting", (app / "job_posting.md").read_text(encoding="utf-8"))
