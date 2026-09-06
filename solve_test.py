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
from datetime import date, timedelta
from pathlib import Path

import fixturesolution
import fixturespec
import fmodel
import solve

# Home dates comfortably in the future, so the base tests take solve()'s normal
# path (its past-match guard, tested separately below, never fires).
_D1 = (date.today() + timedelta(days=400)).isoformat()
_D2 = (date.today() + timedelta(days=428)).isoformat()
_D3 = (date.today() + timedelta(days=414)).isoformat()

_MINIMAL_SPEC = f"""
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
        matches:
          max: 1
  albany:
    home_dates: [{_D1}, {_D2}]
  hackney:
    home_dates: [{_D3}]
"""

_TEAM_OBJS = [
    fmodel.Team(division=1, club="albany", index=1),
    fmodel.Team(division=1, club="hackney", index=1),
]
_TEAM_IDS = {"albany-1": ("albany", 1), "hackney-1": ("hackney", 1)}


def _load(solution_path: Path) -> fmodel.SolveResult:
    return fixturesolution.load_solution(solution_path, _TEAM_OBJS, _TEAM_IDS)


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

        loaded = _load(solution_path)
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

        loaded = _load(solution_path)
        self.assertIn("#Variables", loaded.model_stats)
        self.assertIn("status:", loaded.solve_stats)

    def test_records_spec_checksum_matching_the_spec_file(self) -> None:
        output_dir = self.dir / "out"
        solution_path = solve.solve(self.spec_path, output_dir)

        expected = fixturespec.spec_checksum(self.spec_path)
        self.assertTrue(expected.startswith("sha256:"))
        self.assertIn(f"spec_checksum: {expected}", solution_path.read_text())

        loaded = _load(solution_path)
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

    def test_season_start_blackout_excludes_earlier_home_dates(self) -> None:
        """A club_constraints.defaults 'season-start' blackout (matches: {max: 0}
        over an open-ended past date_range) keeps every scheduled match after it,
        using Albany's later home date rather than the excluded earlier one."""
        cutoff = date.today() + timedelta(days=410)
        spec_text = _MINIMAL_SPEC.replace(
            "      - override_key: venue-capacity\n",
            "      - override_key: season-start\n"
            "        matches: {max: 0}\n"
            "        date_ranges:\n"
            f"          - {{end_date: {cutoff.isoformat()}}}\n"
            "      - override_key: venue-capacity\n",
        )
        self.spec_path.write_text(spec_text)
        output_dir = self.dir / "out"
        solution_path = solve.solve(self.spec_path, output_dir)
        loaded = _load(solution_path)
        for sf in loaded.fixtures:
            self.assertGreater(sf.date, cutoff)


class TestPastMatchGuard(unittest.TestCase):
    """solve() aborts, before writing anything, if the solved schedule contains
    any match before today -- unless allow_past_matches is set."""

    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.dir = Path(self._tmpdir.name)
        self.spec_path = self.dir / "spec.yaml"
        # Every home date is in the past, so the whole schedule lands before today.
        past = (
            _MINIMAL_SPEC.replace(_D1, "2020-01-07")
            .replace(_D2, "2020-02-04")
            .replace(_D3, "2020-01-21")
        )
        self.spec_path.write_text(past)

    def test_past_matches_raise_and_write_nothing(self) -> None:
        output_dir = self.dir / "out"
        with self.assertRaises(solve.PastMatchError):
            solve.solve(self.spec_path, output_dir)
        self.assertFalse((output_dir / "solution.yaml").exists())

    def test_allow_past_matches_writes_the_solution(self) -> None:
        output_dir = self.dir / "out"
        solution_path = solve.solve(self.spec_path, output_dir, allow_past_matches=True)
        self.assertEqual(len(_load(solution_path).fixtures), 2)

    def test_pinned_past_fixtures_also_raise(self) -> None:
        """A past date trips the guard even when it comes straight from
        fixed_fixtures: the check is purely on the solved ScheduledFixtures."""
        spec_text = self.spec_path.read_text() + (
            "fixed_fixtures:\n"
            "  - home: albany-1\n"
            "    away: hackney-1\n"
            "    date: 2020-01-07\n"
            "  - home: hackney-1\n"
            "    away: albany-1\n"
            "    date: 2020-01-21\n"
        )
        self.spec_path.write_text(spec_text)
        output_dir = self.dir / "out"
        with self.assertRaises(solve.PastMatchError):
            solve.solve(self.spec_path, output_dir)
        self.assertFalse((output_dir / "solution.yaml").exists())
        # ...but the flag still lets it through.
        solution_path = solve.solve(self.spec_path, output_dir, allow_past_matches=True)
        self.assertEqual(len(_load(solution_path).fixtures), 2)


if __name__ == "__main__":
    unittest.main()
