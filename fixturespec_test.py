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

# Clubs/teams/divisions boilerplate, minus 'club_constraints', for tests that need to
# supply their own version of that section (concatenating a second copy on top of an
# existing one would create a duplicate top-level YAML key, which PyYAML resolves by
# silently letting the later one clobber the earlier one).
_BOILERPLATE = """
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
    division: 1
  hackney-1:
    club: hackney
    index: 1
    division: 1

divisions:
  1: [albany-1, hackney-1]
"""

# A valid spec minus 'club_constraints.defaults' (so every club still needs its own
# max_concurrent_home_matches entry to be valid), for tests that need to append their
# own version of that piece.
_MINIMAL_SPEC_NO_MCHM = (
    _BOILERPLATE
    + """
club_constraints:
  albany:
    home_dates: [2025-09-01, 2025-09-29]
    unavailable_away_dates: [2025-12-25]
  hackney:
    home_dates: [2025-09-15]
"""
)

_MINIMAL_SPEC = (
    _MINIMAL_SPEC_NO_MCHM + "  defaults:\n    max_concurrent_home_matches: 1\n"
)

# A three-team spec (two Albany teams plus Hackney) for exclude_fixtures tests, which
# need a division where excluding one team/club still leaves other fixtures behind.
_THREE_TEAM_SPEC = """
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
    division: 1
  albany-2:
    club: albany
    index: 2
    division: 1
  hackney-1:
    club: hackney
    index: 1
    division: 1

divisions:
  1: [albany-1, albany-2, hackney-1]

club_constraints:
  defaults:
    max_concurrent_home_matches: 2
  albany:
    home_dates: [2025-09-01, 2025-10-01, 2025-11-01, 2025-12-01]
  hackney:
    home_dates: [2026-01-01, 2026-02-01]
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
                home_venue_name="Albany Sports Hall",
                home_venue_address="1 Albany Road, London",
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
        # Neither club has its own entry, so both inherit _MINIMAL_SPEC's
        # club_constraints.defaults.max_concurrent_home_matches (1).
        self.assertEqual(
            spec.parameters.max_concurrent_home_matches["albany"],
            fmodel.MaxConcurrentHomeMatches(default=1),
        )
        self.assertEqual(
            spec.parameters.max_concurrent_home_matches["hackney"],
            fmodel.MaxConcurrentHomeMatches(default=1),
        )
        self.assertEqual(spec.name, "")
        self.assertFalse(spec.draft)

    def test_run_name_and_draft(self):
        path = self._write(_MINIMAL_SPEC + '\nname: "2025-26 Season"\ndraft: true\n')
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.name, "2025-26 Season")
        self.assertTrue(spec.draft)

    def test_description(self):
        path = self._write(
            _MINIMAL_SPEC
            + '\ndescription: "Final schedule; refer to ECF LMS for authoritative dates."\n'
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            spec.description,
            "Final schedule; refer to ECF LMS for authoritative dates.",
        )

    def test_description_defaults_to_empty(self):
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.description, "")

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
        path = self._write(_MINIMAL_SPEC + "\nmin_gap_days: 10\n")
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.min_gap_days, 10)

    def test_latest_internal_match_date_absent(self):
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertIsNone(spec.parameters.latest_internal_match_date)

    def test_latest_internal_match_date_parsed(self):
        path = self._write(_MINIMAL_SPEC + "latest_internal_match_date: 2025-12-31\n")
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.latest_internal_match_date, date(2025, 12, 31))

    def test_latest_internal_match_date_invalid(self):
        path = self._write(_MINIMAL_SPEC + "latest_internal_match_date: not-a-date\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "not-a-date"):
            fixturespec.load_spec(path)

    def test_avoid_dates_absent(self):
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            spec.parameters.unavailable_away_dates["albany"], [date(2025, 12, 25)]
        )
        self.assertEqual(spec.parameters.unavailable_away_dates["hackney"], [])

    def test_avoid_dates_merged_into_every_clubs_unavailable_away_dates(self):
        path = self._write(_MINIMAL_SPEC + "avoid_dates: [2025-12-25, 2026-01-01]\n")
        spec = fixturespec.load_spec(path)
        # albany already had 2025-12-25 of its own; avoid_dates adds 2026-01-01
        # without duplicating the date it already had.
        self.assertEqual(
            spec.parameters.unavailable_away_dates["albany"],
            [date(2025, 12, 25), date(2026, 1, 1)],
        )
        # hackney had no unavailable_away_dates of its own; picks up both avoid_dates.
        self.assertEqual(
            spec.parameters.unavailable_away_dates["hackney"],
            [date(2025, 12, 25), date(2026, 1, 1)],
        )

    def test_avoid_dates_invalid_date(self):
        path = self._write(_MINIMAL_SPEC + "avoid_dates: [not-a-date]\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "not-a-date"):
            fixturespec.load_spec(path)

    def test_avoid_dates_duplicate_rejected(self):
        path = self._write(_MINIMAL_SPEC + "avoid_dates: [2025-12-25, 2025-12-25]\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "duplicate date"):
            fixturespec.load_spec(path)

    def test_duplicate_top_level_key_rejected(self):
        path = self._write(_MINIMAL_SPEC + "\nmin_gap_days: 7\nmin_gap_days: 10\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "duplicate key"):
            fixturespec.load_spec(path)

    def test_duplicate_nested_key_rejected(self):
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    max_concurrent_home_matches: 1\n"
            "  albany:\n"
            "    home_dates: [2025-09-01]\n"
            "  albany:\n"
            "    home_dates: [2025-09-08]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "duplicate key"):
            fixturespec.load_spec(path)

    # These tests write standalone specs rather than extending _MINIMAL_SPEC_NO_MCHM,
    # since appending a second 'club_constraints' section (or a second entry for a
    # club already present) would create a duplicate YAML key, which PyYAML resolves
    # by silently letting the later one clobber the earlier one.

    def test_max_concurrent_home_matches_shorthand_int(self):
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    max_concurrent_home_matches: 1\n"
            "  albany:\n"
            "    max_concurrent_home_matches: 3\n"
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            spec.parameters.max_concurrent_home_matches["albany"],
            fmodel.MaxConcurrentHomeMatches(default=3),
        )
        # hackney wasn't given its own entry, so it inherits club_constraints.defaults
        self.assertEqual(
            spec.parameters.max_concurrent_home_matches["hackney"],
            fmodel.MaxConcurrentHomeMatches(default=1),
        )

    def test_max_concurrent_home_matches_default_and_overrides(self):
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    max_concurrent_home_matches:\n"
            "      default: 2\n"
            "      overrides:\n"
            "        2025-09-01: 3\n"
            "  hackney:\n"
            "    max_concurrent_home_matches: 1\n"
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            spec.parameters.max_concurrent_home_matches["albany"],
            fmodel.MaxConcurrentHomeMatches(default=2, overrides={date(2025, 9, 1): 3}),
        )

    def test_max_concurrent_home_matches_missing_default(self):
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    max_concurrent_home_matches:\n"
            "      overrides:\n"
            "        2025-09-01: 3\n"
            "  hackney:\n"
            "    max_concurrent_home_matches: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "default"):
            fixturespec.load_spec(path)

    def test_club_constraints_unknown_club(self):
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    max_concurrent_home_matches: 1\n"
            "  hackney:\n"
            "    max_concurrent_home_matches: 1\n"
            "  nonexistent:\n"
            "    max_concurrent_home_matches: 3\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "nonexistent"):
            fixturespec.load_spec(path)

    def test_max_concurrent_home_matches_missing_club_without_section_default(self):
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    max_concurrent_home_matches: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "hackney"):
            fixturespec.load_spec(path)

    def test_max_concurrent_home_matches_section_omitted_entirely(self):
        path = self._write(_BOILERPLATE)
        with self.assertRaisesRegex(fixturespec.SpecError, "albany.*hackney"):
            fixturespec.load_spec(path)

    def test_club_constraints_defaults_unsupported_field(self):
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    home_dates: [2025-09-01]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "not supported"):
            fixturespec.load_spec(path)

    def test_missing_clubs(self):
        path = self._write("teams: {}\ndivisions: {}\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "clubs"):
            fixturespec.load_spec(path)

    def test_club_missing_field(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue_name: Albany Sports Hall
    home_venue_address: 1 Albany Road, London
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
    home_venue_name: x
    home_venue_address: x
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
    home_venue_name: x
    home_venue_address: x
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
    home_venue_name: x
    home_venue_address: x
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
    home_venue_name: x
    home_venue_address: x
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
    home_venue_name: x
    home_venue_address: x
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
    home_venue_name: x
    home_venue_address: x
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
    home_venue_name: x
    home_venue_address: x
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
    home_venue_name: x
    home_venue_address: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  albany-1:
    club: albany
    index: 1
    division: 1
divisions:
  1: [albany-1]
club_constraints:
  albany:
    home_dates: ["not-a-date"]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "not-a-date"):
            fixturespec.load_spec(path)

    def test_duplicate_date_in_home_dates_rejected(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue_name: x
    home_venue_address: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  albany-1:
    club: albany
    index: 1
    division: 1
divisions:
  1: [albany-1]
club_constraints:
  albany:
    home_dates: [2025-09-01, 2025-09-01]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "duplicate date"):
            fixturespec.load_spec(path)

    def test_duplicate_date_in_unavailable_away_dates_rejected(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue_name: x
    home_venue_address: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  albany-1:
    club: albany
    index: 1
    division: 1
divisions:
  1: [albany-1]
club_constraints:
  albany:
    unavailable_away_dates: [2025-09-01, 2025-09-01]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "duplicate date"):
            fixturespec.load_spec(path)

    def test_unsupported_club_constraint_field(self):
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue_name: x
    home_venue_address: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  albany-1:
    club: albany
    index: 1
    division: 1
divisions:
  1: [albany-1]
club_constraints:
  albany:
    home_dates: [2025-09-01]
    teams: {}
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "not supported"):
            fixturespec.load_spec(path)

    def test_top_level_not_a_mapping(self):
        path = self._write("- just\n- a\n- list\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "mapping"):
            fixturespec.load_spec(path)

    def test_max_home_dates_used_per_club(self):
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    max_concurrent_home_matches: 1\n"
            "  albany:\n"
            "    max_home_dates_used: 1\n"
            "  hackney:\n"
            "    max_home_dates_used: 1\n"
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            spec.parameters.max_home_dates_used, {"albany": 1, "hackney": 1}
        )

    def test_max_home_dates_used_partial_clubs(self):
        """Only the clubs given their own entry are constrained."""
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    max_concurrent_home_matches: 1\n"
            "  albany:\n"
            "    max_home_dates_used: 1\n"
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.max_home_dates_used, {"albany": 1})

    def test_max_home_dates_used_absent(self):
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.max_home_dates_used, {})

    def test_max_home_dates_used_unknown_club(self):
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    max_concurrent_home_matches: 1\n"
            "  unknown-club:\n"
            "    max_home_dates_used: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "unknown-club"):
            fixturespec.load_spec(path)

    def test_max_home_dates_used_not_int(self):
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    max_concurrent_home_matches: 1\n"
            "  albany:\n"
            "    max_home_dates_used: not-an-int\n"
            "  hackney:\n"
            "    max_home_dates_used: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "integer"):
            fixturespec.load_spec(path)

    def test_fixed_fixture(self):
        path = self._write(
            _MINIMAL_SPEC + "fixed_fixtures:\n"
            "  - home: hackney-1\n"
            "    away: albany-1\n"
            "    date: 2025-09-15\n"
        )
        spec = fixturespec.load_spec(path)
        hackney_1 = next(t for t in spec.parameters.teams if t.club == "hackney")
        albany_1 = next(t for t in spec.parameters.teams if t.club == "albany")
        self.assertEqual(
            spec.parameters.fixed_fixtures,
            [
                fmodel.ScheduledFixture(
                    fixture=fmodel.Fixture(home_team=hackney_1, away_team=albany_1),
                    date=date(2025, 9, 15),
                )
            ],
        )

    def test_fixed_fixtures_absent(self):
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.fixed_fixtures, ())

    def test_fixed_fixtures_not_a_list(self):
        path = self._write(_MINIMAL_SPEC + "fixed_fixtures:\n  home: hackney-1\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "list"):
            fixturespec.load_spec(path)

    def test_fixed_fixtures_missing_field(self):
        path = self._write(
            _MINIMAL_SPEC + "fixed_fixtures:\n  - home: hackney-1\n    away: albany-1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "date"):
            fixturespec.load_spec(path)

    def test_fixed_fixtures_unsupported_field(self):
        path = self._write(
            _MINIMAL_SPEC + "fixed_fixtures:\n"
            "  - home: hackney-1\n"
            "    away: albany-1\n"
            "    date: 2025-09-15\n"
            "    venue: elsewhere\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "venue"):
            fixturespec.load_spec(path)

    def test_fixed_fixtures_unknown_team(self):
        path = self._write(
            _MINIMAL_SPEC + "fixed_fixtures:\n"
            "  - home: nonexistent\n"
            "    away: albany-1\n"
            "    date: 2025-09-15\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "nonexistent"):
            fixturespec.load_spec(path)

    def test_fixed_fixtures_home_equals_away(self):
        path = self._write(
            _MINIMAL_SPEC + "fixed_fixtures:\n"
            "  - home: hackney-1\n"
            "    away: hackney-1\n"
            "    date: 2025-09-15\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "hackney-1"):
            fixturespec.load_spec(path)

    def test_fixed_fixtures_different_divisions_rejected(self):
        path = self._write(
            _MINIMAL_SPEC.replace(
                "  hackney-1:\n    club: hackney\n    index: 1\n    division: 1",
                "  hackney-1:\n    club: hackney\n    index: 1\n    division: 2",
            ).replace(
                "divisions:\n  1: [albany-1, hackney-1]",
                "divisions:\n  1: [albany-1]\n  2: [hackney-1]",
            )
            + "fixed_fixtures:\n"
            "  - home: hackney-1\n"
            "    away: albany-1\n"
            "    date: 2025-09-15\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "not in the same division"):
            fixturespec.load_spec(path)

    def test_fixed_fixtures_date_not_a_home_date_rejected(self):
        path = self._write(
            _MINIMAL_SPEC + "fixed_fixtures:\n"
            "  - home: hackney-1\n"
            "    away: albany-1\n"
            "    date: 2025-09-16\n"  # not one of hackney's home dates
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "home dates"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_absent(self):
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.excluded_fixtures, ())

    def test_exclude_specific_fixture(self):
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n"
            "  fixtures:\n"
            "    - home: albany-1\n"
            "      away: albany-2\n"
        )
        spec = fixturespec.load_spec(path)
        albany1 = next(
            t for t in spec.parameters.teams if t.club == "albany" and t.index == 1
        )
        albany2 = next(
            t for t in spec.parameters.teams if t.club == "albany" and t.index == 2
        )
        self.assertEqual(
            list(spec.parameters.excluded_fixtures),
            [fmodel.Fixture(home_team=albany1, away_team=albany2)],
        )

    def test_exclude_team(self):
        """Excluding a team excludes all its fixtures, in both directions."""
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n  teams: [hackney-1]\n"
        )
        spec = fixturespec.load_spec(path)
        hackney1 = next(t for t in spec.parameters.teams if t.club == "hackney")
        others = [t for t in spec.parameters.teams if t.club != "hackney"]
        expected = set()
        for other in others:
            expected.add(fmodel.Fixture(home_team=hackney1, away_team=other))
            expected.add(fmodel.Fixture(home_team=other, away_team=hackney1))
        self.assertEqual(set(spec.parameters.excluded_fixtures), expected)

    def test_exclude_club(self):
        """Excluding a club excludes all of that club's teams' fixtures."""
        path = self._write(_THREE_TEAM_SPEC + "exclude_fixtures:\n  clubs: [hackney]\n")
        spec = fixturespec.load_spec(path)
        hackney1 = next(t for t in spec.parameters.teams if t.club == "hackney")
        others = [t for t in spec.parameters.teams if t.club != "hackney"]
        expected = set()
        for other in others:
            expected.add(fmodel.Fixture(home_team=hackney1, away_team=other))
            expected.add(fmodel.Fixture(home_team=other, away_team=hackney1))
        self.assertEqual(set(spec.parameters.excluded_fixtures), expected)

    def test_exclude_fixtures_unknown_club(self):
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n  clubs: [nonexistent]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "nonexistent"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_unknown_team(self):
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n  teams: [nonexistent]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "nonexistent"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_unsupported_key(self):
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n  players: [nonexistent]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "not supported"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_unknown_team_in_fixtures_list(self):
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n"
            "  fixtures:\n"
            "    - home: nonexistent\n"
            "      away: albany-2\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "nonexistent"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_home_equals_away(self):
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n"
            "  fixtures:\n"
            "    - home: albany-1\n"
            "      away: albany-1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "albany-1"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_different_divisions_rejected(self):
        path = self._write(
            _THREE_TEAM_SPEC.replace(
                "  hackney-1:\n    club: hackney\n    index: 1\n    division: 1",
                "  hackney-1:\n    club: hackney\n    index: 1\n    division: 2",
            ).replace(
                "divisions:\n  1: [albany-1, albany-2, hackney-1]",
                "divisions:\n  1: [albany-1, albany-2]\n  2: [hackney-1]",
            )
            + "exclude_fixtures:\n"
            "  fixtures:\n"
            "    - home: albany-1\n"
            "      away: hackney-1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "not in the same division"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_missing_field(self):
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n  fixtures:\n    - home: albany-1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "away"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_conflicts_with_fixed_fixtures(self):
        path = self._write(
            _THREE_TEAM_SPEC + "fixed_fixtures:\n"
            "  - home: albany-1\n"
            "    away: albany-2\n"
            "    date: 2025-09-01\n"
            "exclude_fixtures:\n"
            "  fixtures:\n"
            "    - home: albany-1\n"
            "      away: albany-2\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "also excluded"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_solves_end_to_end(self):
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n"
            "  fixtures:\n"
            "    - home: albany-1\n"
            "      away: albany-2\n"
        )
        spec = fixturespec.load_spec(path)
        fixtures = list(fmodel.solve(spec.parameters))
        albany1 = next(
            t for t in spec.parameters.teams if t.club == "albany" and t.index == 1
        )
        albany2 = next(
            t for t in spec.parameters.teams if t.club == "albany" and t.index == 2
        )
        self.assertEqual(len(fixtures), 5)  # 6 fixtures minus the excluded one
        self.assertFalse(
            any(
                sf.fixture.home_team == albany1 and sf.fixture.away_team == albany2
                for sf in fixtures
            )
        )

    def test_latest_internal_match_date_conflicts_with_fixed_fixtures(self):
        path = self._write(
            _THREE_TEAM_SPEC + "fixed_fixtures:\n"
            "  - home: albany-1\n"
            "    away: albany-2\n"
            "    date: 2025-12-01\n"
            "latest_internal_match_date: 2025-10-15\n"
        )
        with self.assertRaisesRegex(
            fixturespec.SpecError, "after latest_internal_match_date"
        ):
            fixturespec.load_spec(path)

    def test_latest_internal_match_date_ignores_cross_club_fixed_fixtures(self):
        """The cutoff only applies to fixtures between two teams of the same club."""
        path = self._write(
            _THREE_TEAM_SPEC + "fixed_fixtures:\n"
            "  - home: albany-1\n"
            "    away: hackney-1\n"
            "    date: 2025-12-01\n"
            "latest_internal_match_date: 2025-10-15\n"
        )
        spec = fixturespec.load_spec(path)  # must not raise
        self.assertEqual(spec.parameters.latest_internal_match_date, date(2025, 10, 15))

    def test_latest_internal_match_date_solves_end_to_end(self):
        path = self._write(
            _THREE_TEAM_SPEC + "latest_internal_match_date: 2025-10-15\n"
        )
        spec = fixturespec.load_spec(path)
        fixtures = list(fmodel.solve(spec.parameters))
        albany1 = next(
            t for t in spec.parameters.teams if t.club == "albany" and t.index == 1
        )
        albany2 = next(
            t for t in spec.parameters.teams if t.club == "albany" and t.index == 2
        )
        internal = [
            sf
            for sf in fixtures
            if {sf.fixture.home_team, sf.fixture.away_team} == {albany1, albany2}
        ]
        self.assertEqual(len(internal), 2)
        for sf in internal:
            self.assertLessEqual(sf.date, date(2025, 10, 15))

    def test_solves_end_to_end(self):
        """A loaded spec's Parameters should be usable directly with fmodel.solve()."""
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        fixtures = list(fmodel.solve(spec.parameters))
        self.assertEqual(len(fixtures), 2)  # Albany v Hackney and Hackney v Albany


if __name__ == "__main__":
    unittest.main()
