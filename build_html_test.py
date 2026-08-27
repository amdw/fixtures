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

"""Test cases for the HTML-rebuilding CLI used by the Pages workflow."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_html
import solve

_SPEC = """
name: "{name}"

clubs:
  albany:
    name: Albany
    home_venue_name: Albany Sports Hall
    home_venue_address: 1 Albany Road, London
    home_start_time: "19:30"
    home_time_limit: "75+15"
  hackney:
    name: Hackney
    home_venue_name: Hackney Community Centre
    home_venue_address: 2 Hackney Road, London
    home_start_time: "19:00"
    home_time_limit: "60+15"

teams:
  albany-1:
    club: albany
    index: 1
  hackney-1:
    club: hackney
    index: 1

divisions:
  1: [albany-1, hackney-1]

club_constraints:
  defaults:
    max_concurrent_home_matches: 1
  albany:
    home_dates: [2025-09-01, 2025-09-29]
  hackney:
    home_dates: [2025-09-15]
"""


def _make_run(run_dir: Path, name: str) -> None:
    """Write a spec.yaml into run_dir and solve it so solution.yaml sits alongside,
    mirroring a committed run folder (spec + solution, no HTML)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    spec_path = run_dir / "spec.yaml"
    spec_path.write_text(_SPEC.format(name=name))
    solve.solve(spec_path, run_dir)


class TestFindRunSpecs(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.runs_dir = Path(self._tmpdir.name) / "runs"

    def test_finds_runs_at_any_depth(self):
        flat = self.runs_dir / "example"
        nested = self.runs_dir / "2026-27" / "draft1"
        for d in (flat, nested):
            d.mkdir(parents=True)
            (d / "spec.yaml").write_text("clubs: {}\n")
            (d / "solution.yaml").write_text("fixtures: []\n")

        self.assertEqual(build_html.find_run_specs(self.runs_dir), [nested, flat])

    def test_ignores_dirs_missing_spec_or_solution(self):
        (self.runs_dir / "spec-only").mkdir(parents=True)
        (self.runs_dir / "spec-only" / "spec.yaml").write_text("clubs: {}\n")
        (self.runs_dir / "solution-only").mkdir(parents=True)
        (self.runs_dir / "solution-only" / "solution.yaml").write_text("fixtures: []\n")

        self.assertEqual(build_html.find_run_specs(self.runs_dir), [])

    def test_missing_runs_dir(self):
        self.assertEqual(build_html.find_run_specs(self.runs_dir), [])


class TestBuildReports(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.runs_dir = self.root / "runs"
        self.root_index = self.root / "index.html"

    def _run_cli(self) -> None:
        with patch(
            "sys.argv",
            [
                "build_html.py",
                "--runs-dir",
                str(self.runs_dir),
                "--root-index",
                str(self.root_index),
            ],
        ):
            build_html.main()

    def test_regenerates_report_and_index_pages_from_source(self):
        flat = self.runs_dir / "example"
        nested = self.runs_dir / "2026-27" / "draft1"
        _make_run(flat, "Example Season")
        _make_run(nested, "2026-27 Draft 1")

        self._run_cli()

        for run_dir in (flat, nested):
            for page in ("all-matches.html", "division-1.html", "index.html"):
                self.assertTrue(
                    (run_dir / page).exists(), f"{run_dir / page} not written"
                )
        self.assertIn("Example Season", (flat / "all-matches.html").read_text())

        root = self.root_index.read_text()
        self.assertIn("runs/example/index.html", root)
        self.assertIn("runs/2026-27/draft1/index.html", root)

    def test_does_not_re_solve(self):
        run_dir = self.runs_dir / "example"
        _make_run(run_dir, "Example Season")
        before = (run_dir / "solution.yaml").read_text()

        self._run_cli()

        self.assertEqual((run_dir / "solution.yaml").read_text(), before)

    def test_no_runs_writes_empty_index(self):
        self.runs_dir.mkdir()
        self._run_cli()
        self.assertIn("No runs yet", self.root_index.read_text())


if __name__ == "__main__":
    unittest.main()
