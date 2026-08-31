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

"""Test cases for the solve CLI."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

import fixturesolution
import fixturespec
import fmodel
import solve

_MINIMAL_SPEC = """
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
        max: 1
  albany:
    home_dates: [2025-09-01, 2025-09-29]
  hackney:
    home_dates: [2025-09-15]
"""


class TestSolve(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.dir = Path(self._tmpdir.name)
        self.spec_path = self.dir / "spec.yaml"
        self.spec_path.write_text(_MINIMAL_SPEC)

    def test_writes_solution_yaml(self) -> None:
        output_dir = self.dir / "out"
        solution_path = solve.solve(self.spec_path, output_dir)

        self.assertEqual(solution_path, output_dir / "solution.yaml")
        self.assertTrue(solution_path.exists())

        loaded = fixturesolution.load_solution(
            solution_path,
            [
                fmodel.Team(division=1, club="albany", index=1),
                fmodel.Team(division=1, club="hackney", index=1),
            ],
            {"albany-1": ("albany", 1), "hackney-1": ("hackney", 1)},
        )
        # Albany v Hackney and Hackney v Albany
        self.assertEqual(len(loaded.fixtures), 2)
        self.assertIn("home: albany-1", solution_path.read_text())

    def test_records_solver_stats_in_solution_yaml(self) -> None:
        output_dir = self.dir / "out"
        solution_path = solve.solve(self.spec_path, output_dir)

        contents = solution_path.read_text()
        self.assertIn("stats:", contents)
        self.assertIn("satisfaction model", contents)
        self.assertIn("CpSolverResponse summary", contents)

        loaded = fixturesolution.load_solution(
            solution_path,
            [
                fmodel.Team(division=1, club="albany", index=1),
                fmodel.Team(division=1, club="hackney", index=1),
            ],
            {"albany-1": ("albany", 1), "hackney-1": ("hackney", 1)},
        )
        self.assertIn("#Variables", loaded.model_stats)
        self.assertIn("status:", loaded.solve_stats)

    def test_records_spec_checksum_matching_the_spec_file(self) -> None:
        output_dir = self.dir / "out"
        solution_path = solve.solve(self.spec_path, output_dir)

        expected = fixturespec.spec_checksum(self.spec_path)
        self.assertTrue(expected.startswith("sha256:"))
        self.assertIn(f"spec_checksum: {expected}", solution_path.read_text())

        loaded = fixturesolution.load_solution(
            solution_path,
            [
                fmodel.Team(division=1, club="albany", index=1),
                fmodel.Team(division=1, club="hackney", index=1),
            ],
            {"albany-1": ("albany", 1), "hackney-1": ("hackney", 1)},
        )
        self.assertEqual(loaded.spec_checksum, expected)

    def test_creates_output_dir(self) -> None:
        output_dir = self.dir / "nested" / "out"
        solution_path = solve.solve(self.spec_path, output_dir)
        self.assertTrue(solution_path.exists())

    def test_overwrites_existing_solution(self) -> None:
        output_dir = self.dir / "out"
        output_dir.mkdir()
        (output_dir / "solution.yaml").write_text("stale: true\n")

        solve.solve(self.spec_path, output_dir)

        self.assertIn("fixtures:", (output_dir / "solution.yaml").read_text())

    def test_earliest_match_date_excludes_earlier_home_dates(self) -> None:
        """Albany has two candidate home dates (2025-09-01 and 2025-09-29); a cutoff
        that excludes the earlier one should still solve, using the later one."""
        output_dir = self.dir / "out"
        solution_path = solve.solve(
            self.spec_path, output_dir, earliest_match_date=date(2025, 9, 2)
        )
        loaded = fixturesolution.load_solution(
            solution_path,
            [
                fmodel.Team(division=1, club="albany", index=1),
                fmodel.Team(division=1, club="hackney", index=1),
            ],
            {"albany-1": ("albany", 1), "hackney-1": ("hackney", 1)},
        )
        for sf in loaded.fixtures:
            self.assertGreaterEqual(sf.date, date(2025, 9, 2))

    def test_no_cutoff_by_default(self) -> None:
        output_dir = self.dir / "out"
        solution_path = solve.solve(self.spec_path, output_dir)
        self.assertTrue(solution_path.exists())


if __name__ == "__main__":
    unittest.main()
