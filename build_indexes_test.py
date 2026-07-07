# Copyright 2026 Andrew Medworth
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test cases for the dependency-free index-rebuilding CLI used by the Pages workflow."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_indexes


class TestBuildIndexes(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.runs_dir = self.root / "runs"
        self.root_index = self.root / "index.html"

    def test_rebuilds_run_and_root_indexes(self):
        run_dir = self.runs_dir / "2025-26-season"
        run_dir.mkdir(parents=True)
        (run_dir / "all-matches.html").write_text("<title>All matches</title>")
        (run_dir / "division-1.html").write_text("<title>Division 1</title>")

        with patch(
            "sys.argv",
            [
                "build_indexes.py",
                "--runs-dir",
                str(self.runs_dir),
                "--root-index",
                str(self.root_index),
            ],
        ):
            build_indexes.main()

        self.assertTrue((run_dir / "index.html").exists())
        self.assertIn("division-1.html", (run_dir / "index.html").read_text())

        self.assertTrue(self.root_index.exists())
        self.assertIn("2025-26-season", self.root_index.read_text())

    def test_no_runs_dir(self):
        with patch(
            "sys.argv",
            [
                "build_indexes.py",
                "--runs-dir",
                str(self.runs_dir),
                "--root-index",
                str(self.root_index),
            ],
        ):
            build_indexes.main()
        self.assertIn("No runs yet", self.root_index.read_text())


if __name__ == "__main__":
    unittest.main()
