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

"""Test cases for the YAML fixture specification reader."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

import fixturespec
import fmodel

_MINIMAL_SPEC = """
clubs:
  albany:
    name: Albany
    home_venue: Albany Sports Hall
    home_start_time: "19:30"
    home_time_limit: "75+15"
  hackney:
    name: Hackney
    home_venue: Hackney Community Centre
    home_start_time: "19:00"
    home_time_limit: "60+15"

teams:
  albany-1:
    club: albany
    index: 1
    division: 1
  hackney-1:
    club: hackney
    index: 1
    division: 1

divisions:
  1: [albany-1, hackney-1]

home_dates:
  clubs:
    albany: [2025-09-01, 2025-09-29]
    hackney: [2025-09-15]

unavailable_away_dates:
  clubs:
    albany: [2025-12-25]
"""


class TestLoadSpec(unittest.TestCase):
    """Test cases for load_spec()."""

    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.dir = Path(self._tmpdir.name)

    def _write(self, contents: str, name: str = "spec.yaml") -> Path:
        path = self.dir / name
        path.write_text(contents)
        return path

    def test_minimal_spec(self):
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)

        self.assertEqual(
            spec.clubs["albany"],
            fmodel.Club(
                name="Albany",
                home_venue="Albany Sports Hall",
                home_start_time="19:30",
                home_time_limit="75+15",
            ),
        )

        self.assertCountEqual(
            spec.parameters.teams,
            [
                fmodel.Team(division=1, club="albany", index=1),
                fmodel.Team(division=1, club="hackney", index=1),
            ],
        )
        self.assertEqual(
            spec.parameters.home_dates["albany"], [date(2025, 9, 1), date(2025, 9, 29)]
        )
        self.assertEqual(spec.parameters.home_dates["hackney"], [date(2025, 9, 15)])
        self.assertEqual(
            spec.parameters.unavailable_away_dates["albany"], [date(2025, 12, 25)]
        )
        # hackney has no unavailable_away_dates entry, should default to empty
        self.assertEqual(spec.parameters.unavailable_away_dates["hackney"], [])
        self.assertEqual(spec.parameters.min_gap_days, 7)
        self.assertEqual(spec.parameters.max_concurrent_home_matches, 2)
        self.assertEqual(spec.name, "")
        self.assertFalse(spec.draft)

    def test_run_name_and_draft(self):
        path = self._write(_MINIMAL_SPEC + '\nname: "2025-26 Season"\ndraft: true\n')
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.name, "2025-26 Season")
        self.assertTrue(spec.draft)

    def test_draft_must_be_a_boolean(self):
        path = self._write(_MINIMAL_SPEC + "\ndraft: notabool\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "draft"):
            fixturespec.load_spec(path)

    def test_name_must_be_a_string(self):
        path = self._write(_MINIMAL_SPEC + "\nname: [1, 2]\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "name"):
            fixturespec.load_spec(path)

    def test_name_override(self):
        path = self._write(
            _MINIMAL_SPEC.replace(
                "  hackney-1:\n    club: hackney\n    index: 1\n    division: 1",
                "  hackney-1:\n    club: hackney\n    index: 1\n    division: 1\n"
                '    name_override: "Hackney Herons"',
            )
        )
        spec = fixturespec.load_spec(path)
        team = next(t for t in spec.parameters.teams if t.club == "hackney")
        self.assertEqual(team.name_override, "Hackney Herons")

    def test_overridden_constraints(self):
        path = self._write(
            _MINIMAL_SPEC + "\nmin_gap_days: 10\nmax_concurrent_home_matches: 3\n"
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.min_gap_days, 10)
        self.assertEqual(spec.parameters.max_concurrent_home_matches, 3)

    def test_missing_clubs(self):
        path = self._write("teams: {}\ndivisions: {}\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "clubs"):
            fixturespec.load_spec(path)

    def test_club_missing_field(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue: Albany Sports Hall
    home_start_time: "19:30"
teams: {}
divisions: {}
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "home_time_limit"):
            fixturespec.load_spec(path)

    def test_missing_teams(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "teams"):
            fixturespec.load_spec(path)

    def test_team_references_unknown_club(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  hackney-1:
    club: hackney
    index: 1
    division: 1
divisions:
  1: [hackney-1]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "hackney"):
            fixturespec.load_spec(path)

    def test_duplicate_club_index(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  albany-1:
    club: albany
    index: 1
    division: 1
  albany-1-again:
    club: albany
    index: 1
    division: 1
divisions:
  1: [albany-1, albany-1-again]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "index 1"):
            fixturespec.load_spec(path)

    def test_missing_divisions_section(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  albany-1:
    club: albany
    index: 1
    division: 1
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "divisions"):
            fixturespec.load_spec(path)

    def test_team_missing_from_divisions(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  albany-1:
    club: albany
    index: 1
    division: 1
  albany-2:
    club: albany
    index: 2
    division: 1
divisions:
  1: [albany-1]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "albany-2"):
            fixturespec.load_spec(path)

    def test_division_mismatch_between_team_and_divisions_section(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  albany-1:
    club: albany
    index: 1
    division: 1
divisions:
  2: [albany-1]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "albany-1"):
            fixturespec.load_spec(path)

    def test_team_listed_in_two_divisions(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  albany-1:
    club: albany
    index: 1
    division: 1
divisions:
  1: [albany-1]
  2: [albany-1]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "more than one division"):
            fixturespec.load_spec(path)

    def test_invalid_date_string(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  albany-1:
    club: albany
    index: 1
    division: 1
divisions:
  1: [albany-1]
home_dates:
  clubs:
    albany: ["not-a-date"]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "not-a-date"):
            fixturespec.load_spec(path)

    def test_unsupported_dates_subsection(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  albany-1:
    club: albany
    index: 1
    division: 1
divisions:
  1: [albany-1]
home_dates:
  teams:
    albany-1: [2025-09-01]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "not supported"):
            fixturespec.load_spec(path)

    def test_top_level_not_a_mapping(self):
        path = self._write("- just\n- a\n- list\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "mapping"):
            fixturespec.load_spec(path)

    def test_solves_end_to_end(self):
        """A loaded spec's Parameters should be usable directly with fmodel.solve()."""
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        fixtures = list(fmodel.solve(spec.parameters))
        self.assertEqual(len(fixtures), 2)  # Albany v Hackney and Hackney v Albany


if __name__ == "__main__":
    unittest.main()
