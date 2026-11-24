import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from job_scout.storage import PROJECT_ROOT, private_data_dir


class PrivateDataDirectoryTests(unittest.TestCase):
    def test_accepts_external_directory_without_creating_it(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "candidate-data"
            self.assertEqual(private_data_dir(target), target.resolve())
            self.assertFalse(target.exists())

    def test_rejects_checkout_and_children(self):
        for target in (PROJECT_ROOT, PROJECT_ROOT / "candidate-data"):
            with self.subTest(target=target.name):
                with self.assertRaisesRegex(ValueError, "outside the checkout"):
                    private_data_dir(target)

    def test_rejects_external_symlink_into_checkout(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "workspace-link"
            try:
                target.symlink_to(PROJECT_ROOT, target_is_directory=True)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"Directory symlink not available: {error.__class__.__name__}")
            with self.assertRaisesRegex(ValueError, "outside the checkout"):
                private_data_dir(target / "candidate-data")

    def test_rejects_relative_path_and_regular_file(self):
        with self.assertRaisesRegex(ValueError, "absolute path"):
            private_data_dir("candidate-data")
        with TemporaryDirectory() as directory:
            target = Path(directory) / "file"
            target.touch()
            with self.assertRaisesRegex(ValueError, "must be a directory"):
                private_data_dir(target)

    def test_cli_rejects_environment_override_into_checkout(self):
        result = subprocess.run(
            [sys.executable, "-m", "job_scout", "check-data-dir"],
            cwd=PROJECT_ROOT,
            env={**os.environ, "JOB_SCOUT_DATA_DIR": str(PROJECT_ROOT / "candidate-data")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("outside the checkout", result.stderr)
        self.assertNotIn(str(PROJECT_ROOT), result.stderr)


if __name__ == "__main__":
    unittest.main()
