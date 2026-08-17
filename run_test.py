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

"""Test cases for the run CLI (solve.py + report.py stitched together)."""

import tempfile
import unittest
from pathlib import Path

import report
import run

_SPEC = """
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


class TestRun(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.dir = Path(self._tmpdir.name)
        self.spec_path = self.dir / "spec.yaml"
        self.spec_path.write_text(_SPEC)
        self.output_dir = self.dir / "out"

    def test_writes_solution_and_report(self):
        index_path = run.run(self.spec_path, self.output_dir)

        self.assertTrue((self.output_dir / "solution.yaml").exists())
        self.assertTrue(index_path.exists())
        self.assertTrue((self.output_dir / "all-matches.html").exists())

    def test_report_can_be_regenerated_without_resolving(self):
        run.run(self.spec_path, self.output_dir)
        (self.output_dir / "all-matches.html").unlink()

        report.report(
            self.spec_path, self.output_dir / "solution.yaml", self.output_dir
        )

        self.assertTrue((self.output_dir / "all-matches.html").exists())


if __name__ == "__main__":
    unittest.main()
