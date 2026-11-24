from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml

from job_scout.jobs import collect_jobs
from job_scout.sources import Batch, Posting, Source, SourceError
from job_scout.storage import PROJECT_ROOT
from job_scout.tailoring import ENGINE_FILES, ENGINE_SKILL, load_engine, run_stage, select_job


NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)
SOURCE = Source("greenhouse", "example", "Example Observatory")
POSTING = Posting(
    "42", "Software Developer", "Example Observatory", "https://example.invalid/jobs/example",
    description="Build Python data checks and write tests for a research programme.",
    deadline="2026-08-20",
)


def setup_private_fixture(base):
    engine = base / "engine"
    data = base / "data"
    data.mkdir()
    skill = engine / ENGINE_SKILL
    for name in ENGINE_FILES:
        path = skill / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    (skill / "scripts" / "workflow_state.py").write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "app = Path(sys.argv[2])\n"
        "(app / 'workflow_state.json').write_text(json.dumps({'application_id': app.name}))\n",
        encoding="utf-8",
    )
    for name in ("profile/candidate_facts.yaml", "resume/master_resume.tex"):
        path = engine / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fictional only\n", encoding="utf-8")
    (data / "tailor_engine.yaml").write_text(
        yaml.safe_dump({"version": 1, "root": str(engine)}), encoding="utf-8",
    )
    (data / "job_sources.yaml").write_text(
        "version: 1\nsources:\n- provider: greenhouse\n  board: example\n  employer: Example Observatory\n",
        encoding="utf-8",
    )
    collect_jobs([SOURCE], data, lambda _: Batch([POSTING]))
    (data / "ranked_jobs.json").write_text(json.dumps({
        "profiles": {"industry": [{"id": 1, "url": POSTING.url, "fit_score": 80}], "research": []}
    }), encoding="utf-8")
    return engine, data


class HandoffTests(unittest.TestCase):
    def test_fresh_posting_initializes_existing_workflow_and_keeps_revisions(self):
        with TemporaryDirectory() as folder:
            engine, data = setup_private_fixture(Path(folder).resolve())
            app = select_job(1, data, fetch=lambda _: Batch([POSTING]), now=NOW)
            info = json.loads((app / "handoff.json").read_text(encoding="utf-8"))
            state = json.loads((app / "workflow_state.json").read_text(encoding="utf-8"))
            self.assertEqual(info["status"], "Selected")
            self.assertEqual(info["mode"], "resume_only")
            self.assertEqual(state["application_id"], app.name)
            self.assertIn(POSTING.description, (app / "job_posting.md").read_text(encoding="utf-8"))
            self.assertIn("Reviewer Panel", (app / "START_HERE.md").read_text(encoding="utf-8"))
            self.assertFalse((app / "candidate_facts_snapshot.yaml").exists())
            self.assertFalse(app.is_relative_to(engine))
            self.assertEqual(select_job(1, data, fetch=lambda _: Batch([POSTING]), now=NOW), app)
            updated = replace(POSTING, description="Maintain Python pipelines and write integration tests.")
            revised = select_job(1, data, fetch=lambda _: Batch([updated]), now=NOW)
            self.assertNotEqual(revised, app)
            self.assertIn(updated.description, (revised / "job_posting.md").read_text(encoding="utf-8"))

    def test_refuses_expired_missing_failed_sources_and_invalid_job_ids(self):
        with TemporaryDirectory() as folder:
            _, data = setup_private_fixture(Path(folder).resolve())
            with self.assertRaisesRegex(ValueError, "deadline has passed"):
                select_job(1, data, fetch=lambda _: Batch([replace(POSTING, deadline="2026-08-01")]), now=NOW)
            with self.assertRaisesRegex(ValueError, "no longer listed"):
                select_job(1, data, fetch=lambda _: Batch([]), now=NOW)
            def failed(_):
                raise SourceError("HTTP 503")
            with self.assertRaisesRegex(ValueError, "could not be verified"):
                select_job(1, data, fetch=failed, now=NOW)
            with self.assertRaisesRegex(ValueError, "not in"):
                select_job(2, data, fetch=lambda _: Batch([POSTING]), now=NOW)
            self.assertFalse((data / "applications").exists())

    def test_detects_changed_rank_identity(self):
        with TemporaryDirectory() as folder:
            _, data = setup_private_fixture(Path(folder).resolve())
            (data / "ranked_jobs.json").write_text(json.dumps({
                "profiles": {"industry": [{"id": 1, "url": "https://example.invalid/jobs/another"}]}
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed in storage"):
                select_job(1, data, fetch=lambda _: Batch([POSTING]), now=NOW)

    def test_snapshot_is_local_and_compile_cannot_bypass_review_gate(self):
        with TemporaryDirectory() as folder:
            engine, data = setup_private_fixture(Path(folder).resolve())
            app = select_job(1, data, fetch=lambda _: Batch([POSTING]), now=NOW)
            with patch("job_scout.tailoring._script") as script:
                run_stage("snapshot", app, data)
                call = script.call_args.args
                self.assertEqual(call[2], "build_review_snapshot")
                self.assertIn("--allow-missing-cover-letter", call[3])
                self.assertIn(engine / "profile/candidate_facts.yaml", call[3])
                script.reset_mock()
                with self.assertRaisesRegex(ValueError, "review-gate"):
                    run_stage("compile", app, data)
                script.assert_not_called()
            self.assertFalse((app / "application.json").exists())
            (app / "job_posting.md").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "snapshot changed"):
                run_stage("snapshot", app, data)

    def test_config_and_results_cannot_use_public_checkout(self):
        with self.assertRaisesRegex(ValueError, "outside the checkout"):
            load_engine(PROJECT_ROOT)
        with TemporaryDirectory() as folder:
            _, data = setup_private_fixture(Path(folder).resolve())
            (data / "tailor_engine.yaml").write_text(
                yaml.safe_dump({"version": 1, "root": str(PROJECT_ROOT)}), encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "outside the checkout"):
                load_engine(data)
