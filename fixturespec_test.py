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

import hashlib
import tempfile
import unittest
from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path

import fixturespec
import fmodel


def _find_limit(
    limits: Collection[fmodel.MatchLimit],
    *,
    club: str,
    venue_scope: fmodel.VenueScope,
    apply_per: fmodel.ApplyPer,
) -> fmodel.MatchLimit:
    """The one resolved MatchLimit for `club` with the given venue_scope /
    apply_per (fails the calling assertion if there isn't exactly one)."""
    matches = [
        limit
        for limit in limits
        if all(t.club == club for t in limit.teams)
        and limit.venue_scope is venue_scope
        and limit.apply_per is apply_per
    ]
    assert len(matches) == 1, f"expected exactly one, got {matches}"
    return matches[0]


def _cap_base(cap: fmodel.Cap | None) -> int | None:
    """`cap.base`, or None if there's no cap at all -- lets a test assert on the
    effective plain value ('matches.max'/'playing_teams.max', or their 'min'
    counterparts) without caring whether the whole Cap is absent or just its
    base."""
    return cap.base if cap is not None else None


def _cap_overrides(cap: fmodel.Cap | None) -> Mapping[date, int | None]:
    """`cap.overrides`, or {} if there's no cap at all."""
    return cap.overrides if cap is not None else {}


# Clubs/teams/divisions boilerplate, minus 'club_constraints', for tests that need to
# supply their own version of that section (concatenating a second copy on top of an
# existing one would create a duplicate top-level YAML key, which PyYAML resolves by
# silently letting the later one clobber the earlier one).
#
# Pins earliest_match_date well before every home_dates entry below, so tests that
# solve() end-to-end aren't affected by the default (today) cutoff excluding these
# fixed 2025 dates as time passes; tests of earliest_match_date itself override or
# strip this line instead of appending a second one (a duplicate top-level key, as
# above).
_BOILERPLATE = """
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
"""

# A valid spec with no concurrency limits at all (no 'club_constraints.defaults'
# and no per-club max_concurrent_matches), for tests that need to append their own
# version of that piece. Concurrency limits are entirely optional, so this is valid
# as-is too.
_MINIMAL_SPEC_NO_CONCURRENCY = (
    _BOILERPLATE
    + """
club_constraints:
  albany:
    home_dates: [2025-09-01, 2025-09-29]
  hackney:
    home_dates: [2025-09-15]
"""
)

# _MINIMAL_SPEC adds a spec-wide venue-capacity default and, on albany, a
# match_count_limits away blackout for 2025-12-25 (the dates albany's teams
# can't travel) -- the successor to the old club-level unavailable_away_dates.
_MINIMAL_SPEC = (
    _MINIMAL_SPEC_NO_CONCURRENCY.replace(
        "  albany:\n    home_dates: [2025-09-01, 2025-09-29]\n",
        "  albany:\n    home_dates: [2025-09-01, 2025-09-29]\n"
        "    match_count_limits:\n"
        "      - venue_scope: away\n"
        "        matches: {max: 0}\n"
        "        dates: [2025-12-25]\n",
    )
    + "  defaults:\n    match_count_limits:\n      - override_key: venue-capacity\n        venue_scope: home\n        matches:\n          max: 1\n"
)

# A three-team spec (two Albany teams plus Hackney) for exclude_fixtures tests, which
# need a division where excluding one team/club still leaves other fixtures behind.
# See _BOILERPLATE above for why earliest_match_date is pinned here too.
_THREE_TEAM_SPEC = """
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
  albany-2:
    club: albany
    index: 2
  hackney-1:
    club: hackney
    index: 1

divisions:
  1:
    scheme: double_round
    teams: [albany-1, albany-2, hackney-1]

club_constraints:
  albany:
    home_dates: [2025-09-01, 2025-10-01, 2025-11-01, 2025-12-01]
  hackney:
    home_dates: [2026-01-01, 2026-02-01]
"""


