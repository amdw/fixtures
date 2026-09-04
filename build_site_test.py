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

"""Test cases for the site-assembly CLI used by the Pages workflow."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_site
import solve

_SPEC = """
name: "{name}"
earliest_match_date: 2025-01-01

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
  1:
    scheme: double_round
    teams: [albany-1, hackney-1]

club_constraints:
  defaults:
    match_count_limits:
      - override_key: venue-capacity
        venue_scope: home
        max_matches: 1
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
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.runs_dir = Path(self._tmpdir.name) / "runs"

    def test_finds_runs_at_any_depth(self) -> None:
        flat = self.runs_dir / "example"
        nested = self.runs_dir / "2026-27" / "draft1"
        for d in (flat, nested):
            d.mkdir(parents=True)
            (d / "spec.yaml").write_text("clubs: {}\n")
            (d / "solution.yaml").write_text("fixtures: []\n")

        self.assertEqual(build_site.find_run_specs(self.runs_dir), [nested, flat])

    def test_ignores_dirs_missing_spec_or_solution(self) -> None:
        (self.runs_dir / "spec-only").mkdir(parents=True)
        (self.runs_dir / "spec-only" / "spec.yaml").write_text("clubs: {}\n")
        (self.runs_dir / "solution-only").mkdir(parents=True)
        (self.runs_dir / "solution-only" / "solution.yaml").write_text("fixtures: []\n")

        self.assertEqual(build_site.find_run_specs(self.runs_dir), [])

    def test_missing_runs_dir(self) -> None:
        self.assertEqual(build_site.find_run_specs(self.runs_dir), [])


class TestBuildReports(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.runs_dir = self.root / "runs"
        self.out_dir = self.root / "_site"
        self.root_index = self.out_dir / "index.html"

    def _run_cli(self) -> None:
        with patch(
            "sys.argv",
            [
                "build_site.py",
                "--runs-dir",
                str(self.runs_dir),
                "--out-dir",
                str(self.out_dir),
            ],
        ):
            build_site.main()

    def test_regenerates_report_and_index_pages_from_source(self) -> None:
        flat = self.runs_dir / "example"
        nested = self.runs_dir / "2026-27" / "draft1"
        _make_run(flat, "Example Season")
        _make_run(nested, "2026-27 Draft 1")

        self._run_cli()

        for rel in (Path("example"), Path("2026-27") / "draft1"):
            out_run = self.out_dir / "runs" / rel
            for page in (
                "all-matches.html",
                "division-1.html",
                "index.html",
                "all-matches.csv",
                "all-matches-by-team.csv",
            ):
                self.assertTrue(
                    (out_run / page).exists(), f"{out_run / page} not written"
                )
        self.assertIn(
            "Example Season",
            (self.out_dir / "runs" / "example" / "all-matches.html").read_text(),
        )

        root = self.root_index.read_text()
        self.assertIn('<a href="runs/example/">', root)
        self.assertIn('<a href="runs/2026-27/draft1/">', root)

    def test_writes_nothing_into_the_source_runs_dir(self) -> None:
        _make_run(self.runs_dir / "example", "Example Season")

        self._run_cli()

        strays = sorted(
            p.name
            for p in (self.runs_dir / "example").iterdir()
            if p.name not in ("spec.yaml", "solution.yaml")
        )
        self.assertEqual(
            strays, [], f"build_site.py wrote {strays} back into the source runs dir"
        )

    def test_drops_runs_removed_from_the_source(self) -> None:
        _make_run(self.runs_dir / "keep", "Keep")
        _make_run(self.runs_dir / "drop", "Drop")
        self._run_cli()
        self.assertTrue((self.out_dir / "runs" / "drop" / "index.html").exists())

        shutil.rmtree(self.runs_dir / "drop")
        self._run_cli()

        self.assertFalse((self.out_dir / "runs" / "drop").exists())
        self.assertTrue((self.out_dir / "runs" / "keep" / "index.html").exists())
        self.assertNotIn('href="runs/drop/"', self.root_index.read_text())

    def test_does_not_re_solve(self) -> None:
        run_dir = self.runs_dir / "example"
        _make_run(run_dir, "Example Season")
        before = (run_dir / "solution.yaml").read_text()

        self._run_cli()

        self.assertEqual((run_dir / "solution.yaml").read_text(), before)

    def test_no_runs_writes_empty_index(self) -> None:
        self.runs_dir.mkdir()
        self._run_cli()
        self.assertIn("No runs yet", self.root_index.read_text())


if __name__ == "__main__":
    unittest.main()
