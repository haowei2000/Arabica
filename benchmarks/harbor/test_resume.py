import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harbor.run_long_horizon import require_finished_job


class ResumeTests(unittest.TestCase):
    def test_missing_or_invalid_job_cannot_be_ledgered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                require_finished_job(root)
            (root / "result.json").write_text("invalid JSON")
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                require_finished_job(root)

    def test_interrupted_job_cannot_be_silently_ledgered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "result.json"
            result.write_text(json.dumps({"finished_at": None}))
            with self.assertRaisesRegex(RuntimeError, "unfinished"):
                require_finished_job(root)
            result.write_text(json.dumps({"finished_at": "2026-09-07"}))
            require_finished_job(root)