class TestLoadSpec(unittest.TestCase):
    """Test cases for load_spec()."""

    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.dir = Path(self._tmpdir.name)

    def _write(self, contents: str, name: str = "spec.yaml") -> Path:
        path = self.dir / name
        path.write_text(contents)
        return path

    def test_minimal_spec(self) -> None:
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
        # albany's "can't travel on 2025-12-25" is a match_count_limits away
        # blackout: a whole-club RangeLimit with match_max 0 over that single day.
        away_block = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.AWAY,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        assert isinstance(away_block, fmodel.RangeLimit)
        self.assertEqual(_cap_base(away_block.match_max), 0)
        self.assertEqual(
            away_block.ranges,
            (fmodel.DateRange(start=date(2025, 12, 25), end=date(2025, 12, 25)),),
        )
        # hackney has no away blackout at all.
        self.assertEqual(
            [
                limit
                for limit in spec.parameters.match_count_limits
                if all(t.club == "hackney" for t in limit.teams)
                and limit.venue_scope is fmodel.VenueScope.AWAY
            ],
            [],
        )
        # Neither club has its own match_count_limits, so both inherit
        # _MINIMAL_SPEC's club_constraints.defaults entry (home limit 1).
        for club in ("albany", "hackney"):
            limit = _find_limit(
                spec.parameters.match_count_limits,
                club=club,
                venue_scope=fmodel.VenueScope.HOME,
                apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
            )
            self.assertEqual(_cap_base(limit.match_max), 1)
        self.assertEqual(spec.name, "")
        self.assertFalse(spec.is_final)

    def test_run_name_and_is_final(self) -> None:
        path = self._write(_MINIMAL_SPEC + '\nname: "2025-26 Season"\nis_final: true\n')
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.name, "2025-26 Season")
        self.assertTrue(spec.is_final)

    def test_description(self) -> None:
        path = self._write(
            _MINIMAL_SPEC
            + '\ndescription: "Final schedule; refer to ECF LMS for authoritative dates."\n'
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            spec.description,
            "Final schedule; refer to ECF LMS for authoritative dates.",
        )

    def test_description_defaults_to_empty(self) -> None:
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.description, "")

    def test_is_final_must_be_a_boolean(self) -> None:
        path = self._write(_MINIMAL_SPEC + "\nis_final: notabool\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "is_final"):
            fixturespec.load_spec(path)

    def test_name_must_be_a_string(self) -> None:
        path = self._write(_MINIMAL_SPEC + "\nname: [1, 2]\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "name"):
            fixturespec.load_spec(path)

    def test_name_override(self) -> None:
        path = self._write(
            _MINIMAL_SPEC.replace(
                "  hackney-1:\n    club: hackney\n    index: 1",
                "  hackney-1:\n    club: hackney\n    index: 1\n"
                '    name_override: "Hackney Herons"',
            )
        )
        spec = fixturespec.load_spec(path)
        team = next(t for t in spec.parameters.teams if t.club == "hackney")
        self.assertEqual(team.name_override, "Hackney Herons")

    def test_match_count_limits_empty_when_none_configured(self) -> None:
        path = self._write(_MINIMAL_SPEC_NO_CONCURRENCY)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.match_count_limits, ())

    def test_unknown_top_level_key_rejected(self) -> None:
        path = self._write(_MINIMAL_SPEC + "\nmin_gap_days: 10\n")
        with self.assertRaisesRegex(
            fixturespec.SpecError,
            r"top-level key\(s\) \['min_gap_days'\] not supported",
        ):
            fixturespec.load_spec(path)

    def test_spec_checksum_helper_is_self_describing_sha256(self) -> None:
        path = self._write("name: Test\n")
        self.assertEqual(
            fixturespec.spec_checksum(path),
            "sha256:" + hashlib.sha256(b"name: Test\n").hexdigest(),
        )

    def test_load_spec_records_matching_spec_checksum_on_parameters(self) -> None:
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.spec_checksum, fixturespec.spec_checksum(path))
        self.assertTrue(spec.parameters.spec_checksum.startswith("sha256:"))

    def test_latest_internal_match_date_absent(self) -> None:
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertIsNone(spec.parameters.latest_internal_match_date)

    def test_latest_internal_match_date_parsed(self) -> None:
        path = self._write(_MINIMAL_SPEC + "latest_internal_match_date: 2025-12-31\n")
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.latest_internal_match_date, date(2025, 12, 31))

    def test_latest_internal_match_date_invalid(self) -> None:
        path = self._write(_MINIMAL_SPEC + "latest_internal_match_date: not-a-date\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "not-a-date"):
            fixturespec.load_spec(path)

    def test_earliest_match_date_defaults_to_today(self) -> None:
        path = self._write(
            _MINIMAL_SPEC.replace("earliest_match_date: 2025-01-01\n", "")
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.earliest_match_date, date.today())

    def test_earliest_match_date_parsed(self) -> None:
        path = self._write(
            _MINIMAL_SPEC.replace(
                "earliest_match_date: 2025-01-01", "earliest_match_date: 2030-01-01"
            )
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.earliest_match_date, date(2030, 1, 1))

    def test_earliest_match_date_invalid(self) -> None:
        path = self._write(
            _MINIMAL_SPEC.replace(
                "earliest_match_date: 2025-01-01", "earliest_match_date: not-a-date"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "not-a-date"):
            fixturespec.load_spec(path)

    def test_earliest_match_date_in_the_past_logs_a_warning(self) -> None:
        path = self._write(
            _MINIMAL_SPEC.replace(
                "earliest_match_date: 2025-01-01", "earliest_match_date: 2020-01-01"
            )
        )
        with self.assertLogs(fixturespec.logger, level="WARNING") as cm:
            fixturespec.load_spec(path)
        self.assertIn("2020-01-01", cm.output[0])
        self.assertIn("in the past", cm.output[0])

    def test_earliest_match_date_today_does_not_log_a_warning(self) -> None:
        path = self._write(
            _MINIMAL_SPEC.replace(
                "earliest_match_date: 2025-01-01",
                f"earliest_match_date: {date.today().isoformat()}",
            )
        )
        with self.assertNoLogs(fixturespec.logger, level="WARNING"):
            fixturespec.load_spec(path)

    def test_avoid_dates_key_no_longer_recognised(self) -> None:
        # avoid_dates was removed in favour of a defaults matches: {max: 0}
        # date_ranges entry; the old top-level key is now an unknown field.
        path = self._write(_MINIMAL_SPEC + "avoid_dates: [2025-12-25]\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "avoid_dates"):
            fixturespec.load_spec(path)

    def test_no_play_dates_default_resolves_per_club(self) -> None:
        # A whole-club matches: {max: 0} date_ranges defaults entry (the
        # avoid_dates replacement): every club gets a resolved limit over all
        # its teams.
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: no-play-dates\n"
            "        matches:\n          max: 0\n"
            "        date_ranges:\n"
            "          - start_date: 2025-12-22\n"
            "            end_date: 2026-01-04\n"
            "  albany:\n"
            "    home_dates: [2025-12-15]\n"
            "  hackney:\n"
            "    home_dates: [2025-12-08]\n"
        )
        spec = fixturespec.load_spec(path)
        by_club: dict[str, fmodel.RangeLimit] = {}
        for limit in spec.parameters.match_count_limits:
            if isinstance(limit, fmodel.RangeLimit) and _cap_base(limit.match_max) == 0:
                (club,) = {t.club for t in limit.teams}
                by_club[club] = limit
        self.assertEqual(set(by_club), {"albany", "hackney"})
        self.assertEqual(
            by_club["albany"].ranges,
            (fmodel.DateRange(date(2025, 12, 22), date(2026, 1, 4)),),
        )

    def test_no_play_dates_default_can_be_overridden_by_a_club(self) -> None:
        # A club naming the override_key replaces the block wholesale -- here
        # cancelling it, so that club has no matches: {max: 0} limit.
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: no-play-dates\n"
            "        matches:\n          max: 0\n"
            "        date_ranges:\n"
            "          - start_date: 2025-12-22\n"
            "            end_date: 2026-01-04\n"
            "  albany:\n"
            "    home_dates: [2025-12-15]\n"
            "    match_count_limits:\n"
            "      - override_key: no-play-dates\n"
            "        matches:\n          max: null\n"
            "  hackney:\n"
            "    home_dates: [2025-12-08]\n"
        )
        spec = fixturespec.load_spec(path)
        zero_clubs = {
            t.club
            for limit in spec.parameters.match_count_limits
            if isinstance(limit, fmodel.RangeLimit) and _cap_base(limit.match_max) == 0
            for t in limit.teams
        }
        self.assertEqual(zero_clubs, {"hackney"})

    def test_duplicate_top_level_key_rejected(self) -> None:
        path = self._write(_MINIMAL_SPEC + '\nname: "one"\nname: "two"\n')
        with self.assertRaisesRegex(fixturespec.SpecError, "duplicate key"):
            fixturespec.load_spec(path)

    def test_duplicate_nested_key_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n        venue_scope: home\n        matches:\n          max: 1\n"
            "  albany:\n"
            "    home_dates: [2025-09-01]\n"
            "  albany:\n"
            "    home_dates: [2025-09-08]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "duplicate key"):
            fixturespec.load_spec(path)

    # These tests write standalone specs rather than extending
    # _MINIMAL_SPEC_NO_CONCURRENCY, since appending a second 'club_constraints'
    # section (or a second entry for a club already present) would create a
    # duplicate YAML key, which PyYAML resolves by silently letting the later one
    # clobber the earlier one.

    def test_match_count_limits_override_key_replaces_default(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n"
            "        venue_scope: home\n"
            "        matches:\n          max: 1\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n"
            "        venue_scope: home\n"
            "        matches:\n          max: 3\n"
        )
        spec = fixturespec.load_spec(path)
        albany = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertEqual(_cap_base(albany.match_max), 3)
        # hackney has no entry of its own, so it keeps the default home cap 1.
        hackney = _find_limit(
            spec.parameters.match_count_limits,
            club="hackney",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertEqual(_cap_base(hackney.match_max), 1)

    def test_match_count_limits_matches_max_overrides_parsed(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: 2\n"
            "          max_overrides:\n"
            "            2025-09-01: 3\n"
        )
        spec = fixturespec.load_spec(path)
        albany = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertEqual(_cap_base(albany.match_max), 2)
        self.assertEqual(_cap_overrides(albany.match_max), {date(2025, 9, 1): 3})

    def test_match_count_limits_matches_max_overrides_allow_zero(self) -> None:
        """A 'matches.max_overrides' entry of 0 is meaningful on its own terms --
        the single-day equivalent of a 'date_ranges' blackout -- unlike a plain
        'matches.max' of 0, which needs 'date_ranges' to mean anything."""
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: 2\n"
            "          max_overrides:\n"
            "            2025-09-01: 0\n"
        )
        spec = fixturespec.load_spec(path)
        albany = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertEqual(_cap_overrides(albany.match_max), {date(2025, 9, 1): 0})

    def test_match_count_limits_matches_max_overrides_reject_negative(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: 2\n"
            "          max_overrides:\n"
            "            2025-09-01: -1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "max_overrides"):
            fixturespec.load_spec(path)

    def test_match_count_limits_override_null_lifts_the_cap_that_day(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: null\n"
            "          max_overrides:\n"
            "            2025-09-01: 3\n"
        )
        spec = fixturespec.load_spec(path)
        albany = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertIsNone(_cap_base(albany.match_max))
        self.assertEqual(_cap_overrides(albany.match_max), {date(2025, 9, 1): 3})

    def test_match_count_limits_null_max_cancels_default_via_override_key(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n"
            "        venue_scope: home\n"
            "        matches:\n          max: 1\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n"
            "        venue_scope: home\n"
            "        matches:\n          max: null\n"
        )
        spec = fixturespec.load_spec(path)
        albany_home = [
            limit
            for limit in spec.parameters.match_count_limits
            if all(t.club == "albany" for t in limit.teams)
            and limit.venue_scope is fmodel.VenueScope.HOME
        ]
        self.assertEqual(albany_home, [])
        # hackney still gets the default.
        hackney = _find_limit(
            spec.parameters.match_count_limits,
            club="hackney",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertEqual(_cap_base(hackney.match_max), 1)

    def test_match_count_limits_unknown_override_key_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n"
            "        venue_scope: home\n"
            "        matches:\n          max: 1\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - override_key: typo\n"
            "        venue_scope: home\n"
            "        matches:\n          max: 2\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "override_key"):
            fixturespec.load_spec(path)

    def test_match_count_limits_defaults_require_override_key(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "override_key"):
            fixturespec.load_spec(path)

    def test_match_count_limits_defaults_duplicate_override_key_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: cap\n"
            "        venue_scope: home\n"
            "        matches:\n          max: 1\n"
            "      - override_key: cap\n"
            "        venue_scope: away\n"
            "        matches:\n          max: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "override_key"):
            fixturespec.load_spec(path)

    def test_match_count_limits_override_key_with_teams_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: cap\n"
            "        venue_scope: home\n"
            "        matches:\n          max: 1\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - override_key: cap\n"
            "        teams: [albany-1]\n"
            "        matches:\n          max: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "override_key"):
            fixturespec.load_spec(path)

    def test_match_count_limits_matches_max_overrides_require_one_day_window(
        self,
    ) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: 2\n"
            "          max_overrides:\n"
            "            2025-09-01: 3\n"
            "        time_window_days: 7\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "max_overrides"):
            fixturespec.load_spec(path)

    def test_match_count_limits_missing_max_field_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "matches.*playing_teams"):
            fixturespec.load_spec(path)

    def test_match_count_limits_defaults_additive_when_no_override_key(self) -> None:
        # A club entry without an override_key is additive, even if a default
        # covers the same venue_scope.
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n"
            "        venue_scope: home\n"
            "        matches:\n          max: 2\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: all\n"
            "        matches:\n          max: 1\n"
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            _cap_base(
                _find_limit(
                    spec.parameters.match_count_limits,
                    club="albany",
                    venue_scope=fmodel.VenueScope.HOME,
                    apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
                ).match_max
            ),
            2,
        )
        self.assertEqual(
            _cap_base(
                _find_limit(
                    spec.parameters.match_count_limits,
                    club="albany",
                    venue_scope=fmodel.VenueScope.ALL,
                    apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
                ).match_max
            ),
            1,
        )

    def test_match_count_limits_defaults_reject_teams_field(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: k\n"
            "        teams: [albany-1]\n"
            "        matches:\n          max: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "teams is not allowed"):
            fixturespec.load_spec(path)

    def test_match_count_limits_apply_per_each_team_parsed(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - apply_per: each_team\n"
            "        time_window_days: 7\n"
            "        matches:\n          max: 1\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.ALL,
            apply_per=fmodel.ApplyPer.EACH_TEAM,
        )
        assert isinstance(limit, fmodel.RollingLimit)
        self.assertEqual((limit.window_days, _cap_base(limit.match_max)), (7, 1))

    def test_match_count_limits_unknown_venue_scope_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: sideways\n"
            "        matches:\n          max: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "venue_scope"):
            fixturespec.load_spec(path)

    def test_match_count_limits_unknown_apply_per_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - apply_per: sometimes\n"
            "        matches:\n          max: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "apply_per"):
            fixturespec.load_spec(path)

    def test_club_constraints_unknown_club(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    home_dates: [2025-09-01]\n"
            "  nonexistent:\n"
            "    home_dates: [2025-09-01]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "nonexistent"):
            fixturespec.load_spec(path)

    def test_match_count_limits_absent_for_a_club_is_allowed(self) -> None:
        # No defaults, and only albany has an entry: hackney simply has no limits.
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: 1\n"
        )
        spec = fixturespec.load_spec(path)
        clubs = {
            t.club for limit in spec.parameters.match_count_limits for t in limit.teams
        }
        self.assertEqual(clubs, {"albany"})

    def test_match_count_limits_date_ranges_parsed(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - matches:\n          max: 1\n"
            "        date_ranges:\n"
            "          - start_date: 2025-10-27\n"
            "            end_date: 2025-11-02\n"
            "          - start_date: 2026-02-16\n"
            "            end_date: 2026-02-22\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.ALL,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        assert isinstance(limit, fmodel.RangeLimit)
        self.assertEqual(
            limit.ranges,
            (
                fmodel.DateRange(date(2025, 10, 27), date(2025, 11, 2)),
                fmodel.DateRange(date(2026, 2, 16), date(2026, 2, 22)),
            ),
        )
        self.assertEqual(_cap_base(limit.match_max), 1)

    def test_match_count_limits_date_ranges_allow_max_zero(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - matches:\n          max: 0\n"
            "        date_ranges:\n"
            "          - start_date: 2025-10-27\n"
            "            end_date: 2025-11-02\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.ALL,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertEqual(_cap_base(limit.match_max), 0)

    def test_match_count_limits_max_zero_rejected_without_date_ranges(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: 0\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "must be >= 1"):
            fixturespec.load_spec(path)

    def test_match_count_limits_date_ranges_reject_time_window_days(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - matches:\n          max: 1\n"
            "        time_window_days: 7\n"
            "        date_ranges:\n"
            "          - start_date: 2025-10-27\n"
            "            end_date: 2025-11-02\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "time_window_days"):
            fixturespec.load_spec(path)

    def test_match_count_limits_date_ranges_reject_max_overrides(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - matches:\n"
            "          max: 1\n"
            "          max_overrides:\n"
            "            2025-10-27: 1\n"
            "        date_ranges:\n"
            "          - start_date: 2025-10-27\n"
            "            end_date: 2025-11-02\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "max_overrides"):
            fixturespec.load_spec(path)

    def test_match_count_limits_date_ranges_reject_override_key(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n"
            "        venue_scope: home\n"
            "        matches:\n          max: 1\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n"
            "        matches:\n          max: 1\n"
            "        date_ranges:\n"
            "          - start_date: 2025-10-27\n"
            "            end_date: 2025-11-02\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "date_ranges"):
            fixturespec.load_spec(path)

    def test_match_count_limits_date_ranges_allowed_under_defaults(self) -> None:
        # A defaults entry may carry date_ranges; its own override_key is the
        # entry's identity, not a replace-target, so the two coexist.
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: k\n"
            "        matches:\n          max: 1\n"
            "        date_ranges:\n"
            "          - start_date: 2025-10-27\n"
            "            end_date: 2025-11-02\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.ALL,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        assert isinstance(limit, fmodel.RangeLimit)
        self.assertEqual(
            limit.ranges,
            (fmodel.DateRange(date(2025, 10, 27), date(2025, 11, 2)),),
        )

    def test_match_count_limits_date_ranges_reject_null_max(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - matches:\n          max: null\n"
            "        date_ranges:\n"
            "          - start_date: 2025-10-27\n"
            "            end_date: 2025-11-02\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "date_ranges needs"):
            fixturespec.load_spec(path)

    def test_match_count_limits_date_ranges_reject_start_after_end(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - matches:\n          max: 1\n"
            "        date_ranges:\n"
            "          - start_date: 2025-11-02\n"
            "            end_date: 2025-10-27\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "is after end"):
            fixturespec.load_spec(path)

    def test_match_count_limits_date_ranges_reject_missing_both_dates(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - matches:\n          max: 1\n"
            "        date_ranges:\n"
            "          - {}\n"
        )
        with self.assertRaisesRegex(
            fixturespec.SpecError, "start_date.*and/or.*end_date"
        ):
            fixturespec.load_spec(path)

    def test_match_count_limits_date_ranges_open_ended_start(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - matches:\n          max: 1\n"
            "        date_ranges:\n"
            "          - start_date: 2025-10-27\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.ALL,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        assert isinstance(limit, fmodel.RangeLimit)
        self.assertEqual(limit.ranges, (fmodel.DateRange(start=date(2025, 10, 27)),))

    def test_match_count_limits_date_ranges_open_ended_end(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - matches:\n          max: 1\n"
            "        date_ranges:\n"
            "          - end_date: 2025-11-02\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.ALL,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        assert isinstance(limit, fmodel.RangeLimit)
        self.assertEqual(limit.ranges, (fmodel.DateRange(end=date(2025, 11, 2)),))

    def test_match_count_limits_date_ranges_reject_unknown_key(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - matches:\n          max: 1\n"
            "        date_ranges:\n"
            "          - start_date: 2025-10-27\n"
            "            end_date: 2025-11-02\n"
            "            note: half term\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "not supported"):
            fixturespec.load_spec(path)

    def test_match_count_limits_date_ranges_reject_empty_list(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - matches:\n          max: 1\n"
            "        date_ranges: []\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "non-empty list"):
            fixturespec.load_spec(path)

    def test_match_count_limits_omitted_everywhere_is_allowed(self) -> None:
        # No club_constraints.defaults and no per-club match_count_limits: limits
        # are entirely optional.
        path = self._write(_MINIMAL_SPEC_NO_CONCURRENCY)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.match_count_limits, ())

    def test_club_constraints_defaults_unsupported_field(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    home_dates: [2025-09-01]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "not supported"):
            fixturespec.load_spec(path)

    def test_missing_clubs(self) -> None:
        path = self._write("teams: {}\ndivisions: {}\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "clubs"):
            fixturespec.load_spec(path)

    def test_club_missing_field(self) -> None:
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

    def test_missing_teams(self) -> None:
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

    def test_team_references_unknown_club(self) -> None:
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
divisions:
  1:
    scheme: double_round
    teams: [hackney-1]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "hackney"):
            fixturespec.load_spec(path)

    def test_duplicate_club_index(self) -> None:
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
  albany-1-again:
    club: albany
    index: 1
divisions:
  1:
    scheme: double_round
    teams: [albany-1, albany-1-again]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "index 1"):
            fixturespec.load_spec(path)

    def test_missing_divisions_section(self) -> None:
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
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "divisions"):
            fixturespec.load_spec(path)

    def test_team_missing_from_divisions(self) -> None:
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
  albany-2:
    club: albany
    index: 2
divisions:
  1:
    scheme: double_round
    teams: [albany-1]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "albany-2"):
            fixturespec.load_spec(path)

    def test_division_key_not_an_integer(self) -> None:
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
divisions:
  "one":
    scheme: double_round
    teams: [albany-1]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "integer"):
            fixturespec.load_spec(path)

    def test_team_listed_in_two_divisions(self) -> None:
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
divisions:
  1:
    scheme: double_round
    teams: [albany-1]
  2:
    scheme: double_round
    teams: [albany-1]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "more than one division"):
            fixturespec.load_spec(path)

    _SCHEME_SPEC_HEAD = """
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
  albany-2:
    club: albany
    index: 2
divisions:
"""

    # Appended after a divisions block to make _SCHEME_SPEC_HEAD specs valid enough
    # to load successfully (the failure-path tests don't get this far).
    _SCHEME_SPEC_TAIL = (
        "club_constraints:\n"
        "  defaults:\n    match_count_limits:\n      - override_key: venue-capacity\n        venue_scope: home\n        matches:\n          max: 1\n"
        "  albany:\n    home_dates: [2025-09-01, 2025-09-08, 2025-09-15]\n"
    )

    def test_division_scheme_single_round_is_parsed(self) -> None:
        path = self._write(
            self._SCHEME_SPEC_HEAD + "  1:\n    scheme: single_round\n"
            "    teams: [albany-1, albany-2]\n" + self._SCHEME_SPEC_TAIL
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            {1: fmodel.FixtureScheme.SINGLE_ROUND},
            {d.number: d.scheme for d in spec.parameters.divisions},
        )

    def test_division_scheme_double_round_is_parsed(self) -> None:
        path = self._write(
            self._SCHEME_SPEC_HEAD + "  1:\n    scheme: double_round\n"
            "    teams: [albany-1, albany-2]\n" + self._SCHEME_SPEC_TAIL
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            {1: fmodel.FixtureScheme.DOUBLE_ROUND},
            {d.number: d.scheme for d in spec.parameters.divisions},
        )

    def test_division_missing_scheme_rejected(self) -> None:
        path = self._write(
            self._SCHEME_SPEC_HEAD + "  1:\n    teams: [albany-1, albany-2]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "scheme"):
            fixturespec.load_spec(path)

    def test_division_missing_teams_rejected(self) -> None:
        path = self._write(self._SCHEME_SPEC_HEAD + "  1:\n    scheme: double_round\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "teams"):
            fixturespec.load_spec(path)

    def test_division_unknown_scheme_rejected(self) -> None:
        path = self._write(
            self._SCHEME_SPEC_HEAD + "  1:\n    scheme: triple_round\n"
            "    teams: [albany-1, albany-2]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "scheme.*triple_round"):
            fixturespec.load_spec(path)

    def test_division_bare_list_rejected(self) -> None:
        path = self._write(self._SCHEME_SPEC_HEAD + "  1: [albany-1, albany-2]\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "mapping"):
            fixturespec.load_spec(path)

    def test_division_unsupported_field_rejected(self) -> None:
        path = self._write(
            self._SCHEME_SPEC_HEAD + "  1:\n    scheme: double_round\n"
            "    teams: [albany-1, albany-2]\n    berger_seed: 3\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "berger_seed"):
            fixturespec.load_spec(path)

    def test_single_round_division_team_order_follows_the_divisions_list(self) -> None:
        # teams: lists albany-2 before albany-1; the divisions list is the Berger
        # draw order and must win, so parameters.teams reflects the divisions list.
        path = self._write("""
clubs:
  albany:
    name: Albany
    home_venue_name: x
    home_venue_address: x
    home_start_time: "19:30"
    home_time_limit: "75+15"
teams:
  albany-2:
    club: albany
    index: 2
  albany-1:
    club: albany
    index: 1
  albany-3:
    club: albany
    index: 3
divisions:
  1:
    scheme: single_round
    teams: [albany-1, albany-2, albany-3]
club_constraints:
  defaults:
    match_count_limits:
      - override_key: venue-capacity
        venue_scope: home
        matches:
          max: 1
  albany:
    home_dates: [2025-09-01, 2025-09-08, 2025-09-15]
""")
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            [(t.club, t.index) for t in spec.parameters.teams],
            [("albany", 1), ("albany", 2), ("albany", 3)],
        )

    def test_invalid_date_string(self) -> None:
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
divisions:
  1:
    scheme: double_round
    teams: [albany-1]
club_constraints:
  albany:
    home_dates: ["not-a-date"]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "not-a-date"):
            fixturespec.load_spec(path)

    def test_duplicate_date_in_home_dates_rejected(self) -> None:
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
divisions:
  1:
    scheme: double_round
    teams: [albany-1]
club_constraints:
  albany:
    home_dates: [2025-09-01, 2025-09-01]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "duplicate date"):
            fixturespec.load_spec(path)

    def test_duplicate_date_in_match_count_limits_dates_rejected(self) -> None:
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
divisions:
  1:
    scheme: double_round
    teams: [albany-1]
club_constraints:
  albany:
    match_count_limits:
      - venue_scope: away
        matches: {max: 0}
        dates: [2025-09-01, 2025-09-01]
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "duplicate date"):
            fixturespec.load_spec(path)

    def test_unsupported_club_constraint_field(self) -> None:
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
divisions:
  1:
    scheme: double_round
    teams: [albany-1]
club_constraints:
  albany:
    home_dates: [2025-09-01]
    venue: {}
""")
        with self.assertRaisesRegex(fixturespec.SpecError, "not supported"):
            fixturespec.load_spec(path)

    def test_top_level_not_a_mapping(self) -> None:
        path = self._write("- just\n- a\n- list\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "mapping"):
            fixturespec.load_spec(path)

    def test_home_dates_used_per_club(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n        venue_scope: home\n        matches:\n          max: 1\n"
            "  albany:\n"
            "    home_dates_used:\n"
            "      max: 1\n"
            "  hackney:\n"
            "    home_dates_used:\n"
            "      min: 2\n"
            "      max: 4\n"
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            spec.parameters.home_dates_used,
            {
                "albany": fmodel.HomeDatesUsedBounds(maximum=1),
                "hackney": fmodel.HomeDatesUsedBounds(minimum=2, maximum=4),
            },
        )

    def test_home_dates_used_partial_clubs(self) -> None:
        """Only the clubs given their own entry are constrained."""
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n        venue_scope: home\n        matches:\n          max: 1\n"
            "  albany:\n"
            "    home_dates_used:\n"
            "      min: 3\n"
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            spec.parameters.home_dates_used,
            {"albany": fmodel.HomeDatesUsedBounds(minimum=3)},
        )

    def test_home_dates_used_absent(self) -> None:
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.home_dates_used, {})

    def test_home_dates_used_unknown_club(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n        venue_scope: home\n        matches:\n          max: 1\n"
            "  unknown-club:\n"
            "    home_dates_used:\n"
            "      max: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "unknown-club"):
            fixturespec.load_spec(path)

    def test_home_dates_used_not_int(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n        venue_scope: home\n        matches:\n          max: 1\n"
            "  albany:\n"
            "    home_dates_used:\n"
            "      max: not-an-int\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "integer"):
            fixturespec.load_spec(path)

    def test_home_dates_used_not_a_mapping(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n        venue_scope: home\n        matches:\n          max: 1\n"
            "  albany:\n"
            "    home_dates_used: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "mapping"):
            fixturespec.load_spec(path)

    def test_home_dates_used_empty_mapping(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n        venue_scope: home\n        matches:\n          max: 1\n"
            "  albany:\n"
            "    home_dates_used: {}\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "min.*max"):
            fixturespec.load_spec(path)

    def test_home_dates_used_unsupported_key(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n        venue_scope: home\n        matches:\n          max: 1\n"
            "  albany:\n"
            "    home_dates_used:\n"
            "      minimum: 2\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "minimum"):
            fixturespec.load_spec(path)

    def test_home_dates_used_below_one(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n        venue_scope: home\n        matches:\n          max: 1\n"
            "  albany:\n"
            "    home_dates_used:\n"
            "      min: 0\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "at least 1"):
            fixturespec.load_spec(path)

    def test_home_dates_used_min_exceeds_max(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: venue-capacity\n        venue_scope: home\n        matches:\n          max: 1\n"
            "  albany:\n"
            "    home_dates_used:\n"
            "      min: 5\n"
            "      max: 3\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "exceeds"):
            fixturespec.load_spec(path)

    def test_per_club_gap_via_override_key(self) -> None:
        """A per-club weekly gap is an apply_per: each_team match_count_limits entry
        with override_key: weekly-gap; it replaces the spec-wide default for that
        club only."""
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  defaults:\n"
            "    match_count_limits:\n"
            "      - override_key: weekly-gap\n"
            "        apply_per: each_team\n"
            "        time_window_days: 7\n"
            "        matches:\n          max: 1\n"
            "  albany:\n"
            "    home_dates: [2025-09-01]\n"
            "    match_count_limits:\n"
            "      - override_key: weekly-gap\n"
            "        apply_per: each_team\n"
            "        time_window_days: 14\n"
            "        matches:\n          max: 1\n"
            "  hackney:\n"
            "    home_dates: [2025-09-15]\n"
        )
        spec = fixturespec.load_spec(path)
        albany_gap = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.ALL,
            apply_per=fmodel.ApplyPer.EACH_TEAM,
        )
        hackney_gap = _find_limit(
            spec.parameters.match_count_limits,
            club="hackney",
            venue_scope=fmodel.VenueScope.ALL,
            apply_per=fmodel.ApplyPer.EACH_TEAM,
        )
        assert isinstance(albany_gap, fmodel.RollingLimit)
        assert isinstance(hackney_gap, fmodel.RollingLimit)
        self.assertEqual(albany_gap.window_days, 14)
        self.assertEqual(hackney_gap.window_days, 7)

    def test_match_count_limits_negative_max_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: -1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "max"):
            fixturespec.load_spec(path)

    def test_match_count_limits_null_max_without_max_overrides_rejected(
        self,
    ) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: null\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "matches.*playing_teams"):
            fixturespec.load_spec(path)

    def test_match_count_limits_playing_teams_max_parsed(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: 3\n"
            "        playing_teams:\n          max: 3\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertEqual(_cap_base(limit.match_max), 3)
        self.assertEqual(_cap_base(limit.playing_teams_max), 3)

    def test_match_count_limits_playing_teams_max_defaults_to_none(self) -> None:
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertIsNone(_cap_base(limit.playing_teams_max))

    def test_match_count_limits_playing_teams_max_negative_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: 1\n"
            "        playing_teams:\n          max: -1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "playing_teams.max"):
            fixturespec.load_spec(path)

    def test_match_count_limits_playing_teams_max_allows_multi_day_window(
        self,
    ) -> None:
        """Unlike playing_teams.max_overrides (still tied to a single date), a
        plain playing_teams.max cap is meaningful over a rolling multi-day window
        too -- see fmodel.MatchCountLimit for what it counts there."""
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: 3\n"
            "        playing_teams:\n          max: 1\n"
            "        time_window_days: 7\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        assert isinstance(limit, fmodel.RollingLimit)
        self.assertEqual(_cap_base(limit.playing_teams_max), 1)
        self.assertEqual(limit.window_days, 7)

    def test_match_count_limits_playing_teams_max_allows_date_ranges(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: 0\n"
            "        playing_teams:\n          max: 0\n"
            "        date_ranges:\n"
            "          - start_date: 2025-09-01\n"
            "            end_date: 2025-09-07\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        assert isinstance(limit, fmodel.RangeLimit)
        self.assertEqual(_cap_base(limit.playing_teams_max), 0)
        self.assertEqual(
            limit.ranges,
            (fmodel.DateRange(date(2025, 9, 1), date(2025, 9, 7)),),
        )

    def test_match_count_limits_playing_teams_max_alone_is_allowed(self) -> None:
        """'playing_teams' can carry a rule on its own, with 'matches' omitted
        entirely (no plain match-count cap, just a distinct-teams-playing one)."""
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        playing_teams:\n          max: 2\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertIsNone(_cap_base(limit.match_max))
        self.assertEqual(_cap_base(limit.playing_teams_max), 2)

    def test_match_count_limits_playing_teams_max_alone_with_explicit_null(
        self,
    ) -> None:
        """An explicit 'matches: {max: null}' alongside 'playing_teams' works the
        same as omitting 'matches' entirely."""
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: null\n"
            "        playing_teams:\n          max: 2\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertIsNone(_cap_base(limit.match_max))
        self.assertEqual(_cap_base(limit.playing_teams_max), 2)

    def test_match_count_limits_matches_alone_is_allowed(self) -> None:
        """'matches' can carry a rule on its own, with 'playing_teams'
        omitted entirely."""
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: 3\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertEqual(_cap_base(limit.match_max), 3)
        self.assertIsNone(_cap_base(limit.playing_teams_max))

    def test_match_count_limits_neither_max_field_rejected(self) -> None:
        """Neither 'matches' nor 'playing_teams', and no other way to carry a
        bound (no override_key): rejected."""
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "matches.*playing_teams"):
            fixturespec.load_spec(path)

    def test_match_count_limits_playing_teams_max_overrides_parsed(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: 3\n"
            "        playing_teams:\n"
            "          max: 1\n"
            "          max_overrides:\n"
            "            2025-09-01: 2\n"
            "            2025-09-08: null\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertEqual(_cap_base(limit.playing_teams_max), 1)
        self.assertEqual(
            _cap_overrides(limit.playing_teams_max),
            {date(2025, 9, 1): 2, date(2025, 9, 8): None},
        )

    def test_match_count_limits_playing_teams_max_overrides_require_one_day_window(
        self,
    ) -> None:
        # playing_teams.max itself is omitted here (it would trigger its own,
        # broader time_window_days==1 requirement first -- see
        # test_match_count_limits_playing_teams_max_requires_one_day_window):
        # max_overrides needs the same restriction independently, since it's
        # usable on its own (a rule with only override dates, no everyday cap).
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: 3\n"
            "        time_window_days: 7\n"
            "        playing_teams:\n"
            "          max_overrides:\n"
            "            2025-09-01: 2\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "max_overrides"):
            fixturespec.load_spec(path)

    def test_match_count_limits_playing_teams_max_overrides_reject_negative(
        self,
    ) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: 3\n"
            "        playing_teams:\n"
            "          max: 1\n"
            "          max_overrides:\n"
            "            2025-09-01: -1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "max_overrides"):
            fixturespec.load_spec(path)

    def test_match_count_limits_playing_teams_max_overrides_reject_date_ranges(
        self,
    ) -> None:
        # playing_teams.max itself is omitted -- see the comment on
        # test_match_count_limits_playing_teams_max_overrides_require_one_day_window
        # above.
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: 0\n"
            "        playing_teams:\n"
            "          max_overrides:\n"
            "            2025-09-01: 1\n"
            "        date_ranges:\n"
            "          - start_date: 2025-09-01\n"
            "            end_date: 2025-09-07\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "max_overrides"):
            fixturespec.load_spec(path)

    def test_match_count_limits_matches_min_parsed(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: 3\n"
            "          min: 1\n"
            "          min_overrides:\n"
            "            2025-09-01: 2\n"
            "            2025-09-08: null\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertEqual(_cap_base(limit.match_min), 1)
        self.assertEqual(
            _cap_overrides(limit.match_min),
            {date(2025, 9, 1): 2, date(2025, 9, 8): None},
        )

    def test_match_count_limits_playing_teams_min_parsed(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        playing_teams:\n"
            "          max: 3\n"
            "          min: 1\n"
            "          min_overrides:\n"
            "            2025-09-01: 2\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertEqual(_cap_base(limit.playing_teams_min), 1)
        self.assertEqual(_cap_overrides(limit.playing_teams_min), {date(2025, 9, 1): 2})

    def test_match_count_limits_min_alone_is_allowed(self) -> None:
        """A rule can be a pure floor, with no 'max' at all."""
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          min: 1\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        self.assertIsNone(_cap_base(limit.match_max))
        self.assertEqual(_cap_base(limit.match_min), 1)

    def test_match_count_limits_min_zero_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          min: 0\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "matches.min"):
            fixturespec.load_spec(path)

    def test_match_count_limits_playing_teams_min_zero_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        playing_teams:\n"
            "          min: 0\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "playing_teams.min"):
            fixturespec.load_spec(path)

    def test_match_count_limits_min_overrides_zero_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: 3\n"
            "          min_overrides:\n"
            "            2025-09-01: 0\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "min_overrides"):
            fixturespec.load_spec(path)

    def test_match_count_limits_min_overrides_require_one_day_window(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        time_window_days: 7\n"
            "        matches:\n"
            "          max: 3\n"
            "          min_overrides:\n"
            "            2025-09-01: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "min_overrides"):
            fixturespec.load_spec(path)

    def test_match_count_limits_min_allowed_with_date_ranges(self) -> None:
        """Unlike 'min_overrides', a plain 'min' base value is fine alongside
        'date_ranges' -- there's no 0-with-date_ranges special case the way
        'max' has one, since a floor of 0 is never meaningful either way."""
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          min: 1\n"
            "        date_ranges:\n"
            "          - start_date: 2025-10-27\n"
            "            end_date: 2025-11-02\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        assert isinstance(limit, fmodel.RangeLimit)
        self.assertEqual(_cap_base(limit.match_min), 1)

    def test_match_count_limits_min_overrides_reject_date_ranges(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          min: 1\n"
            "          min_overrides:\n"
            "            2025-10-27: 2\n"
            "        date_ranges:\n"
            "          - start_date: 2025-10-27\n"
            "            end_date: 2025-11-02\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "min_overrides"):
            fixturespec.load_spec(path)

    def test_match_count_limits_min_exceeds_max_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: 2\n"
            "          min: 3\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "matches.min.*exceeds"):
            fixturespec.load_spec(path)

    def test_match_count_limits_playing_teams_min_exceeds_max_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        playing_teams:\n"
            "          max: 2\n"
            "          min: 3\n"
        )
        with self.assertRaisesRegex(
            fixturespec.SpecError, "playing_teams.min.*exceeds"
        ):
            fixturespec.load_spec(path)

    def test_match_count_limits_measure_unsupported_key_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n"
            "          max: 1\n"
            "          nonsense: true\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "not supported"):
            fixturespec.load_spec(path)

    def test_match_count_limits_measure_empty_mapping_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches: {}\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "non-empty mapping"):
            fixturespec.load_spec(path)

    def test_match_count_limits_exclude_dates_parsed(self) -> None:
        """'exclude_dates' lands on the resolved limit as a frozenset of dates
        whose counted matches the rule ignores -- and, unlike the *_overrides
        fields, it's allowed alongside a multi-day rolling window."""
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        playing_teams:\n          max: 3\n"
            "        time_window_days: 7\n"
            "        exclude_dates:\n"
            "          - 2025-09-01\n"
            "          - 2025-09-08\n"
        )
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        assert isinstance(limit, fmodel.RollingLimit)
        self.assertEqual(limit.window_days, 7)
        self.assertEqual(
            limit.exclude_dates,
            frozenset({date(2025, 9, 1), date(2025, 9, 8)}),
        )

    def test_match_count_limits_exclude_dates_defaults_to_empty(self) -> None:
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        limit = _find_limit(
            spec.parameters.match_count_limits,
            club="albany",
            venue_scope=fmodel.VenueScope.HOME,
            apply_per=fmodel.ApplyPer.ACROSS_TEAMS,
        )
        assert isinstance(limit, fmodel.RollingLimit)
        self.assertEqual(limit.exclude_dates, frozenset())

    def test_match_count_limits_exclude_dates_invalid_date_rejected(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: 3\n"
            "        exclude_dates:\n"
            "          - not-a-date\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "exclude_dates"):
            fixturespec.load_spec(path)

    def test_match_count_limits_exclude_dates_reject_date_ranges(self) -> None:
        path = self._write(
            _BOILERPLATE + "club_constraints:\n"
            "  albany:\n"
            "    match_count_limits:\n"
            "      - venue_scope: home\n"
            "        matches:\n          max: 0\n"
            "        exclude_dates:\n"
            "          - 2025-09-01\n"
            "        date_ranges:\n"
            "          - start_date: 2025-09-01\n"
            "            end_date: 2025-09-07\n"
        )
        with self.assertRaisesRegex(
            fixturespec.SpecError, "exclude_dates.*date_ranges"
        ):
            fixturespec.load_spec(path)

    def test_club_latest_match_date_parsed(self) -> None:
        """A per-club latest_match_date lands in Parameters.club_latest_match_date,
        keyed by club, and only for the clubs that set one."""
        path = self._write(
            _MINIMAL_SPEC_NO_CONCURRENCY + "    latest_match_date: 2025-09-20\n"
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            spec.parameters.club_latest_match_date, {"hackney": date(2025, 9, 20)}
        )

    def test_club_latest_match_date_absent(self) -> None:
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.club_latest_match_date, {})

    def test_club_latest_match_date_invalid(self) -> None:
        path = self._write(
            _MINIMAL_SPEC_NO_CONCURRENCY + "    latest_match_date: not-a-date\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "latest_match_date"):
            fixturespec.load_spec(path)

    def test_fixed_fixture(self) -> None:
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

    def test_fixed_fixture_before_earliest_match_date_still_solves(self) -> None:
        """A fixed_fixtures entry dated before earliest_match_date is not rejected,
        and the solver still places it there -- the cutoff only excludes candidate
        dates for newly scheduled fixtures, not fixtures already pinned down. Albany's
        other home date (2025-09-29) is on/after the cutoff, so its own (unfixed)
        fixture against Hackney still has somewhere to land."""
        path = self._write(
            _MINIMAL_SPEC.replace(
                "earliest_match_date: 2025-01-01", "earliest_match_date: 2025-09-20"
            )
            + "fixed_fixtures:\n"
            "  - home: hackney-1\n"
            "    away: albany-1\n"
            "    date: 2025-09-15\n"
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.earliest_match_date, date(2025, 9, 20))
        hackney_1 = next(t for t in spec.parameters.teams if t.club == "hackney")
        albany_1 = next(t for t in spec.parameters.teams if t.club == "albany")
        fixed = fmodel.ScheduledFixture(
            fixture=fmodel.Fixture(home_team=hackney_1, away_team=albany_1),
            date=date(2025, 9, 15),
        )
        fixtures = list(fmodel.solve(spec.parameters).fixtures)
        self.assertIn(fixed, fixtures)

    def test_fixed_fixtures_absent(self) -> None:
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.fixed_fixtures, ())

    def test_fixed_fixtures_not_a_list(self) -> None:
        path = self._write(_MINIMAL_SPEC + "fixed_fixtures:\n  home: hackney-1\n")
        with self.assertRaisesRegex(fixturespec.SpecError, "list"):
            fixturespec.load_spec(path)

    def test_fixed_fixtures_missing_field(self) -> None:
        path = self._write(
            _MINIMAL_SPEC + "fixed_fixtures:\n  - home: hackney-1\n    away: albany-1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "date"):
            fixturespec.load_spec(path)

    def test_fixed_fixtures_unsupported_field(self) -> None:
        path = self._write(
            _MINIMAL_SPEC + "fixed_fixtures:\n"
            "  - home: hackney-1\n"
            "    away: albany-1\n"
            "    date: 2025-09-15\n"
            "    venue: elsewhere\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "venue"):
            fixturespec.load_spec(path)

    def test_fixed_fixtures_unknown_team(self) -> None:
        path = self._write(
            _MINIMAL_SPEC + "fixed_fixtures:\n"
            "  - home: nonexistent\n"
            "    away: albany-1\n"
            "    date: 2025-09-15\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "nonexistent"):
            fixturespec.load_spec(path)

    def test_fixed_fixtures_home_equals_away(self) -> None:
        path = self._write(
            _MINIMAL_SPEC + "fixed_fixtures:\n"
            "  - home: hackney-1\n"
            "    away: hackney-1\n"
            "    date: 2025-09-15\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "hackney-1"):
            fixturespec.load_spec(path)

    def test_fixed_fixtures_different_divisions_rejected(self) -> None:
        path = self._write(
            _MINIMAL_SPEC.replace(
                "  1:\n    scheme: double_round\n    teams: [albany-1, hackney-1]",
                "  1:\n    scheme: double_round\n    teams: [albany-1]\n"
                "  2:\n    scheme: double_round\n    teams: [hackney-1]",
            )
            + "fixed_fixtures:\n"
            "  - home: hackney-1\n"
            "    away: albany-1\n"
            "    date: 2025-09-15\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "not in the same division"):
            fixturespec.load_spec(path)

    def test_fixed_fixtures_date_not_a_home_date_rejected(self) -> None:
        path = self._write(
            _MINIMAL_SPEC + "fixed_fixtures:\n"
            "  - home: hackney-1\n"
            "    away: albany-1\n"
            "    date: 2025-09-16\n"  # not one of hackney's home dates
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "home dates"):
            fixturespec.load_spec(path)

    def test_match_count_limits_dates_is_sugar_for_single_day_ranges(self) -> None:
        """A 'dates' list resolves to a RangeLimit with one single-day DateRange
        per date -- identical to writing them out as 'date_ranges'."""
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1]\n"
                "        venue_scope: away\n"
                "        matches: {max: 0}\n"
                "        dates: [2025-10-01, 2025-11-01]\n"
            )
        )
        spec = fixturespec.load_spec(path)
        albany_1 = next(
            t for t in spec.parameters.teams if t.club == "albany" and t.index == 1
        )
        self.assertEqual(
            list(spec.parameters.match_count_limits),
            [
                fmodel.RangeLimit(
                    teams=[albany_1],
                    match_max=fmodel.Cap(0),
                    venue_scope=fmodel.VenueScope.AWAY,
                    ranges=(
                        fmodel.DateRange(
                            start=date(2025, 10, 1), end=date(2025, 10, 1)
                        ),
                        fmodel.DateRange(
                            start=date(2025, 11, 1), end=date(2025, 11, 1)
                        ),
                    ),
                )
            ],
        )

    def test_match_count_limits_dates_and_date_ranges_rejected(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - matches: {max: 0}\n"
                "        dates: [2025-10-01]\n"
                "        date_ranges: [{start_date: 2025-11-01, end_date: 2025-11-02}]\n"
            )
        )
        with self.assertRaisesRegex(
            fixturespec.SpecError, "'date_ranges' and 'dates' can't be combined"
        ):
            fixturespec.load_spec(path)

    def test_match_count_limits_dates_empty_rejected(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - matches: {max: 0}\n"
                "        dates: []\n"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "non-empty list"):
            fixturespec.load_spec(path)

    def test_match_count_limits_dates_with_time_window_days_rejected(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - matches: {max: 0}\n"
                "        dates: [2025-10-01]\n"
                "        time_window_days: 7\n"
            )
        )
        with self.assertRaisesRegex(
            fixturespec.SpecError, "'dates' and 'time_window_days' can't"
        ):
            fixturespec.load_spec(path)

    def test_match_count_limits_dates_duplicate_rejected(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - matches: {max: 0}\n"
                "        dates: [2025-10-01, 2025-10-01]\n"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "duplicate date"):
            fixturespec.load_spec(path)

    def test_match_count_limits_dates_max_zero_allowed_like_date_ranges(self) -> None:
        """'matches: {max: 0}' -- meaningless in the rolling-window form -- is
        accepted with 'dates', exactly as it is with 'date_ranges'."""
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - matches: {max: 0}\n"
                "        dates: [2025-10-01]\n"
            )
        )
        spec = fixturespec.load_spec(path)
        (limit,) = spec.parameters.match_count_limits
        assert isinstance(limit, fmodel.RangeLimit)
        self.assertEqual(limit.match_max, fmodel.Cap(0))

    def test_fixed_fixtures_on_a_per_team_home_blackout_date_still_parses(self) -> None:
        """A per-team 'venue_scope: home' / 'matches: {max: 0}' blackout no longer
        feeds the fixed_fixtures pre-check (which only looks at the club's
        home_dates); a pin the blackout forbids is caught when the model is
        built, not by load_spec."""
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1]\n"
                "        venue_scope: home\n"
                "        matches: {max: 0}\n"
                "        dates: [2025-09-01]\n"
            )
            + "fixed_fixtures:\n"
            "  - home: albany-1\n"
            "    away: albany-2\n"
            "    date: 2025-09-01\n"  # a club home_date, but albany-1's home blackout
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(len(spec.parameters.fixed_fixtures), 1)

    def test_fixed_fixtures_date_not_one_of_clubs_home_dates_rejected(self) -> None:
        path = self._write(
            _MINIMAL_SPEC + "fixed_fixtures:\n"
            "  - home: albany-1\n"
            "    away: hackney-1\n"
            "    date: 2025-09-16\n"  # not one of albany's home dates
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "home dates"):
            fixturespec.load_spec(path)

    def _with_albany_match_count_limits(self, block: str) -> str:
        """_THREE_TEAM_SPEC (which has two Albany teams) with the given
        'match_count_limits:' block (already indented as it should appear)
        nested under club_constraints.albany, alongside its home_dates."""
        return _THREE_TEAM_SPEC.replace(
            "  albany:\n    home_dates: [2025-09-01, 2025-10-01, 2025-11-01, 2025-12-01]\n",
            "  albany:\n    home_dates: [2025-09-01, 2025-10-01, 2025-11-01, 2025-12-01]\n"
            + block,
        )

    def test_match_count_limits_absent(self) -> None:
        path = self._write(_THREE_TEAM_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.match_count_limits, ())

    def test_match_count_limits_parsed(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1, albany-2]\n"
                "        matches:\n          max: 1\n"
            )
        )
        spec = fixturespec.load_spec(path)
        albany_1 = next(
            t for t in spec.parameters.teams if t.club == "albany" and t.index == 1
        )
        albany_2 = next(
            t for t in spec.parameters.teams if t.club == "albany" and t.index == 2
        )
        self.assertEqual(
            list(spec.parameters.match_count_limits),
            [
                fmodel.RollingLimit(
                    teams=[albany_1, albany_2],
                    match_max=fmodel.Cap(1),
                    window_days=1,
                )
            ],
        )

    def test_match_count_limits_teams_defaults_to_all_of_club(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n      - matches:\n          max: 3\n        time_window_days: 7\n"
            )
        )
        spec = fixturespec.load_spec(path)
        albany_1 = next(
            t for t in spec.parameters.teams if t.club == "albany" and t.index == 1
        )
        albany_2 = next(
            t for t in spec.parameters.teams if t.club == "albany" and t.index == 2
        )
        constraints = list(spec.parameters.match_count_limits)
        self.assertEqual(list(constraints[0].teams), [albany_1, albany_2])
        assert isinstance(constraints[0], fmodel.RollingLimit)
        self.assertEqual(_cap_base(constraints[0].match_max), 3)
        self.assertEqual(constraints[0].window_days, 7)

    def test_match_count_limits_time_window_days_parsed(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1, albany-2]\n"
                "        matches:\n          max: 1\n"
                "        time_window_days: 3\n"
            )
        )
        spec = fixturespec.load_spec(path)
        constraints = list(spec.parameters.match_count_limits)
        assert isinstance(constraints[0], fmodel.RollingLimit)
        self.assertEqual(constraints[0].window_days, 3)

    def test_match_count_limits_time_window_days_defaults_to_one(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1, albany-2]\n"
                "        matches:\n          max: 1\n"
            )
        )
        spec = fixturespec.load_spec(path)
        constraints = list(spec.parameters.match_count_limits)
        assert isinstance(constraints[0], fmodel.RollingLimit)
        self.assertEqual(constraints[0].window_days, 1)

    def test_match_count_limits_venue_scope_defaults_to_all(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1, albany-2]\n"
                "        matches:\n          max: 1\n"
            )
        )
        spec = fixturespec.load_spec(path)
        constraints = list(spec.parameters.match_count_limits)
        self.assertEqual(constraints[0].venue_scope, fmodel.VenueScope.ALL)

    def test_match_count_limits_venue_scope_parsed(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1, albany-2]\n"
                "        matches:\n          max: 1\n"
                "        venue_scope: away\n"
            )
        )
        spec = fixturespec.load_spec(path)
        constraints = list(spec.parameters.match_count_limits)
        self.assertEqual(constraints[0].venue_scope, fmodel.VenueScope.AWAY)

    def test_match_count_limits_invalid_venue_scope_rejected(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1, albany-2]\n"
                "        matches:\n          max: 1\n"
                "        venue_scope: sometimes\n"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "venue_scope"):
            fixturespec.load_spec(path)

    def test_match_count_limits_missing_max_field(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n      - teams: [albany-1, albany-2]\n"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "matches.*playing_teams"):
            fixturespec.load_spec(path)

    def test_match_count_limits_max_below_one_rejected(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1, albany-2]\n"
                "        matches:\n          max: 0\n"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "max"):
            fixturespec.load_spec(path)

    def test_match_count_limits_time_window_days_below_one_rejected(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1, albany-2]\n"
                "        matches:\n          max: 1\n"
                "        time_window_days: 0\n"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "time_window_days"):
            fixturespec.load_spec(path)

    def test_match_count_limits_not_a_list(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits("    match_count_limits: {}\n")
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "list"):
            fixturespec.load_spec(path)

    def test_match_count_limits_entry_not_a_mapping(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n      - just-a-string\n"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "mapping"):
            fixturespec.load_spec(path)

    def test_match_count_limits_empty_teams_list(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n      - teams: []\n        matches:\n          max: 1\n"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "non-empty"):
            fixturespec.load_spec(path)

    def test_match_count_limits_duplicate_team(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1, albany-1]\n"
                "        matches:\n          max: 1\n"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "duplicate"):
            fixturespec.load_spec(path)

    def test_match_count_limits_unknown_team(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1, nonexistent]\n"
                "        matches:\n          max: 1\n"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "nonexistent"):
            fixturespec.load_spec(path)

    def test_match_count_limits_team_belongs_to_different_club(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1, hackney-1]\n"
                "        matches:\n          max: 1\n"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "hackney-1"):
            fixturespec.load_spec(path)

    def test_match_count_limits_unsupported_field(self) -> None:
        path = self._write(
            self._with_albany_match_count_limits(
                "    match_count_limits:\n"
                "      - teams: [albany-1, albany-2]\n"
                "        matches:\n          max: 1\n"
                "        venue: elsewhere\n"
            )
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "not supported"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_absent(self) -> None:
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        self.assertEqual(spec.parameters.excluded_fixtures, ())

    def test_exclude_specific_fixture(self) -> None:
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

    def test_exclude_team(self) -> None:
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

    def test_exclude_club(self) -> None:
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

    def test_exclude_fixtures_unknown_club(self) -> None:
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n  clubs: [nonexistent]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "nonexistent"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_unknown_team(self) -> None:
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n  teams: [nonexistent]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "nonexistent"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_unsupported_key(self) -> None:
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n  players: [nonexistent]\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "not supported"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_unknown_team_in_fixtures_list(self) -> None:
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n"
            "  fixtures:\n"
            "    - home: nonexistent\n"
            "      away: albany-2\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "nonexistent"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_home_equals_away(self) -> None:
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n"
            "  fixtures:\n"
            "    - home: albany-1\n"
            "      away: albany-1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "albany-1"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_different_divisions_rejected(self) -> None:
        path = self._write(
            _THREE_TEAM_SPEC.replace(
                "  1:\n    scheme: double_round\n"
                "    teams: [albany-1, albany-2, hackney-1]",
                "  1:\n    scheme: double_round\n    teams: [albany-1, albany-2]\n"
                "  2:\n    scheme: double_round\n    teams: [hackney-1]",
            )
            + "exclude_fixtures:\n"
            "  fixtures:\n"
            "    - home: albany-1\n"
            "      away: hackney-1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "not in the same division"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_missing_field(self) -> None:
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n  fixtures:\n    - home: albany-1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "away"):
            fixturespec.load_spec(path)

    def test_exclude_fixtures_conflicts_with_fixed_fixtures(self) -> None:
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

    def test_exclude_fixtures_solves_end_to_end(self) -> None:
        path = self._write(
            _THREE_TEAM_SPEC + "exclude_fixtures:\n"
            "  fixtures:\n"
            "    - home: albany-1\n"
            "      away: albany-2\n"
        )
        spec = fixturespec.load_spec(path)
        fixtures = list(fmodel.solve(spec.parameters).fixtures)
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

    def test_latest_internal_match_date_conflicts_with_fixed_fixtures(self) -> None:
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

    def test_latest_internal_match_date_ignores_cross_club_fixed_fixtures(self) -> None:
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

    def test_latest_internal_match_date_solves_end_to_end(self) -> None:
        path = self._write(
            _THREE_TEAM_SPEC + "latest_internal_match_date: 2025-10-15\n"
        )
        spec = fixturespec.load_spec(path)
        fixtures = list(fmodel.solve(spec.parameters).fixtures)
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

    def test_club_latest_match_date_conflicts_with_fixed_fixture_home_team(
        self,
    ) -> None:
        path = self._write(
            _THREE_TEAM_SPEC + "    latest_match_date: 2026-01-15\n"
            "fixed_fixtures:\n"
            "  - home: hackney-1\n"
            "    away: albany-1\n"
            "    date: 2026-02-01\n"
        )
        with self.assertRaisesRegex(
            fixturespec.SpecError, r"is after club_constraints\['hackney'\]"
        ):
            fixturespec.load_spec(path)

    def test_club_latest_match_date_conflicts_with_fixed_fixture_away_team(
        self,
    ) -> None:
        """The cutoff also rejects a fixed fixture where the club plays away."""
        path = self._write(
            _THREE_TEAM_SPEC + "    latest_match_date: 2025-11-15\n"
            "fixed_fixtures:\n"
            "  - home: albany-1\n"
            "    away: hackney-1\n"
            "    date: 2025-12-01\n"
        )
        with self.assertRaisesRegex(
            fixturespec.SpecError, r"is after club_constraints\['hackney'\]"
        ):
            fixturespec.load_spec(path)

    def test_club_latest_match_date_solves_end_to_end(self) -> None:
        """A cutoff that still leaves hackney a usable home date solves, with every
        hackney fixture on or before the cutoff."""
        # hackney-1 hosts two required home fixtures (v albany-1 and v albany-2), and
        # a team may only play once per date, so needs two usable home dates within
        # the cutoff -- an extra one is added here beyond _THREE_TEAM_SPEC's own.
        path = self._write(
            _THREE_TEAM_SPEC.replace(
                "home_dates: [2026-01-01, 2026-02-01]",
                "home_dates: [2025-12-18, 2026-01-01, 2026-02-01]",
            )
            + "    latest_match_date: 2026-01-15\n"
        )
        spec = fixturespec.load_spec(path)
        self.assertEqual(
            spec.parameters.club_latest_match_date, {"hackney": date(2026, 1, 15)}
        )
        fixtures = list(fmodel.solve(spec.parameters).fixtures)
        hackney_fixtures = [
            sf
            for sf in fixtures
            if "hackney" in (sf.fixture.home_team.club, sf.fixture.away_team.club)
        ]
        self.assertEqual(
            len(hackney_fixtures), 4
        )  # hackney-1 home and away vs each albany
        for sf in hackney_fixtures:
            self.assertLessEqual(sf.date, date(2026, 1, 15))

    def test_club_latest_match_date_before_all_its_home_dates_is_infeasible(
        self,
    ) -> None:
        """A cutoff before every hackney home date leaves the hackney-hosted
        fixtures with no schedulable date -- required, so solve() raises."""
        path = self._write(_THREE_TEAM_SPEC + "    latest_match_date: 2025-12-31\n")
        spec = fixturespec.load_spec(path)
        with self.assertRaisesRegex(ValueError, "no schedulable date"):
            fmodel.solve(spec.parameters)

    def test_solves_end_to_end(self) -> None:
        """A loaded spec's Parameters should be usable directly with fmodel.solve()."""
        path = self._write(_MINIMAL_SPEC)
        spec = fixturespec.load_spec(path)
        fixtures = list(fmodel.solve(spec.parameters).fixtures)
        self.assertEqual(len(fixtures), 2)  # Albany v Hackney and Hackney v Albany


class TestLoadTeamIds(unittest.TestCase):
    """Test cases for load_team_ids()."""

    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.dir = Path(self._tmpdir.name)

    def _write(self, contents: str, name: str = "spec.yaml") -> Path:
        path = self.dir / name
        path.write_text(contents)
        return path

    def test_maps_team_ids_to_club_and_index(self) -> None:
        path = self._write(_MINIMAL_SPEC)
        self.assertEqual(
            fixturespec.load_team_ids(path),
            {"albany-1": ("albany", 1), "hackney-1": ("hackney", 1)},
        )

    def test_does_not_require_divisions_or_club_constraints(self) -> None:
        """Unlike load_spec(), only 'clubs' and 'teams' need to be valid."""
        path = self._write(_BOILERPLATE)  # no club_constraints, no divisions issues
        self.assertEqual(
            fixturespec.load_team_ids(path),
            {"albany-1": ("albany", 1), "hackney-1": ("hackney", 1)},
        )

    def test_unknown_club_still_rejected(self) -> None:
        path = self._write(
            "clubs:\n"
            "  hackney:\n"
            "    name: Hackney\n"
            "    home_venue_name: x\n"
            "    home_venue_address: x\n"
            "    home_start_time: '19:00'\n"
            "    home_time_limit: '60+15'\n"
            "teams:\n  albany-1:\n    club: albany\n    index: 1\n"
        )
        with self.assertRaisesRegex(fixturespec.SpecError, "albany"):
            fixturespec.load_team_ids(path)


if __name__ == "__main__":
    unittest.main()
