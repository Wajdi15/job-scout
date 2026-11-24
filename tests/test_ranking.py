from contextlib import closing
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from job_scout.eligibility import assess_eligibility, eligibility_status
from job_scout.jobs import collect_jobs
from job_scout.matching import Candidate, Evidence, extract_criteria, match_criteria
from job_scout.preferences import parse_preferences
from job_scout.ranking import rank_jobs, rank_posting
from job_scout.sources import Batch, Posting, Source
from job_scout.storage import PROJECT_ROOT


NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)
PREFERENCES = """version: 1
profiles:
  industry:
    titles:
      preferred: [Software Engineer]
      excluded: [Sales Engineer]
    title_aliases:
      Software Engineer: [Software Developer]
    sponsorship:
      required: [available]
  research:
    phd_funding:
      required: [funded]
"""
CANDIDATE = Candidate(
    {
        "Python": Evidence("FAKE-1", "Built Python data checks.", "professional"),
        "PyTorch": Evidence("FAKE-2", "Trained PyTorch models.", "professional"),
    },
    {}, (), ({"language": "French", "level": "B2", "id": "FAKE-L1"},), True,
)


def posting(description="", **overrides):
    return {
        "id": 1, "title": "Software Developer", "employer": "Example Observatory",
        "description": description, "url": "https://example.invalid/jobs/example",
        "sources": ["greenhouse:global:example"], "location": "", "workplace": "",
        "role_type": "", "published_at": "2026-08-01", "deadline": "2026-08-20",
        "first_seen": NOW.isoformat(), "last_seen": NOW.isoformat(),
        "seen_in_latest_feed": True, **overrides,
    }


class MatchingTests(unittest.TestCase):
    def test_score_uses_optional_and_alternative_requirements_and_real_evidence(self):
        criteria = extract_criteria(
            "Our organisation runs Terraform. Requirements: Experience with Python or Java. "
            "Preferred qualifications: TensorFlow."
        )
        score, matches = match_criteria(criteria, CANDIDATE)
        self.assertEqual(score, 84)
        self.assertEqual([item["criterion"] for item in matches], ["Python or Java", "TensorFlow"])
        self.assertEqual([item["evidence_id"] for item in matches], ["FAKE-1", "FAKE-2"])
        self.assertEqual(matches[1]["match"], "transferable")
        self.assertEqual(matches[1]["related_skill"], "PyTorch")

    def test_title_and_company_marketing_do_not_contribute_fit(self):
        result = rank_posting(posting("We use Python.", title="Python Engineer"),
                              parse_preferences(PREFERENCES)["industry"], CANDIDATE, NOW)
        self.assertIsNone(result["fit_score"])
        self.assertFalse(result["shortlist"])

    def test_react_native_is_not_extracted_as_react(self):
        self.assertEqual(
            [criterion.skills for criterion in extract_criteria("Requirements: React Native.")],
            [("React Native",)],
        )


class EligibilityTests(unittest.TestCase):
    def test_sponsorship_distinguishes_denial_offer_and_silence(self):
        profile = parse_preferences(PREFERENCES)["industry"]
        for description, expected in (
            ("No visa sponsorship available.", "unmet"),
            ("Visa sponsorship is available.", "met"),
            ("We may provide visa sponsorship.", "unknown"),
            ("Apply using our site.", "unknown"),
        ):
            with self.subTest(expected=expected, description=description):
                checks = assess_eligibility(posting(description), profile, CANDIDATE, NOW)
                self.assertEqual(eligibility_status(checks), expected)

    def test_verified_technical_fit_cannot_mask_language_gap_or_expiry(self):
        item = posting("Requirements: Python. French C1 required. 5+ years of experience. "
                       "Visa sponsorship is available.", deadline="2026-08-10")
        result = rank_posting(item, parse_preferences(PREFERENCES)["industry"], CANDIDATE, NOW)
        self.assertEqual(result["fit_score"], 100)
        checks = {check["check"]: check for check in result["eligibility_checks"]}
        self.assertEqual(checks["Language: french"]["status"], "unmet")
        self.assertEqual(checks["Language: french"]["evidence_id"], "FAKE-L1")
        self.assertEqual(checks["Application deadline"]["status"], "unmet")
        self.assertEqual(checks["Minimum experience"]["years"], 5)
        self.assertFalse(result["shortlist"])

    def test_stale_posting_is_retained_for_review_but_not_shortlisted(self):
        item = posting("Requirements: Python. PyTorch.", seen_in_latest_feed=False)
        result = rank_posting(item, parse_preferences(PREFERENCES)["industry"], CANDIDATE, NOW)
        self.assertEqual(result["fit_score"], 100)
        self.assertFalse(result["shortlist"])
        self.assertIn("Latest source check", {check["check"] for check in result["eligibility_checks"]})

    def test_phd_funding_gate_applies_to_phd_candidates(self):
        profile = parse_preferences(PREFERENCES)["research"]
        for title, expected in (("Unfunded PhD position", "unmet"), ("Research Engineer", None)):
            checks = assess_eligibility(posting("Python experience.", title=title), profile, CANDIDATE, NOW)
            state = next((check["status"] for check in checks if check["check"] == "PhD funding"), None)
            self.assertEqual(state, expected)

    def test_user_title_alias_and_excluded_titles(self):
        profile = parse_preferences(PREFERENCES)["industry"]
        text = "Requirements: Python. PyTorch."
        preferred = rank_posting(posting(text), profile, CANDIDATE, NOW)
        excluded = rank_posting(posting(text, title="Sales Engineer"), profile, CANDIDATE, NOW)
        self.assertIn("titles", preferred["preferred_filters"])
        self.assertTrue(preferred["shortlist"])
        self.assertFalse(excluded["shortlist"])
        self.assertTrue(excluded["excluded"])


class LocalReportTests(unittest.TestCase):
    def test_reports_are_local_and_escape_posting_text(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "search_preferences.yaml").write_text(PREFERENCES, encoding="utf-8")
            facts = root / "candidate_facts.yaml"
            facts.write_text(
                "candidate:\n  email: unused@example.invalid\n"
                "experience:\n- evidence:\n  - id: FAKE-1\n    statement: Built Python data checks.\n",
                encoding="utf-8",
            )
            source = Source("greenhouse", "example", "Example Observatory")
            job = Posting(
                "1", "<script>unsafe</script>", "Example Observatory", "https://example.invalid/jobs/example",
                description="Requirements: Experience with Python and SQL. Visa sponsorship is not offered.",
            )
            collect_jobs([source], root, lambda _: Batch([job]))
            self.assertEqual(rank_jobs(data_dir=root, now=NOW), (1, 0))
            report = json.loads((root / "ranked_jobs.json").read_text(encoding="utf-8"))
            self.assertEqual(report["profiles"]["industry"][0]["criteria"][0]["evidence_id"], "FAKE-1")
            html = (root / "ranked_jobs.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>", html)
            self.assertNotIn("unused@example.invalid", html)
            self.assertIn("&lt;script&gt;", html)
            with closing(sqlite3.connect(root / "jobs.sqlite")) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM postings").fetchone()[0], 1)

    def test_checkout_cannot_hold_reports_or_candidate_inputs(self):
        with self.assertRaisesRegex(ValueError, "outside the checkout"):
            rank_jobs(data_dir=PROJECT_ROOT)
