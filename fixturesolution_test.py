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

"""Test cases for the solution YAML reader/writer."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

import fixturesolution
import fmodel

_ALBANY_1 = fmodel.Team(division=1, club="albany", index=1)
_HACKNEY_1 = fmodel.Team(division=1, club="hackney", index=1)
_HACKNEY_2 = fmodel.Team(
    division=1, club="hackney", index=2, name_override="Hackney Herons"
)

_TEAM_IDS = {
    "albany-1": ("albany", 1),
    "hackney-1": ("hackney", 1),
    "hackney-2": ("hackney", 2),
}


def _sf(home: fmodel.Team, away: fmodel.Team, d: date) -> fmodel.ScheduledFixture:
    return fmodel.ScheduledFixture(
        fixture=fmodel.Fixture(home_team=home, away_team=away), date=d
    )


class TestSaveAndLoadSolution(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.path = Path(self._tmpdir.name) / "solution.yaml"

    def test_round_trip(self):
        fixtures = [
            _sf(_ALBANY_1, _HACKNEY_1, date(2025, 9, 1)),
            _sf(_HACKNEY_2, _ALBANY_1, date(2025, 9, 15)),
        ]
        fixturesolution.save_solution(fixtures, _TEAM_IDS, self.path)

        loaded = fixturesolution.load_solution(
            self.path, [_ALBANY_1, _HACKNEY_1, _HACKNEY_2], _TEAM_IDS
        )
        self.assertCountEqual(loaded, fixtures)

    def test_load_recovers_division_and_name_override(self):
        """Loading resolves each entry against the given teams, so fields not stored
        in the solution file itself (division, name_override) come back correctly."""
        fixturesolution.save_solution(
            [_sf(_HACKNEY_2, _ALBANY_1, date(2025, 9, 15))], _TEAM_IDS, self.path
        )

        [loaded] = fixturesolution.load_solution(
            self.path, [_ALBANY_1, _HACKNEY_1, _HACKNEY_2], _TEAM_IDS
        )
        self.assertEqual(loaded.fixture.home_team, _HACKNEY_2)
        self.assertEqual(loaded.fixture.home_team.name_override, "Hackney Herons")

    def test_written_format_uses_team_ids(self):
        fixturesolution.save_solution(
            [_sf(_ALBANY_1, _HACKNEY_1, date(2025, 9, 1))], _TEAM_IDS, self.path
        )
        contents = self.path.read_text()
        self.assertIn("home: albany-1", contents)
        self.assertIn("away: hackney-1", contents)
        self.assertIn("date: 2025-09-01", contents)

    def test_no_anchors_or_aliases_for_repeated_dates(self):
        """Regression test: PyYAML's default dumper would alias repeated identical
        date objects (&id001/*id001) rather than writing them out literally, since
        fmodel.solve() reuses the same date object for every fixture on a given
        date. The written file should always use literal dates."""
        shared_date = date(2025, 9, 1)
        fixtures = [
            _sf(_ALBANY_1, _HACKNEY_1, shared_date),
            _sf(_HACKNEY_2, _ALBANY_1, shared_date),
        ]
        fixturesolution.save_solution(fixtures, _TEAM_IDS, self.path)
        contents = self.path.read_text()
        self.assertNotIn("&id", contents)
        self.assertNotIn("*id", contents)
        self.assertEqual(contents.count("2025-09-01"), 2)

    def test_save_unknown_team(self):
        with self.assertRaisesRegex(fixturesolution.SolutionError, "albany"):
            fixturesolution.save_solution(
                [_sf(_ALBANY_1, _HACKNEY_1, date(2025, 9, 1))],
                {"hackney-1": ("hackney", 1)},
                self.path,
            )

    def test_missing_fixtures_key(self):
        self.path.write_text("not_fixtures: []\n")
        with self.assertRaisesRegex(fixturesolution.SolutionError, "fixtures"):
            fixturesolution.load_solution(self.path, [_ALBANY_1, _HACKNEY_1], _TEAM_IDS)

    def test_fixtures_not_a_list(self):
        self.path.write_text("fixtures: not-a-list\n")
        with self.assertRaisesRegex(fixturesolution.SolutionError, "list"):
            fixturesolution.load_solution(self.path, [_ALBANY_1, _HACKNEY_1], _TEAM_IDS)

    def test_entry_missing_field(self):
        self.path.write_text("fixtures:\n  - home: albany-1\n    date: 2025-09-01\n")
        with self.assertRaisesRegex(fixturesolution.SolutionError, "away"):
            fixturesolution.load_solution(self.path, [_ALBANY_1, _HACKNEY_1], _TEAM_IDS)

    def test_unknown_team_reference(self):
        self.path.write_text(
            "fixtures:\n  - home: nonexistent\n    away: hackney-1\n    date: 2025-09-01\n"
        )
        with self.assertRaisesRegex(fixturesolution.SolutionError, "nonexistent"):
            fixturesolution.load_solution(self.path, [_ALBANY_1, _HACKNEY_1], _TEAM_IDS)

    def test_invalid_date(self):
        self.path.write_text(
            "fixtures:\n  - home: albany-1\n    away: hackney-1\n    date: not-a-date\n"
        )
        with self.assertRaisesRegex(fixturesolution.SolutionError, "not-a-date"):
            fixturesolution.load_solution(self.path, [_ALBANY_1, _HACKNEY_1], _TEAM_IDS)

    def test_empty_fixtures_list(self):
        fixturesolution.save_solution([], _TEAM_IDS, self.path)
        loaded = fixturesolution.load_solution(
            self.path, [_ALBANY_1, _HACKNEY_1], _TEAM_IDS
        )
        self.assertEqual(loaded, [])


if __name__ == "__main__":
    unittest.main()
