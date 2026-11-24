import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from job_scout.preferences import initialize_preferences, load_preferences, parse_preferences
from job_scout.storage import PROJECT_ROOT


class SearchPreferencesTests(unittest.TestCase):
    def test_public_example_covers_both_searches(self):
        example = PROJECT_ROOT / "examples" / "search-preferences.example.yaml"
        profiles = parse_preferences(example.read_text(encoding="utf-8"))
        industry = profiles["industry"]
        self.assertEqual(industry.filters["regions"].preferred, ("France", "Canada"))
        self.assertEqual(industry.filters["work_modes"].required, ("remote", "hybrid"))
        self.assertEqual(industry.title_aliases["Data Pipeline Engineer"], ("Data Infrastructure Engineer",))
        self.assertEqual(profiles["research"].filters["phd_funding"].required, ("funded",))

    def test_initialization_uses_evidence_and_preserves_user_preferences(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            facts = root / "facts.yaml"
            facts.write_text(
                "experience:\n"
                "  - title: Applied Scientist\n"
                "    evidence:\n"
                "      - id: EV-1\n"
                "        statement: Built computer vision tools.\n"
                "  - title: Unsupported Title\n",
                encoding="utf-8",
            )
            initialize_preferences(root, facts)
            file = root / "search_preferences.yaml"
            industry = load_preferences(root)["industry"]
            self.assertEqual(
                industry.filters["titles"].preferred,
                ("Applied Scientist", "Computer Vision Engineer"),
            )
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)

            edited = file.read_text(encoding="utf-8").replace("Applied Scientist", "New Search Title")
            file.write_text(edited, encoding="utf-8")
            self.assertIn("New Search Title", load_preferences(root)["industry"].filters["titles"].preferred)
            with self.assertRaisesRegex(ValueError, "already exists"):
                initialize_preferences(root, facts)
            self.assertEqual(file.read_text(encoding="utf-8"), edited)

    def test_rejects_unsafe_checkout_paths(self):
        with self.assertRaisesRegex(ValueError, "outside the checkout"):
            initialize_preferences(PROJECT_ROOT)
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "outside the checkout"):
                initialize_preferences(directory, PROJECT_ROOT / "examples" / "fictional-candidate.json")
            self.assertFalse((Path(directory) / "search_preferences.yaml").exists())

    def test_rejects_preferences_symlink(self):
        with TemporaryDirectory() as directory:
            file = Path(directory) / "search_preferences.yaml"
            target = PROJECT_ROOT / "examples" / "search-preferences.example.yaml"
            try:
                file.symlink_to(target)
            except (NotImplementedError, OSError):
                self.skipTest("File symlinks are unavailable.")
            with self.assertRaisesRegex(ValueError, "outside the checkout"):
                load_preferences(directory)

    def test_rejects_conflicts_invalid_thresholds_and_aliases(self):
        cases = (
            ("    regions: {required: [Canada], excluded: [canada]}\n", "conflicting"),
            ("    min_alert_score: true\n", "integer"),
            ("    title_aliases: {A: [B]}\n", "configured title"),
            ("    work_modes: {required: [occasionally]}\n", "work_modes accepts"),
            ("    regoins: {}\n", "unrecognized"),
        )
        for industry, error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(ValueError, error):
                    parse_preferences(f"version: 1\nprofiles:\n  industry:\n{industry}  research: {{}}\n")

    def test_rejects_duplicated_yaml_keys(self):
        with self.assertRaisesRegex(ValueError, "keys must be unique"):
            parse_preferences("version: 1\nprofiles: {industry: {}, industry: {}, research: {}}\n")

    def test_rejects_nonstandard_yaml_tags(self):
        with self.assertRaisesRegex(ValueError, "Invalid YAML"):
            parse_preferences("version: 1\nprofiles: !nonstandard {}\n")
