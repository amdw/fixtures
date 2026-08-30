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

"""Test cases for the report CLI."""

import tempfile
import unittest
from pathlib import Path

import report
import solve

_SPEC = """
name: "Test Season"

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
    max_concurrent_home_matches: 1
  albany:
    home_dates: [2025-09-01, 2025-09-29]
  hackney:
    home_dates: [2025-09-15]
"""


class TestReport(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.dir = Path(self._tmpdir.name)
        self.spec_path = self.dir / "spec.yaml"
        self.spec_path.write_text(_SPEC)
        self.output_dir = self.dir / "out"
        self.solution_path = solve.solve(self.spec_path, self.output_dir)

    def test_regenerates_report_from_solution_alone(self) -> None:
        index_path = report.report(self.spec_path, self.solution_path, self.output_dir)

        self.assertTrue(index_path.exists())
        self.assertTrue((self.output_dir / "all-matches.html").exists())
        self.assertIn("Test Season", (self.output_dir / "all-matches.html").read_text())

    def test_also_writes_csv_exports(self) -> None:
        report.report(self.spec_path, self.solution_path, self.output_dir)

        for name in ("all-matches.csv", "all-matches-by-team.csv"):
            self.assertTrue((self.output_dir / name).exists(), f"{name} not written")
        self.assertIn("Albany 1", (self.output_dir / "all-matches.csv").read_text())

    def test_run_index_links_to_the_csv_exports(self) -> None:
        report.report(self.spec_path, self.solution_path, self.output_dir)

        index = (self.output_dir / "index.html").read_text()
        self.assertIn('href="all-matches.csv"', index)
        self.assertIn('href="all-matches-by-team.csv"', index)

    def test_does_not_require_resolving(self) -> None:
        """Deleting nothing but the intermediate solving step should still work:
        report.py only reads solution.yaml, never calls fmodel.solve()."""
        original_contents = self.solution_path.read_text()

        report.report(self.spec_path, self.solution_path, self.output_dir)

        # solution.yaml is untouched by reporting
        self.assertEqual(self.solution_path.read_text(), original_contents)


if __name__ == "__main__":
    unittest.main()
