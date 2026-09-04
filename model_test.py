# Copyright 2025, 2026 Andrew Medworth
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

"""Test cases for fixtures model constraints."""

import collections
import dataclasses
import random
import unittest
from collections.abc import Collection
from datetime import date, timedelta
from typing import Any

import berger
import fmodel
import genfixtures


def _home_limit(n: int | None) -> tuple[fmodel.VenueScope, int | None]:
    """Legacy shorthand: a club's per-date HOME-scope match cap of `n` (None =
    unlimited). Consumed by _params() below, which turns it into a
    fmodel.MatchCountLimit over that club's teams."""
    return (fmodel.VenueScope.HOME, n)


def _params(
    *,
    min_gap_days: int | None = None,
    max_concurrent_matches: (
        dict[str, tuple[fmodel.VenueScope, int | None]] | None
    ) = None,
    match_count_limits: Collection[fmodel.MatchLimit] = (),
    **kwargs: Any,
) -> fmodel.Parameters:
    """fmodel.Parameters with the old min_gap_days / max_concurrent_matches
    conveniences expressed as match_count_limits.

    `min_gap_days` (falsy -> skipped) adds a per-team window of that length,
    limit 1, for every club. Each `max_concurrent_matches` entry (a club ID ->
    (scope, n) pair, e.g. from _home_limit) adds a shared cap of `n` matches of
    that scope for the club's teams. Any explicit `match_count_limits` are kept.
    """
    teams = list(kwargs["teams"])
    teams_by_club: dict[str, list[fmodel.Team]] = collections.defaultdict(list)
    for team in teams:
        teams_by_club[team.club].append(team)

    limits: list[fmodel.MatchLimit] = list(match_count_limits)
    if min_gap_days:
        for club_teams in teams_by_club.values():
            limits.append(
                fmodel.RollingLimit(
                    teams=club_teams,
                    match_cap=fmodel.Cap(1),
                    window_days=min_gap_days,
                    apply_per=fmodel.ApplyPer.EACH_TEAM,
                )
            )
    for club, (scope, n) in (max_concurrent_matches or {}).items():
        limits.append(
            fmodel.RollingLimit(
                teams=teams_by_club[club], match_cap=fmodel.Cap(n), venue_scope=scope
            )
        )
    return fmodel.Parameters(match_count_limits=limits, **kwargs)


class TestSolve(unittest.TestCase):
    """Test cases for the solve() function."""

    params: fmodel.Parameters
    fixtures: list[fmodel.ScheduledFixture]

    @classmethod
    def setUpClass(cls) -> None:
        """Set up class-level data by solving once with real parameters."""
        # Seed random number generator for reproducible test results
        random.seed(42)
        cls.params = genfixtures.build_params()
        cls.fixtures = list(fmodel.solve(cls.params).fixtures)

    def test_basic_solve(self) -> None:
        """Test that solve produces fixtures with real parameters."""
        self.assertGreater(len(self.fixtures), 0, "Should generate some fixtures")
        for sf in self.fixtures:
            self.assertIsInstance(sf, fmodel.ScheduledFixture)

    def test_fixture_uniqueness(self) -> None:
        """Test that all fixtures are unique."""
        fixture_pairs: set[tuple[fmodel.Team, fmodel.Team]] = set()
        for sf in self.fixtures:
            pair = (sf.fixture.home_team, sf.fixture.away_team)
            self.assertNotIn(pair, fixture_pairs, f"Duplicate fixture: {pair}")
            fixture_pairs.add(pair)

    def test_valid_home_dates(self) -> None:
        """Test that all fixtures are on valid home dates."""
        for sf in self.fixtures:
            home_club = sf.fixture.home_team.club
            self.assertIn(
                sf.date,
                self.params.home_dates[home_club],
                f"Fixture on {sf.date} not on valid home date for {home_club}",
            )

    def test_team_constraints(self) -> None:
        """Test that team constraints are satisfied with real parameters."""
        # Verify no team plays more than one fixture on the same date
        team_schedule = collections.defaultdict(list)
        for sf in self.fixtures:
            team_schedule[sf.fixture.home_team].append(sf.date)
            team_schedule[sf.fixture.away_team].append(sf.date)

        for team, dates in team_schedule.items():
            unique_dates = set(dates)
            self.assertEqual(
                len(dates),
                len(unique_dates),
                f"Team {team.name} has multiple fixtures on same date",
            )

    def test_min_gap_constraint(self) -> None:
        """Test minimum gap days constraint with real parameters."""
        # Verify minimum gap constraint for each team
        team_dates = collections.defaultdict(list)
        for sf in self.fixtures:
            team_dates[sf.fixture.home_team].append(sf.date)
            team_dates[sf.fixture.away_team].append(sf.date)

        for team, dates in team_dates.items():
            sorted_dates = sorted(dates)
            for i in range(1, len(sorted_dates)):
                gap = (sorted_dates[i] - sorted_dates[i - 1]).days
                self.assertGreaterEqual(
                    gap,
                    genfixtures._MIN_MATCH_GAP_DAYS,
                    f"Team {team.name} has fixtures too close: {sorted_dates[i - 1]} and {sorted_dates[i]} (gap: {gap} days)",
                )

    def test_max_concurrent_home_constraint(self) -> None:
        """Test max concurrent home matches constraint with real parameters
        (genfixtures caps each club at two home matches a night)."""
        home_fixtures_by_club_date: dict[tuple[str, date], int] = (
            collections.defaultdict(int)
        )
        for sf in self.fixtures:
            key = (sf.fixture.home_team.club, sf.date)
            home_fixtures_by_club_date[key] += 1

        for (club, fixture_date), count in home_fixtures_by_club_date.items():
            self.assertLessEqual(
                count,
                2,
                f"Club {club} has {count} home matches on {fixture_date}, exceeding limit of 2",
            )

    def test_unavailable_away_dates(self) -> None:
        """Test that unavailable away dates are respected with real parameters."""
        # Verify clubs don't play away on their unavailable dates
        for sf in self.fixtures:
            away_club = sf.fixture.away_team.club
            if away_club in self.params.unavailable_away_dates:
                self.assertNotIn(
                    sf.date,
                    self.params.unavailable_away_dates[away_club],
                    f"Club {away_club} scheduled away on unavailable date {sf.date}",
                )

    def test_division_separation(self) -> None:
        """Test that teams only play within their division with real parameters."""
        # Verify no cross-division fixtures
        for sf in self.fixtures:
            home_division = sf.fixture.home_team.division
            away_division = sf.fixture.away_team.division
            self.assertEqual(
                home_division,
                away_division,
                f"Cross-division fixture: {sf.fixture.home_team.name} (div {home_division}) vs "
                f"{sf.fixture.away_team.name} (div {away_division})",
            )

    def test_completeness(self) -> None:
        """Test that all required fixtures within divisions are scheduled with real parameters."""
        # Calculate expected fixtures by division
        teams_by_division = collections.defaultdict(list)
        for team in self.params.teams:
            teams_by_division[team.division].append(team)

        expected_fixtures = set()
        for division_teams in teams_by_division.values():
            for home_team in division_teams:
                for away_team in division_teams:
                    if home_team != away_team:
                        expected_fixtures.add((home_team, away_team))

        # Check all expected fixtures are scheduled
        scheduled_fixtures = set()
        for sf in self.fixtures:
            scheduled_fixtures.add((sf.fixture.home_team, sf.fixture.away_team))

        self.assertEqual(
            len(scheduled_fixtures),
            len(expected_fixtures),
            f"Expected {len(expected_fixtures)} fixtures, got {len(scheduled_fixtures)}",
        )
        self.assertEqual(
            scheduled_fixtures,
            expected_fixtures,
            "Not all required fixtures were scheduled",
        )

    def test_fixture_count_by_division(self) -> None:
        """Test that fixture counts are correct for each division with real parameters."""
        # Count fixtures by division
        fixtures_by_division: dict[int, int] = collections.defaultdict(int)
        teams_by_division: dict[int, int] = collections.defaultdict(int)

        for team in self.params.teams:
            teams_by_division[team.division] += 1

        for sf in self.fixtures:
            fixtures_by_division[sf.fixture.home_team.division] += 1

        # Each division should have n * (n-1) fixtures where n is number of teams
        for division, team_count in teams_by_division.items():
            expected_fixture_count = team_count * (team_count - 1)
            actual_fixture_count = fixtures_by_division[division]
            self.assertEqual(
                actual_fixture_count,
                expected_fixture_count,
                f"Division {division}: expected {expected_fixture_count} fixtures, got {actual_fixture_count}",
            )

    def test_teams_play_both_home_and_away(self) -> None:
        """Test that each team plays both home and away fixtures with real parameters."""
        home_fixtures_by_team: dict[fmodel.Team, int] = collections.defaultdict(int)
        away_fixtures_by_team: dict[fmodel.Team, int] = collections.defaultdict(int)

        for sf in self.fixtures:
            home_fixtures_by_team[sf.fixture.home_team] += 1
            away_fixtures_by_team[sf.fixture.away_team] += 1

        for team in self.params.teams:
            self.assertGreater(
                home_fixtures_by_team[team], 0, f"Team {team.name} has no home fixtures"
            )
            self.assertGreater(
                away_fixtures_by_team[team], 0, f"Team {team.name} has no away fixtures"
            )

    def test_simple_impossible_constraint(self) -> None:
        """A required fixture with no schedulable date makes solve() raise, rather
        than silently returning a schedule that omits it."""
        team1 = fmodel.Team(division=1, club="Test Club A", index=1)
        team2 = fmodel.Team(division=1, club="Test Club B", index=1)

        params = _params(
            teams=[team1, team2],
            home_dates={
                "Test Club A": [date(2025, 1, 1)],  # A can only play home on Jan 1
                "Test Club B": [date(2025, 1, 2)],  # B can only play home on Jan 2
            },
            unavailable_away_dates={
                "Test Club A": [
                    date(2025, 1, 2)
                ],  # A can't play away on Jan 2 (when B is home)
                "Test Club B": [
                    date(2025, 1, 1)
                ],  # B can't play away on Jan 1 (when A is home)
            },
            min_gap_days=7,
            max_concurrent_matches={
                "Test Club A": _home_limit(1),
                "Test Club B": _home_limit(1),
            },
        )

        with self.assertRaisesRegex(ValueError, "no schedulable date"):
            fmodel.solve(params)


class TestSolveStats(unittest.TestCase):
    """solve() returns the OR-Tools model/solver summary text alongside the
    schedule, and raises when the model is genuinely unsatisfiable."""

    def test_captures_model_and_solve_stats(self) -> None:
        random.seed(42)
        result = fmodel.solve(genfixtures.build_params())
        self.assertIn("#Variables", result.model_stats)
        self.assertIn("CpSolverResponse summary", result.solve_stats)
        self.assertIn("status:", result.solve_stats)

    def test_copies_spec_checksum_from_parameters_to_result(self) -> None:
        random.seed(42)
        params = dataclasses.replace(
            genfixtures.build_params(), spec_checksum="sha256:" + "ab" * 32
        )
        self.assertEqual(fmodel.solve(params).spec_checksum, "sha256:" + "ab" * 32)

    def test_raises_when_infeasible(self) -> None:
        # Two teams of one club, only a single shared home date: both the A1 v A2
        # and A2 v A1 fixtures are forced onto it, but the per-team weekly limit
        # forbids either team playing twice that day -- an unsatisfiable model,
        # not just an empty schedule.
        team1 = fmodel.Team(division=1, club="A", index=1)
        team2 = fmodel.Team(division=1, club="A", index=2)
        params = _params(
            teams=[team1, team2],
            home_dates={"A": [date(2025, 1, 1)]},
            unavailable_away_dates={"A": []},
            min_gap_days=7,
        )
        with self.assertRaisesRegex(ValueError, "No solution found"):
            fmodel.solve(params)


class TestOneMatchPerTeamPerDate(unittest.TestCase):
    """A team -- home or away -- may never be scheduled for more than one match on
    the same date: physically its players can't be in two places at once. This is
    unconditional (no MatchCountLimit or other spec field is involved), so it holds
    even when nothing else in Parameters would otherwise prevent the clash.
    """

    def test_forbids_a_team_playing_home_and_away_the_same_day(self) -> None:
        """A1 and X1 (division of just the two of them) share a single common
        date -- both A1 v X1 (A1 home, X1 away) and X1 v A1 (A1 away, X1 home) only
        have that one date available, so A1 (and X1) would need to play both there.
        Forbidden, regardless of home/away sidedness."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        x1 = fmodel.Team(division=1, club="X", index=1)
        shared_date = date(2025, 1, 1)
        params = _params(
            teams=[a1, x1],
            home_dates={"A": [shared_date], "X": [shared_date]},
            unavailable_away_dates={"A": [], "X": []},
        )
        with self.assertRaisesRegex(ValueError, "No solution found"):
            fmodel.solve(params)

    def test_allows_the_same_pair_to_meet_twice_on_different_dates(self) -> None:
        """The same setup, but with a second date free for one of the two legs --
        solves, confirming the rule only forbids sharing a date, not meeting twice
        at all."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        x1 = fmodel.Team(division=1, club="X", index=1)
        params = _params(
            teams=[a1, x1],
            home_dates={
                "A": [date(2025, 1, 1)],
                "X": [date(2025, 1, 8)],
            },
            unavailable_away_dates={"A": [], "X": []},
        )
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertEqual(len(fixtures), 2)
        self.assertEqual(
            {sf.date for sf in fixtures}, {date(2025, 1, 1), date(2025, 1, 8)}
        )

    def test_forbids_two_different_opponents_on_the_same_date(self) -> None:
        """A1 hosts both X1 and Y1, but only has one home date -- A1 can't play
        both matches there, even though they're against different opponents (so no
        MatchCountLimit -- which only ever names A1's own club's teams in a venue
        capacity/keep-apart rule -- would naturally catch this). X1 and Y1 each get
        two home dates of their own, so this isolates A1's clash as the only cause
        of infeasibility."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        x1 = fmodel.Team(division=1, club="X", index=1)
        y1 = fmodel.Team(division=1, club="Y", index=1)
        shared_date = date(2025, 1, 1)
        params = _params(
            teams=[a1, x1, y1],
            home_dates={
                "A": [shared_date],
                "X": [date(2025, 3, 1), date(2025, 3, 8)],
                "Y": [date(2025, 4, 1), date(2025, 4, 8)],
            },
            unavailable_away_dates={"A": [], "X": [], "Y": []},
        )
        with self.assertRaisesRegex(ValueError, "No solution found"):
            fmodel.solve(params)


class TestSharedLimitAtOrAboveGroupSize(unittest.TestCase):
    """A shared-budget MatchCountLimit (apply_per=ACROSS_TEAMS) with max_matches >= the number
    of teams it covers can never bind -- the teams can't play more simultaneous
    matches than there are of them -- so the solver skips it (this is how a
    venue capacity stated above a club's team count stays a no-op). See issue #22.
    """

    def _solve(self, num_teams: int, limit: int) -> list[fmodel.ScheduledFixture]:
        teams = [
            fmodel.Team(division=1, club="A", index=i) for i in range(1, num_teams + 1)
        ]
        params = fmodel.Parameters(
            teams=teams,
            # Two home dates, not one: A1 v A2 and A2 v A1 both need A1 to host,
            # and a team may only play once per date (see
            # _add_one_match_per_team_per_date_constraints), so with only one date
            # they couldn't both be scheduled regardless of this test's own limit.
            home_dates={"A": [date(2025, 1, 1), date(2025, 1, 8)]},
            unavailable_away_dates={"A": []},
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=teams,
                    match_cap=fmodel.Cap(limit),
                    venue_scope=fmodel.VenueScope.HOME,
                )
            ],
        )
        return list(fmodel.solve(params).fixtures)

    def test_limit_at_group_size_does_not_bind(self) -> None:
        # 2 teams, limit 2 (== group size): both home legs (A1 v A2 and A2 v A1)
        # solve -- the limit is a no-op, so it doesn't get in the way of whatever
        # else (here, the one-match-per-team-per-date rule) decides their dates.
        fixtures = self._solve(num_teams=2, limit=2)
        self.assertEqual(len(fixtures), 2)

    def test_limit_above_group_size_does_not_bind(self) -> None:
        fixtures = self._solve(num_teams=2, limit=5)
        self.assertEqual(len(fixtures), 2)

    def test_limit_below_group_size_still_binds(self) -> None:
        # 3 teams, one shared home date, home limit 2: three home legs can't all
        # fit -> infeasible.
        teams = [fmodel.Team(division=1, club="A", index=i) for i in range(1, 4)]
        params = fmodel.Parameters(
            teams=teams,
            home_dates={"A": [date(2025, 1, 1)]},
            unavailable_away_dates={"A": []},
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=teams,
                    match_cap=fmodel.Cap(2),
                    venue_scope=fmodel.VenueScope.HOME,
                )
            ],
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)


class TestHomeDatesUsedBounds(unittest.TestCase):
    """Validation of the HomeDatesUsedBounds value object itself."""

    def test_requires_at_least_one_bound(self) -> None:
        with self.assertRaises(ValueError):
            fmodel.HomeDatesUsedBounds()

    def test_rejects_min_above_max(self) -> None:
        with self.assertRaises(ValueError):
            fmodel.HomeDatesUsedBounds(minimum=5, maximum=3)

    def test_equal_min_and_max_allowed(self) -> None:
        bounds = fmodel.HomeDatesUsedBounds(minimum=3, maximum=3)
        self.assertEqual((bounds.minimum, bounds.maximum), (3, 3))


class TestHomeDatesUsed(unittest.TestCase):
    """Test cases for the home_dates_used (min/max) constraint."""

    def test_constraint_limits_dates_used(self) -> None:
        """Solver uses at most home_dates_used.maximum home dates for a club.

        Club "A" has two teams in a division with six other teams (one each from
        clubs B-G).  Each A team plays seven home matches (one vs each of the
        other seven teams, including the intra-club match).  A has twelve weekly
        home dates available but home_dates_used.maximum is set to 8.  With
        max_concurrent_matches home: 2, the solver can pack two home matches per
        date, so 14 total A home matches fit in 8 dates (capacity 16).  This
        leaves at least four of A's twelve available home dates completely unused.
        """
        other_clubs = ["B", "C", "D", "E", "F", "G"]
        teams = [
            fmodel.Team(division=1, club="A", index=1),
            fmodel.Team(division=1, club="A", index=2),
        ] + [fmodel.Team(division=1, club=c, index=1) for c in other_clubs]

        # 12 weekly home dates for A (starting 6 Jan 2025).
        a_start = date(2025, 1, 6)
        a_home_dates = [a_start + timedelta(weeks=i) for i in range(12)]
        # 8 weekly home dates each for B-G, each club's dates shifted by one day
        # relative to the previous club (B starts Sep 1, C starts Sep 2, etc.) so
        # that no two clubs share the same date set.
        other_home_dates = {
            c: [date(2025, 9, 1) + timedelta(days=i, weeks=j) for j in range(8)]
            for i, c in enumerate(other_clubs)
        }
        home_dates = {"A": a_home_dates} | other_home_dates

        all_clubs = ["A"] + other_clubs
        params = _params(
            teams=teams,
            home_dates=home_dates,
            unavailable_away_dates={c: [] for c in all_clubs},
            max_concurrent_matches={
                "A": _home_limit(2),
                **{c: _home_limit(1) for c in other_clubs},
            },
            min_gap_days=0,
            home_dates_used={"A": fmodel.HomeDatesUsedBounds(maximum=8)},
        )
        fixtures = list(fmodel.solve(params).fixtures)

        a_dates_used = {sf.date for sf in fixtures if sf.fixture.home_team.club == "A"}
        self.assertLessEqual(len(a_dates_used), 8)
        unused_a_dates = set(a_home_dates) - a_dates_used
        self.assertGreaterEqual(len(unused_a_dates), 4)

    def test_min_constraint_spreads_dates_used(self) -> None:
        """Solver uses at least home_dates_used.minimum home dates for a club.

        Same shape as test_constraint_limits_dates_used: club "A" has two teams
        playing 14 home matches between them, 12 weekly home dates available, and
        max_concurrent_matches home: 2 -- so absent any spread constraint the solver
        could pack them onto as few as 7 dates.  home_dates_used.minimum=10 forces
        it to use at least 10 distinct dates instead.
        """
        other_clubs = ["B", "C", "D", "E", "F", "G"]
        teams = [
            fmodel.Team(division=1, club="A", index=1),
            fmodel.Team(division=1, club="A", index=2),
        ] + [fmodel.Team(division=1, club=c, index=1) for c in other_clubs]

        a_start = date(2025, 1, 6)
        a_home_dates = [a_start + timedelta(weeks=i) for i in range(12)]
        other_home_dates = {
            c: [date(2025, 9, 1) + timedelta(days=i, weeks=j) for j in range(8)]
            for i, c in enumerate(other_clubs)
        }
        home_dates = {"A": a_home_dates} | other_home_dates

        all_clubs = ["A"] + other_clubs
        params = _params(
            teams=teams,
            home_dates=home_dates,
            unavailable_away_dates={c: [] for c in all_clubs},
            max_concurrent_matches={
                "A": _home_limit(2),
                **{c: _home_limit(1) for c in other_clubs},
            },
            min_gap_days=0,
            home_dates_used={"A": fmodel.HomeDatesUsedBounds(minimum=10)},
        )
        fixtures = list(fmodel.solve(params).fixtures)

        a_dates_used = {sf.date for sf in fixtures if sf.fixture.home_team.club == "A"}
        self.assertGreaterEqual(len(a_dates_used), 10)

    def test_min_constraint_enforced_strictly(self) -> None:
        """A club whose schedule can't reach home_dates_used.minimum is infeasible."""
        # A's single team plays one home match, so at most one home date can ever be
        # used; requiring a minimum of 2 is impossible.
        teams = [
            fmodel.Team(division=1, club="A", index=1),
            fmodel.Team(division=1, club="B", index=1),
        ]
        home_dates = {
            "A": [date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)],
            "B": [date(2025, 4, 1), date(2025, 5, 1), date(2025, 6, 1)],
        }
        params = _params(
            teams=teams,
            home_dates=home_dates,
            unavailable_away_dates={"A": [], "B": []},
            max_concurrent_matches={
                "A": _home_limit(1),
                "B": _home_limit(1),
            },
            min_gap_days=7,
            home_dates_used={"A": fmodel.HomeDatesUsedBounds(minimum=2)},
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_constraint_enforced_strictly(self) -> None:
        """A club with two teams needs two home dates; a limit of 1 makes it infeasible."""
        # A has two teams in the same division, requiring 2 home matches on different dates
        # (min_gap_days=7 forces different dates). But home_dates_used.maximum=1 for A
        # means only 1 date can be used — impossible.
        teams = [
            fmodel.Team(division=1, club="A", index=1),
            fmodel.Team(division=1, club="A", index=2),
            fmodel.Team(division=1, club="B", index=1),
        ]
        home_dates = {
            "A": [date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)],
            "B": [date(2025, 4, 1), date(2025, 5, 1), date(2025, 6, 1)],
        }
        params = _params(
            teams=teams,
            home_dates=home_dates,
            unavailable_away_dates={"A": [], "B": []},
            max_concurrent_matches={
                "A": _home_limit(1),
                "B": _home_limit(2),
            },
            min_gap_days=7,
            home_dates_used={
                "A": fmodel.HomeDatesUsedBounds(maximum=1),
                "B": fmodel.HomeDatesUsedBounds(maximum=3),
            },
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)


class TestFixedFixtures(unittest.TestCase):
    """Test cases for the fixed_fixtures constraint."""

    def _params(self, **kwargs: Any) -> fmodel.Parameters:
        teams = [
            fmodel.Team(division=1, club="A", index=1),
            fmodel.Team(division=1, club="A", index=2),
            fmodel.Team(division=1, club="B", index=1),
        ]
        # A needs 4 home dates to fit A1 v A2, A2 v A1, A1 v B1 and A2 v B1 without
        # double-booking B1 (B1 can't play two away matches on the same date); B just
        # needs 2 for its two home matches (B1 v A1, B1 v A2), min_gap_days apart.
        home_dates = {
            "A": [
                date(2025, 1, 1),
                date(2025, 2, 1),
                date(2025, 3, 1),
                date(2025, 4, 1),
            ],
            "B": [date(2025, 5, 1), date(2025, 6, 1)],
        }
        return _params(
            teams=teams,
            home_dates=home_dates,
            unavailable_away_dates={"A": [], "B": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "B": _home_limit(1),
            },
            min_gap_days=7,
            **kwargs,
        )

    def test_fixed_fixture_is_scheduled_on_given_date(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixed = fmodel.ScheduledFixture(
            fixture=fmodel.Fixture(home_team=a1, away_team=a2), date=date(2025, 1, 1)
        )
        params = self._params(fixed_fixtures=[fixed])
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertIn(fixed, fixtures)

    def test_fixed_fixture_on_non_home_date_rejected(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixed = fmodel.ScheduledFixture(
            fixture=fmodel.Fixture(home_team=a1, away_team=a2),
            date=date(2025, 7, 1),  # not one of A's home dates
        )
        params = self._params(fixed_fixtures=[fixed])
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_fixed_fixture_forces_other_fixtures_off_that_date(self) -> None:
        """Pinning A1 v A2 to a date means A1 and A2 are both unavailable that date,
        so the reverse fixture (which involves both of the same teams) must land
        elsewhere."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixed = fmodel.ScheduledFixture(
            fixture=fmodel.Fixture(home_team=a1, away_team=a2), date=date(2025, 1, 1)
        )
        params = self._params(fixed_fixtures=[fixed])
        fixtures = list(fmodel.solve(params).fixtures)
        # a2 v a1 (the reverse fixture) must be on a different date
        reverse = next(
            sf
            for sf in fixtures
            if sf.fixture.home_team == a2 and sf.fixture.away_team == a1
        )
        self.assertNotEqual(reverse.date, date(2025, 1, 1))

    def test_reverse_fixture_exactly_min_gap_days_apart_is_allowed(self) -> None:
        """Regression test: a gap of exactly min_gap_days satisfies a *minimum*
        gap of min_gap_days, so two fixed fixtures for the same pair of teams
        that many days apart must be schedulable, not rejected as too close."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixed = [
            fmodel.ScheduledFixture(
                fixture=fmodel.Fixture(home_team=a1, away_team=a2),
                date=date(2025, 1, 1),
            ),
            fmodel.ScheduledFixture(
                fixture=fmodel.Fixture(home_team=a2, away_team=a1),
                date=date(2025, 1, 8),
            ),
        ]
        params = _params(
            teams=[a1, a2],
            home_dates={"A": [date(2025, 1, 1), date(2025, 1, 8)]},
            unavailable_away_dates={"A": []},
            max_concurrent_matches={"A": _home_limit(None)},
            min_gap_days=7,
            fixed_fixtures=fixed,
        )
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertCountEqual(fixtures, fixed)

    def test_reverse_fixture_one_day_inside_min_gap_days_is_rejected(self) -> None:
        """A gap one day short of min_gap_days must still be rejected."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixed = [
            fmodel.ScheduledFixture(
                fixture=fmodel.Fixture(home_team=a1, away_team=a2),
                date=date(2025, 1, 1),
            ),
            fmodel.ScheduledFixture(
                fixture=fmodel.Fixture(home_team=a2, away_team=a1),
                date=date(2025, 1, 7),
            ),
        ]
        params = _params(
            teams=[a1, a2],
            home_dates={"A": [date(2025, 1, 1), date(2025, 1, 7)]},
            unavailable_away_dates={"A": []},
            max_concurrent_matches={"A": _home_limit(None)},
            min_gap_days=7,
            fixed_fixtures=fixed,
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)


class TestLatestInternalMatchDate(unittest.TestCase):
    """Test cases for the latest_internal_match_date constraint."""

    def _params(self, **kwargs: Any) -> fmodel.Parameters:
        teams = [
            fmodel.Team(division=1, club="A", index=1),
            fmodel.Team(division=1, club="A", index=2),
            fmodel.Team(division=1, club="B", index=1),
        ]
        home_dates = {
            "A": [
                date(2025, 1, 1),
                date(2025, 2, 1),
                date(2025, 3, 1),
                date(2025, 4, 1),
            ],
            "B": [date(2025, 5, 1), date(2025, 6, 1)],
        }
        return _params(
            teams=teams,
            home_dates=home_dates,
            unavailable_away_dates={"A": [], "B": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "B": _home_limit(1),
            },
            min_gap_days=7,
            **kwargs,
        )

    def test_internal_matches_respect_the_cutoff(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        params = self._params(latest_internal_match_date=date(2025, 2, 15))
        fixtures = list(fmodel.solve(params).fixtures)
        internal = [
            sf
            for sf in fixtures
            if {sf.fixture.home_team, sf.fixture.away_team} == {a1, a2}
        ]
        self.assertEqual(len(internal), 2)  # A1 v A2 and A2 v A1
        for sf in internal:
            self.assertLessEqual(sf.date, date(2025, 2, 15))

    def test_non_internal_matches_unaffected(self) -> None:
        """A cross-club fixture (different clubs) may still use a late home date."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        b1 = fmodel.Team(division=1, club="B", index=1)
        params = self._params(latest_internal_match_date=date(2025, 2, 15))
        fixtures = list(fmodel.solve(params).fixtures)
        cross_club = next(
            sf
            for sf in fixtures
            if sf.fixture.home_team == a1 and sf.fixture.away_team == b1
        )
        self.assertGreater(cross_club.date, date(2025, 2, 15))

    def test_cutoff_before_any_home_date_makes_the_solve_infeasible(self) -> None:
        """A cutoff before every A home date leaves the required A1 v A2 internal
        fixture (both directions) with no schedulable date, so solve() raises
        rather than returning a schedule quietly missing those matches."""
        params = self._params(latest_internal_match_date=date(2024, 12, 1))
        with self.assertRaisesRegex(ValueError, "no schedulable date"):
            fmodel.solve(params)

    def test_fixed_internal_fixture_after_cutoff_rejected(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixed = fmodel.ScheduledFixture(
            fixture=fmodel.Fixture(home_team=a1, away_team=a2), date=date(2025, 4, 1)
        )
        params = self._params(
            fixed_fixtures=[fixed], latest_internal_match_date=date(2025, 2, 15)
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_no_cutoff_by_default(self) -> None:
        """Without latest_internal_match_date, internal matches can use any date."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        params = self._params()
        fixtures = list(fmodel.solve(params).fixtures)
        internal_dates = {
            sf.date
            for sf in fixtures
            if {sf.fixture.home_team, sf.fixture.away_team} == {a1, a2}
        }
        self.assertTrue(internal_dates)


class TestClubLatestMatchDate(unittest.TestCase):
    """Test cases for the per-club club_latest_match_date cutoff.

    Division 1 here has 3 teams (A1, A2, B1). Club A hosts 4 mutually-conflicting
    fixtures (A1 v A2, A2 v A1, A1 v B1, A2 v B1 -- any two share a team), so club
    A's home_dates below has slack (6 dates) beyond that minimum of 4, leaving room
    for a cutoff to remove some and still solve.
    """

    def _params(
        self, *, b_home_dates: list[date] | None = None, **kwargs: Any
    ) -> fmodel.Parameters:
        teams = [
            fmodel.Team(division=1, club="A", index=1),
            fmodel.Team(division=1, club="A", index=2),
            fmodel.Team(division=1, club="B", index=1),
        ]
        home_dates = {
            "A": [
                date(2025, 1, 1),
                date(2025, 1, 8),
                date(2025, 2, 1),
                date(2025, 2, 8),
                date(2025, 3, 1),
                date(2025, 3, 8),
            ],
            "B": b_home_dates or [date(2025, 5, 1), date(2025, 6, 1)],
        }
        return _params(
            teams=teams,
            home_dates=home_dates,
            unavailable_away_dates={"A": [], "B": []},
            max_concurrent_matches={"A": _home_limit(2), "B": _home_limit(1)},
            min_gap_days=7,
            **kwargs,
        )

    # B offers both early dates (playable within A's cutoff) and late ones, so the
    # scenario stays feasible and the tests can check that A's cutoff pulls the
    # B-hosted fixtures against A onto the early dates.
    _B_EARLY_AND_LATE = [
        date(2025, 1, 15),
        date(2025, 1, 22),
        date(2025, 5, 1),
        date(2025, 6, 1),
    ]

    def test_cutoff_limits_every_fixture_the_club_is_in(self) -> None:
        """A cutoff on club A keeps every fixture involving an A team -- hosted by
        A or not -- on or before it."""
        params = self._params(
            b_home_dates=self._B_EARLY_AND_LATE,
            club_latest_match_date={"A": date(2025, 2, 8)},
        )
        fixtures = list(fmodel.solve(params).fixtures)
        a_involved = [
            sf
            for sf in fixtures
            if "A" in (sf.fixture.home_team.club, sf.fixture.away_team.club)
        ]
        self.assertEqual(len(a_involved), 6)  # 4 A-hosted + B1 v A1 + B1 v A2
        for sf in a_involved:
            self.assertLessEqual(sf.date, date(2025, 2, 8))

    def test_cutoff_also_blocks_the_club_playing_away(self) -> None:
        """B1 v A1 and B1 v A2 are hosted by B, which offers May/June dates, but
        A's cutoff still forces them onto B's early dates."""
        params = self._params(
            b_home_dates=self._B_EARLY_AND_LATE,
            club_latest_match_date={"A": date(2025, 2, 8)},
        )
        fixtures = list(fmodel.solve(params).fixtures)
        b_hosted = [sf for sf in fixtures if sf.fixture.home_team.club == "B"]
        self.assertEqual(len(b_hosted), 2)
        for sf in b_hosted:
            self.assertLessEqual(sf.date, date(2025, 2, 8))

    def test_cutoff_after_a_needed_away_date_makes_it_infeasible(self) -> None:
        """With B hosting only after A's cutoff, B1 v A1 / B1 v A2 have no
        schedulable date -- required fixtures, so solve() raises."""
        params = self._params(club_latest_match_date={"A": date(2025, 2, 8)})
        with self.assertRaisesRegex(ValueError, "no schedulable date"):
            fmodel.solve(params)

    def test_other_clubs_unaffected(self) -> None:
        """A cutoff on A doesn't constrain a fixture between two non-A teams."""
        teams = [
            fmodel.Team(division=1, club="A", index=1),
            fmodel.Team(division=1, club="B", index=1),
            fmodel.Team(division=1, club="C", index=1),
        ]
        params = _params(
            teams=teams,
            home_dates={
                "A": [date(2025, 1, 1), date(2025, 1, 15)],
                # Early dates keep A's away legs at B/C schedulable within its
                # cutoff; the late dates carry the B-C fixtures.
                "B": [date(2025, 1, 8), date(2025, 6, 1)],
                "C": [date(2025, 1, 22), date(2025, 6, 8)],
            },
            unavailable_away_dates={"A": [], "B": [], "C": []},
            min_gap_days=7,
            club_latest_match_date={"A": date(2025, 2, 1)},
        )
        fixtures = list(fmodel.solve(params).fixtures)
        bc = [
            sf
            for sf in fixtures
            if {sf.fixture.home_team.club, sf.fixture.away_team.club} == {"B", "C"}
        ]
        self.assertEqual(len(bc), 2)
        for sf in bc:
            self.assertGreater(sf.date, date(2025, 2, 1))

    def test_no_cutoff_by_default(self) -> None:
        params = self._params()
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertTrue(any(sf.date > date(2025, 4, 1) for sf in fixtures))

    def test_fixed_fixture_after_cutoff_rejected(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixed = fmodel.ScheduledFixture(
            fixture=fmodel.Fixture(home_team=a1, away_team=a2), date=date(2025, 3, 8)
        )
        params = self._params(
            fixed_fixtures=[fixed], club_latest_match_date={"A": date(2025, 2, 8)}
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)


class TestEarliestMatchDate(unittest.TestCase):
    """Test cases for the earliest_match_date constraint.

    Division 1 here has only 3 teams (A1, A2, B1), so every pair of the 4
    fixtures that club A hosts (A1 v A2, A2 v A1, A1 v B1, A2 v B1) shares a
    team -- with only 3 teams to draw from, any two of those 4 fixtures must
    involve at least one of the same two teams. That means all 4 need distinct
    dates regardless of max_concurrent_matches, so club A's home_dates
    below deliberately has some slack (6 dates) beyond that minimum of 4, to
    leave room for a cutoff to remove some of them and still solve.
    """

    def _params(self, **kwargs: Any) -> fmodel.Parameters:
        teams = [
            fmodel.Team(division=1, club="A", index=1),
            fmodel.Team(division=1, club="A", index=2),
            fmodel.Team(division=1, club="B", index=1),
        ]
        home_dates = {
            "A": [
                date(2025, 1, 1),
                date(2025, 1, 8),
                date(2025, 2, 1),
                date(2025, 2, 8),
                date(2025, 3, 1),
                date(2025, 3, 8),
            ],
            "B": [date(2025, 5, 1), date(2025, 6, 1)],
        }
        return _params(
            teams=teams,
            home_dates=home_dates,
            unavailable_away_dates={"A": [], "B": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "B": _home_limit(1),
            },
            min_gap_days=7,
            **kwargs,
        )

    def test_new_fixtures_respect_the_cutoff(self) -> None:
        """A cutoff that excludes some (but not all) of club A's dates still
        leaves enough of them (4, the minimum -- see class docstring) to solve."""
        params = self._params(earliest_match_date=date(2025, 2, 1))
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertTrue(fixtures)
        for sf in fixtures:
            self.assertGreaterEqual(sf.date, date(2025, 2, 1))

    def test_cutoff_after_all_of_a_clubs_home_dates_makes_the_solve_infeasible(
        self,
    ) -> None:
        """A cutoff after all of club A's home dates makes every A-hosted fixture
        unschedulable. Those fixtures are still required, so solve() raises rather
        than returning a schedule that silently omits them -- an already-played
        match belongs in fixed_fixtures (which bypass earliest_match_date), not
        inferred from a truncated result."""
        params = self._params(earliest_match_date=date(2025, 4, 1))
        with self.assertRaisesRegex(ValueError, "no schedulable date"):
            fmodel.solve(params)

    def test_fixed_fixture_before_cutoff_still_solves(self) -> None:
        """Unlike latest_internal_match_date, a fixed fixture dated before the cutoff
        is not rejected: fixed_fixtures represents matches that are already
        committed (possibly already played), so an old date there is expected --
        this is what lets the solver be re-run after some home dates have passed
        without breaking on fixtures already fixed to those dates.
        """
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixed = fmodel.ScheduledFixture(
            fixture=fmodel.Fixture(home_team=a1, away_team=a2), date=date(2025, 1, 1)
        )
        params = self._params(
            fixed_fixtures=[fixed], earliest_match_date=date(2025, 2, 1)
        )
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertIn(fixed, fixtures)

    def test_no_cutoff_by_default(self) -> None:
        """Without earliest_match_date, all 6 fixtures solve as normal (the tight
        4-distinct-A-dates requirement from the class docstring, with no cutoff
        trimming club A's 6 candidate dates, is comfortably satisfiable)."""
        params = self._params()
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertEqual(len(fixtures), 6)


class TestExcludedFixtures(unittest.TestCase):
    """Test cases for the excluded_fixtures parameter."""

    def _params(self, **kwargs: Any) -> fmodel.Parameters:
        teams = [
            fmodel.Team(division=1, club="A", index=1),
            fmodel.Team(division=1, club="A", index=2),
            fmodel.Team(division=1, club="B", index=1),
        ]
        home_dates = {
            "A": [
                date(2025, 1, 1),
                date(2025, 2, 1),
                date(2025, 3, 1),
                date(2025, 4, 1),
            ],
            "B": [date(2025, 5, 1), date(2025, 6, 1)],
        }
        return _params(
            teams=teams,
            home_dates=home_dates,
            unavailable_away_dates={"A": [], "B": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "B": _home_limit(1),
            },
            min_gap_days=7,
            **kwargs,
        )

    def test_excluded_fixture_is_not_scheduled(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        excluded_fixture = fmodel.Fixture(home_team=a1, away_team=a2)
        params = self._params(excluded_fixtures=[excluded_fixture])
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertNotIn(excluded_fixture, [sf.fixture for sf in fixtures])
        # The other 5 fixtures in this 3-team division must still be scheduled
        self.assertEqual(len(fixtures), 5)

    def test_exclusion_is_directional(self) -> None:
        """Excluding A1 v A2 must not also exclude the reverse fixture A2 v A1."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        excluded_fixture = fmodel.Fixture(home_team=a1, away_team=a2)
        params = self._params(excluded_fixtures=[excluded_fixture])
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertTrue(
            any(
                sf.fixture.home_team == a2 and sf.fixture.away_team == a1
                for sf in fixtures
            )
        )

    def test_fixed_and_excluded_conflict_rejected(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixture = fmodel.Fixture(home_team=a1, away_team=a2)
        fixed = fmodel.ScheduledFixture(fixture=fixture, date=date(2025, 1, 1))
        params = self._params(fixed_fixtures=[fixed], excluded_fixtures=[fixture])
        with self.assertRaises(ValueError):
            fmodel.solve(params)


class TestDivisionSchemes(unittest.TestCase):
    """Test cases for division_schemes and the fmodel.Division view Parameters
    derives from it: DOUBLE_ROUND (the default for any unlisted division) vs
    SINGLE_ROUND, the latter taking each match's home/away side from the Berger
    table for that division's teams in Parameters.teams order.
    """

    def _params(
        self,
        teams: list[fmodel.Team],
        division_schemes: dict[int, fmodel.FixtureScheme] | None = None,
    ) -> fmodel.Parameters:
        clubs = sorted({t.club for t in teams})
        # A generous common pool of home dates, > min_gap_days apart, so date
        # feasibility never masks a wrong fixture count.
        dates = [date(2025, 1, 1) + timedelta(days=7 * i) for i in range(20)]
        return _params(
            teams=teams,
            home_dates={c: list(dates) for c in clubs},
            unavailable_away_dates={c: [] for c in clubs},
            max_concurrent_matches={c: _home_limit(1) for c in clubs},
            min_gap_days=7,
            division_schemes=division_schemes or {},
        )

    @staticmethod
    def _teams(clubs: str, division: int = 1) -> list[fmodel.Team]:
        return [fmodel.Team(division=division, club=c, index=1) for c in clubs]

    def test_double_round_is_the_default_scheme(self) -> None:
        params = self._params(self._teams("ABCD"))
        fixtures = list(fmodel.solve(params).fixtures)
        # 4 teams, home and away: 4 * 3 = 12 fixtures.
        self.assertEqual(12, len(fixtures))
        self.assertEqual(1, len(params.divisions))
        self.assertEqual(fmodel.FixtureScheme.DOUBLE_ROUND, params.divisions[0].scheme)

    def test_single_round_plays_each_pair_once(self) -> None:
        params = self._params(
            self._teams("ABCD"),
            division_schemes={1: fmodel.FixtureScheme.SINGLE_ROUND},
        )
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertEqual(6, len(fixtures))
        unordered = {
            frozenset((sf.fixture.home_team, sf.fixture.away_team)) for sf in fixtures
        }
        self.assertEqual(6, len(unordered))

    def test_single_round_home_away_follows_the_berger_table(self) -> None:
        teams = self._teams("ABCD")
        params = self._params(
            teams, division_schemes={1: fmodel.FixtureScheme.SINGLE_ROUND}
        )
        fixtures = list(fmodel.solve(params).fixtures)
        got = {
            (sf.fixture.home_team.club, sf.fixture.away_team.club) for sf in fixtures
        }
        expected = {
            (home.club, away.club) for home, away in berger.single_round_pairings(teams)
        }
        self.assertEqual(expected, got)

    def test_single_round_draw_order_comes_from_parameters_teams(self) -> None:
        # teams order D, C, B, A: the Berger draw, and every H/A, follows it.
        teams = self._teams("DCBA")
        params = self._params(
            teams, division_schemes={1: fmodel.FixtureScheme.SINGLE_ROUND}
        )
        fixtures = list(fmodel.solve(params).fixtures)
        got = {
            (sf.fixture.home_team.club, sf.fixture.away_team.club) for sf in fixtures
        }
        expected = {
            (home.club, away.club) for home, away in berger.single_round_pairings(teams)
        }
        self.assertEqual(expected, got)
        # Round 1 of the Berger table pairs position 1 at home against position n:
        # here that's D (first in teams) hosting A (last in teams).
        self.assertIn(("D", "A"), got)

    def test_schemes_are_per_division(self) -> None:
        teams = self._teams("ABCD", division=1) + self._teams("EFGH", division=2)
        params = self._params(
            teams, division_schemes={1: fmodel.FixtureScheme.SINGLE_ROUND}
        )
        fixtures = list(fmodel.solve(params).fixtures)
        by_division = collections.Counter(
            sf.fixture.home_team.division for sf in fixtures
        )
        self.assertEqual(6, by_division[1])  # single round
        self.assertEqual(12, by_division[2])  # double round (the default)

    def test_scheme_for_division_with_no_teams_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "division.*9.*no teams"):
            self._params(
                self._teams("ABCD"),
                division_schemes={9: fmodel.FixtureScheme.SINGLE_ROUND},
            )


class TestTeamConstraints(unittest.TestCase):
    """Test cases for team_home_dates and team_unavailable_away_dates: per-team
    overrides/additions to a club's home_dates/unavailable_away_dates, for clubs
    whose teams don't all share the same availability.
    """

    def test_team_home_dates_override_restricts_that_team_only(self) -> None:
        """A1 has a narrower home_dates override than club A; A2 (no override) can
        still use A's full home_dates list."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        a_home_dates = [date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)]
        params = _params(
            teams=[a1, a2],
            home_dates={"A": a_home_dates},
            unavailable_away_dates={"A": []},
            max_concurrent_matches={
                "A": _home_limit(1),
            },
            min_gap_days=7,
            # A1 can only host on Jan 1; A2 keeps A's full home_dates.
            team_home_dates={a1: [date(2025, 1, 1)]},
        )
        fixtures = list(fmodel.solve(params).fixtures)
        a1_home_dates_used = {sf.date for sf in fixtures if sf.fixture.home_team == a1}
        self.assertEqual(a1_home_dates_used, {date(2025, 1, 1)})
        a2_home_dates_used = {sf.date for sf in fixtures if sf.fixture.home_team == a2}
        self.assertTrue(a2_home_dates_used.issubset(set(a_home_dates)))
        self.assertTrue(a2_home_dates_used - {date(2025, 1, 1)})

    def test_team_unavailable_away_dates_is_additive(self) -> None:
        """A1's team-specific unavailable_away_dates blocks it from playing away on
        one of B's home dates, even though club A has no such club-wide
        restriction: B1 v A1 must then use B's other home date."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        b1 = fmodel.Team(division=1, club="B", index=1)
        params = _params(
            teams=[a1, b1],
            home_dates={
                "A": [date(2025, 1, 1)],
                "B": [date(2025, 2, 1), date(2025, 2, 15)],
            },
            unavailable_away_dates={"A": [], "B": []},
            max_concurrent_matches={
                "A": _home_limit(1),
                "B": _home_limit(1),
            },
            min_gap_days=7,
            team_unavailable_away_dates={a1: [date(2025, 2, 1)]},
        )
        fixtures = list(fmodel.solve(params).fixtures)
        b_hosted = next(
            sf
            for sf in fixtures
            if sf.fixture.home_team == b1 and sf.fixture.away_team == a1
        )
        # Feb 1 is blocked for A1 specifically, so B1 v A1 falls on Feb 15.
        self.assertEqual(b_hosted.date, date(2025, 2, 15))

    def test_team_without_override_falls_back_to_club(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        params = _params(
            teams=[a1],
            home_dates={"A": [date(2025, 1, 1), date(2025, 2, 1)]},
            unavailable_away_dates={"A": []},
            max_concurrent_matches={"A": _home_limit(1)},
        )
        self.assertEqual(
            params.home_dates_for(a1), [date(2025, 1, 1), date(2025, 2, 1)]
        )
        self.assertEqual(params.unavailable_away_dates_for(a1), set())


class TestMatchCountLimits(unittest.TestCase):
    """Test cases for RollingLimit: no window of window_days consecutive days may
    hold more than `limit` matches involving a given set of teams (two dates
    exactly window_days apart fall in separate windows).
    """

    def test_window_days_defaults_to_one(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        self.assertEqual(
            fmodel.RollingLimit(teams=[a1, a2], match_cap=fmodel.Cap(1)).window_days, 1
        )

    def test_venue_scope_defaults_to_all(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        self.assertEqual(
            fmodel.RollingLimit(teams=[a1, a2], match_cap=fmodel.Cap(1)).venue_scope,
            fmodel.VenueScope.ALL,
        )

    def test_forces_different_dates_for_constrained_teams(self) -> None:
        """A1 and A2 are in different divisions (so have no fixture between them
        directly forcing a gap) and could otherwise both be scheduled at home on the
        same one of A's two shared home dates; the coscheduling constraint forces
        them apart. (X gets two well-separated home dates too, since the constraint
        covers *all* of A1/A2's matches, home or away -- see
        test_also_applies_to_away_matches -- not just their home ones; with only one
        X date, A1 and A2's away legs would collide there instead.)"""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1), date(2025, 1, 8)],
                "X": [date(2025, 3, 1), date(2025, 3, 8)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "X": _home_limit(2),
            },
            min_gap_days=7,
            match_count_limits=[
                fmodel.RollingLimit(teams=[a1, a2], match_cap=fmodel.Cap(1))
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        a1_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a1)
        a2_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a2)
        self.assertNotEqual(a1_home_date, a2_home_date)

    def test_also_applies_to_away_matches(self) -> None:
        """The constraint covers every match involving a constrained team, not just
        its home ones -- so if X only has one home date, A1 and A2 can't both play
        their away leg there, even though neither is hosting."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1), date(2025, 1, 8)],
                "X": [date(2025, 3, 1)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "X": _home_limit(2),
            },
            min_gap_days=7,
            match_count_limits=[
                fmodel.RollingLimit(teams=[a1, a2], match_cap=fmodel.Cap(1))
            ],
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_infeasible_when_only_one_shared_date_available(self) -> None:
        """Same setup as test_forces_different_dates_for_constrained_teams, but A
        only has one home date -- both A1 and A2 need it for their home leg, and the
        coscheduling constraint forbids them sharing it. (X still gets two dates, to
        isolate this from the away-match interaction covered by
        test_also_applies_to_away_matches.)"""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1)],
                "X": [date(2025, 3, 1), date(2025, 3, 8)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "X": _home_limit(2),
            },
            min_gap_days=7,
            match_count_limits=[
                fmodel.RollingLimit(teams=[a1, a2], match_cap=fmodel.Cap(1))
            ],
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_time_window_days_forbids_shorter_gaps(self) -> None:
        """With time_window_days=3 and max_matches=1, A1 and A2's home matches can't share
        any 3-consecutive-day window -- of A's three candidate dates (Jan 1, 3, 10),
        the only forbidden pairing is Jan 1 / Jan 3 (2 days apart), so the solver
        must pick a pairing that involves Jan 10. (X's two home dates, for A1/A2's
        away legs, are far enough apart not to introduce a second conflict of their
        own.)"""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1), date(2025, 1, 3), date(2025, 1, 10)],
                "X": [date(2025, 3, 1), date(2025, 3, 8)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "X": _home_limit(2),
            },
            min_gap_days=7,
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[a1, a2], match_cap=fmodel.Cap(1), window_days=3
                )
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        a1_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a1)
        a2_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a2)
        self.assertGreaterEqual(abs((a1_home_date - a2_home_date).days), 3)

    def test_time_window_days_allows_exact_gap(self) -> None:
        """time_window_days=N counts a run of N consecutive days, not a gap between
        endpoints: two dates exactly N days apart fall in separate windows. With
        time_window_days=7, max_matches=1 and A's only two home dates exactly a week apart
        (Jan 1 and Jan 8), A1 and A2 must take one each -- the schedule stays
        feasible. An off-by-one that put a 7-day span in one window would make this
        infeasible."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1), date(2025, 1, 8)],
                "X": [date(2025, 3, 1), date(2025, 3, 15)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "X": _home_limit(2),
            },
            min_gap_days=7,
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[a1, a2], match_cap=fmodel.Cap(1), window_days=7
                )
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        a1_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a1)
        a2_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a2)
        self.assertEqual(
            {a1_home_date, a2_home_date}, {date(2025, 1, 1), date(2025, 1, 8)}
        )

    def test_direct_fixture_between_constrained_teams_not_double_counted(self) -> None:
        """A single match between two constrained teams should count once towards
        the <=1 limit, not twice -- if the two teams' variables were combined
        naively (e.g. by concatenating each team's own match list) rather than by
        filtering the single var per (fixture, date), this fixture's variable would
        be counted twice and wrongly made unschedulable."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        params = _params(
            teams=[a1, a2],
            home_dates={"A": [date(2025, 1, 1)]},
            unavailable_away_dates={"A": []},
            max_concurrent_matches={"A": _home_limit(1)},
            min_gap_days=7,
            excluded_fixtures=[fmodel.Fixture(home_team=a2, away_team=a1)],
            match_count_limits=[
                fmodel.RollingLimit(teams=[a1, a2], match_cap=fmodel.Cap(1))
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertEqual(
            fixtures,
            [
                fmodel.ScheduledFixture(
                    fixture=fmodel.Fixture(home_team=a1, away_team=a2),
                    date=date(2025, 1, 1),
                )
            ],
        )

    def test_applies_to_away_allows_shared_home_date(self) -> None:
        """With applies_to=AWAY the constraint ignores home matches: A1 and A2 may
        both host on A's single home date (which BOTH would forbid -- cf
        test_infeasible_when_only_one_shared_date_available), while X's two dates keep
        their away legs apart."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1)],
                "X": [date(2025, 3, 1), date(2025, 3, 8)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "X": _home_limit(2),
            },
            min_gap_days=7,
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[a1, a2],
                    match_cap=fmodel.Cap(1),
                    venue_scope=fmodel.VenueScope.AWAY,
                )
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        a1_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a1)
        a2_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a2)
        self.assertEqual(a1_home_date, a2_home_date)

    def test_applies_to_away_still_separates_away_legs(self) -> None:
        """applies_to=AWAY still constrains away matches: with only one X home date,
        A1 and A2 can't both play their away leg there."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1), date(2025, 1, 8)],
                "X": [date(2025, 3, 1)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "X": _home_limit(2),
            },
            min_gap_days=7,
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[a1, a2],
                    match_cap=fmodel.Cap(1),
                    venue_scope=fmodel.VenueScope.AWAY,
                )
            ],
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_applies_to_home_ignores_away_collision(self) -> None:
        """With applies_to=HOME the constraint ignores away matches: A1 and A2 may
        both play their away leg on X's single home date, while A's two dates keep
        their home legs apart."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1), date(2025, 1, 8)],
                "X": [date(2025, 3, 1)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "X": _home_limit(2),
            },
            min_gap_days=7,
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[a1, a2],
                    match_cap=fmodel.Cap(1),
                    venue_scope=fmodel.VenueScope.HOME,
                )
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        a1_away_date = next(sf.date for sf in fixtures if sf.fixture.away_team == a1)
        a2_away_date = next(sf.date for sf in fixtures if sf.fixture.away_team == a2)
        self.assertEqual(a1_away_date, a2_away_date)

    def test_applies_to_home_still_separates_home_legs(self) -> None:
        """applies_to=HOME still constrains home matches: with only one A home date,
        A1 and A2 can't both host there."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1)],
                "X": [date(2025, 3, 1), date(2025, 3, 8)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={
                "A": _home_limit(2),
                "X": _home_limit(2),
            },
            min_gap_days=7,
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[a1, a2],
                    match_cap=fmodel.Cap(1),
                    venue_scope=fmodel.VenueScope.HOME,
                )
            ],
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def _three_a_teams_in_one_window(self, limit: int) -> fmodel.Parameters:
        """A1/A2/A3 each have exactly one fixture (their home leg; the reverse legs
        against X are excluded), and A's only three home dates all sit inside a
        single 30-day window. A match_count_limit of `limit` over that window then
        allows only `limit` of the three to be scheduled."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        a3 = fmodel.Team(division=3, club="A", index=3)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        x3 = fmodel.Team(division=3, club="X", index=3)
        return _params(
            teams=[a1, a2, a3, x1, x2, x3],
            home_dates={
                "A": [date(2025, 1, 1), date(2025, 1, 15), date(2025, 1, 29)],
                "X": [],
            },
            unavailable_away_dates={"A": [], "X": []},
            min_gap_days=7,
            excluded_fixtures=[
                fmodel.Fixture(home_team=x1, away_team=a1),
                fmodel.Fixture(home_team=x2, away_team=a2),
                fmodel.Fixture(home_team=x3, away_team=a3),
            ],
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[a1, a2, a3], match_cap=fmodel.Cap(limit), window_days=30
                )
            ],
        )

    def test_limit_above_one_still_binds_when_exceeded(self) -> None:
        """limit=2 over the shared window leaves nowhere for the third of A1/A2/A3's
        fixtures -> infeasible."""
        with self.assertRaises(ValueError):
            fmodel.solve(self._three_a_teams_in_one_window(limit=2))

    def test_limit_above_one_permits_up_to_the_limit(self) -> None:
        """limit=3 admits all three of A1/A2/A3's fixtures in the same window."""
        fixtures = list(
            fmodel.solve(self._three_a_teams_in_one_window(limit=3)).fixtures
        )
        self.assertEqual(len(fixtures), 3)


class TestMaxPlayingTeams(unittest.TestCase):
    """Test cases for MatchCountLimit.playing_teams_cap: unlike match_cap, which
    counts matches, this counts the *distinct* teams from `teams` that play (home or
    away) within a window. A same-club derby is one match (one candidate variable)
    but puts two of the club's own teams on to play, so it counts as 1 towards
    match_cap but 2 towards playing_teams_cap -- guarding against exactly the case a
    plain match_cap misses: N matches, one of them an internal derby, needing N+1
    teams' worth of players.
    """

    def test_internal_derby_alone_blocked_by_a_playing_teams_cap_of_one(self) -> None:
        """A single derby match between two of a club's own teams already needs two
        of them playing -- so a playing_teams_cap of 1 makes every candidate date
        infeasible, even though it's only one match (well within match_cap). Unlike
        a plain match_cap of 0 (which prunes the candidate variables entirely, see
        MatchCountLimit.forbids), this isn't detectable per-candidate -- it only
        shows up as a genuine solver infeasibility."""
        h1 = fmodel.Team(division=1, club="H", index=1)
        h2 = fmodel.Team(division=1, club="H", index=2)
        params = _params(
            teams=[h1, h2],
            home_dates={"H": [date(2025, 1, 1), date(2025, 1, 8)]},
            unavailable_away_dates={"H": []},
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[h1, h2],
                    match_cap=fmodel.Cap(2),
                    playing_teams_cap=fmodel.Cap(1),
                    venue_scope=fmodel.VenueScope.HOME,
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "INFEASIBLE"):
            fmodel.solve(params)

    def test_venue_capacity_spreads_an_internal_derby_off_a_full_night(self) -> None:
        """Mirrors the motivating case: a club hosts up to 3 matches a night
        (max_matches: 3) but only has 3 teams' worth of players to field
        (max_playing_teams: 3). Two of its teams (H3, H4) are only free to host on
        one shared date; adding either leg of the H1-v-H2 derby there too would need
        a 4th team's worth of players, so the solver must push both legs onto H1/H2's
        other two shared dates instead (one leg per date, since -- regardless of this
        cap -- a team may only play once per date), even though max_matches alone
        (2 + 1 = 3) would have allowed combining a leg with H3/H4."""
        h1 = fmodel.Team(division=1, club="H", index=1)
        h2 = fmodel.Team(division=1, club="H", index=2)
        h3 = fmodel.Team(division=2, club="H", index=3)
        h4 = fmodel.Team(division=3, club="H", index=4)
        x1 = fmodel.Team(division=2, club="X", index=1)
        y1 = fmodel.Team(division=3, club="Y", index=1)
        shared_date = date(2025, 1, 1)
        params = _params(
            teams=[h1, h2, h3, h4, x1, y1],
            home_dates={
                "H": [shared_date, date(2025, 1, 8), date(2025, 1, 15)],
                "X": [date(2025, 3, 1)],
                "Y": [date(2025, 3, 8)],
            },
            unavailable_away_dates={"H": [], "X": [], "Y": []},
            team_home_dates={h3: [shared_date], h4: [shared_date]},
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[h1, h2, h3, h4],
                    match_cap=fmodel.Cap(3),
                    playing_teams_cap=fmodel.Cap(3),
                    venue_scope=fmodel.VenueScope.HOME,
                )
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        derby_dates = {
            sf.date
            for sf in fixtures
            if {sf.fixture.home_team, sf.fixture.away_team} == {h1, h2}
        }
        self.assertNotIn(shared_date, derby_dates)

    def test_cap_at_or_above_group_size_is_a_no_op(self) -> None:
        """A max_playing_teams cap that can never bind (>= the group size) leaves the
        derby free to land on either date -- unlike the test above, shared_date is
        not excluded."""
        h1 = fmodel.Team(division=1, club="H", index=1)
        h2 = fmodel.Team(division=1, club="H", index=2)
        params = _params(
            teams=[h1, h2],
            home_dates={"H": [date(2025, 1, 1), date(2025, 1, 8)]},
            unavailable_away_dates={"H": []},
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[h1, h2],
                    match_cap=fmodel.Cap(2),
                    playing_teams_cap=fmodel.Cap(2),
                    venue_scope=fmodel.VenueScope.HOME,
                )
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertEqual(len(fixtures), 2)

    def test_zero_forbids_the_candidate_up_front_like_max_matches_zero(self) -> None:
        """Unlike a cap of 1 or more, a playing_teams_cap of 0 is an up-front bar:
        any counted match puts at least one team from `teams` on to play, so it's
        detectable per-candidate (see MatchCountLimit.forbids) rather than only
        showing up as a solver infeasibility."""
        h1 = fmodel.Team(division=1, club="H", index=1)
        h2 = fmodel.Team(division=1, club="H", index=2)
        fix = fmodel.Fixture(home_team=h1, away_team=h2)
        blocked = fmodel.RollingLimit(
            teams=[h1, h2], match_cap=fmodel.Cap(2), playing_teams_cap=fmodel.Cap(0)
        )
        self.assertTrue(blocked.forbids(fix, date(2025, 1, 1)))

    def test_override_lifts_the_cap_on_a_specific_date(self) -> None:
        """max_playing_teams_overrides replaces the base cap on that one date only
        -- mirroring max_matches_overrides. A base cap of 1 blocks either derby leg
        everywhere on its own (each alone already needs 2 teams playing), but two
        overridden dates raise it to 2 -- enough for one leg apiece (a team may only
        play once per date, so the two legs can't share even an overridden date) --
        while a third, un-overridden date stays governed by the base cap and so is
        left unused."""
        h1 = fmodel.Team(division=1, club="H", index=1)
        h2 = fmodel.Team(division=1, club="H", index=2)
        overridden_date_1 = date(2025, 1, 1)
        overridden_date_2 = date(2025, 1, 8)
        plain_date = date(2025, 1, 15)
        params = _params(
            teams=[h1, h2],
            home_dates={"H": [overridden_date_1, overridden_date_2, plain_date]},
            unavailable_away_dates={"H": []},
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[h1, h2],
                    match_cap=fmodel.Cap(2),
                    playing_teams_cap=fmodel.Cap(
                        1,
                        {overridden_date_1: 2, overridden_date_2: 2},
                    ),
                    venue_scope=fmodel.VenueScope.HOME,
                )
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        derby_dates = {
            sf.date
            for sf in fixtures
            if {sf.fixture.home_team, sf.fixture.away_team} == {h1, h2}
        }
        self.assertEqual(derby_dates, {overridden_date_1, overridden_date_2})

    def test_overrides_reject_non_default_window_days(self) -> None:
        """Unlike playing_teams_cap itself (see the multi-day-window tests below,
        which show it's meaningful over a wider window), a Cap's per-date overrides
        are still tied to a single date: an override names one specific date, which
        only lines up with one specific window when window_days is 1."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        with self.assertRaises(ValueError):
            fmodel.RollingLimit(
                teams=[a1],
                match_cap=fmodel.Cap(1),
                window_days=7,
                playing_teams_cap=fmodel.Cap(overrides={date(2025, 1, 1): 1}),
            )

    def test_multi_day_window_counts_a_repeated_pair_twice(self) -> None:
        """Over a multi-day window, the same pair meeting twice counts towards
        max_playing_teams once per match, not once per team: two internal H1-v-H2
        matches within 7 days of each other would need 4 teams'-worth of players in
        that week (2 matches x 2 teams each), exceeding a cap of 3, even though only
        2 distinct teams are ever involved. H1 and H2 have two close-together dates
        and one far-apart one; the solver must use the far one for at least one leg
        to keep any 7-day window's tally at or under 3."""
        h1 = fmodel.Team(division=1, club="H", index=1)
        h2 = fmodel.Team(division=1, club="H", index=2)
        close_dates = {date(2025, 1, 1), date(2025, 1, 3)}
        far_date = date(2025, 1, 20)
        params = _params(
            teams=[h1, h2],
            home_dates={"H": sorted(close_dates | {far_date})},
            unavailable_away_dates={"H": []},
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[h1, h2],
                    playing_teams_cap=fmodel.Cap(3),
                    window_days=7,
                )
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        derby_dates = {sf.date for sf in fixtures}
        self.assertEqual(len(fixtures), 2)
        self.assertFalse(derby_dates <= close_dates)

    def test_date_ranges_counts_a_repeated_pair_twice(self) -> None:
        """The same property, using an explicit date_ranges window instead of a
        rolling time_window_days one."""
        h1 = fmodel.Team(division=1, club="H", index=1)
        h2 = fmodel.Team(division=1, club="H", index=2)
        in_range_dates = {date(2025, 1, 1), date(2025, 1, 3)}
        out_of_range_date = date(2025, 1, 20)
        params = _params(
            teams=[h1, h2],
            home_dates={"H": sorted(in_range_dates | {out_of_range_date})},
            unavailable_away_dates={"H": []},
            match_count_limits=[
                fmodel.RangeLimit(
                    teams=[h1, h2],
                    playing_teams_cap=fmodel.Cap(3),
                    ranges=(fmodel.DateRange(date(2025, 1, 1), date(2025, 1, 7)),),
                )
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        derby_dates = {sf.date for sf in fixtures}
        self.assertEqual(len(fixtures), 2)
        self.assertFalse(derby_dates <= in_range_dates)

    def test_exclude_dates_drops_a_date_from_the_window_tally(self) -> None:
        """`exclude_dates` makes every window ignore whatever counted matches fall
        on the listed dates -- unlike the *_overrides fields, it works over a
        multi-day rolling window. H1 and H2 (double_round, so two derby legs) have
        only two home dates two days apart: both legs land inside one 7-day window,
        needing 4 teams'-worth of players, over a max_playing_teams cap of 3. That
        is infeasible as-is; excluding one of the two dates drops its leg from the
        tally, leaving every window at 2 and letting both legs be scheduled."""
        h1 = fmodel.Team(division=1, club="H", index=1)
        h2 = fmodel.Team(division=1, club="H", index=2)
        d1 = date(2025, 1, 1)
        d2 = date(2025, 1, 3)

        def params_with(exclude_dates: frozenset[date]) -> fmodel.Parameters:
            return _params(
                teams=[h1, h2],
                home_dates={"H": [d1, d2]},
                unavailable_away_dates={"H": []},
                match_count_limits=[
                    fmodel.RollingLimit(
                        teams=[h1, h2],
                        playing_teams_cap=fmodel.Cap(3),
                        window_days=7,
                        exclude_dates=exclude_dates,
                    )
                ],
            )

        with self.assertRaisesRegex(ValueError, "INFEASIBLE"):
            fmodel.solve(params_with(frozenset()))

        fixtures = list(fmodel.solve(params_with(frozenset({d1}))).fixtures)
        self.assertEqual(len(fixtures), 2)

    # `exclude_dates` and explicit ranges are now structurally exclusive --
    # `exclude_dates` is a RollingLimit-only field, `ranges` a RangeLimit-only one
    # -- rather than a runtime check, so there is no MatchCountLimit-level rejection
    # test here any more. fixturespec_test.py still covers the YAML-level rejection
    # (see test_match_count_limits_exclude_dates_reject_date_ranges), since a YAML
    # mapping can still name both keys.


class TestMatchCountLimitDateRanges(unittest.TestCase):
    """A MatchCountLimit with explicit `date_ranges` caps matches within each
    listed inclusive range rather than within every rolling `time_window_days`
    window; matches outside every range are unaffected.
    """

    def _three_a_teams(
        self,
        *,
        a_home_dates: list[date],
        date_ranges: tuple[fmodel.DateRange, ...],
        limit: int,
    ) -> fmodel.Parameters:
        """A1/A2/A3 each have exactly one fixture (their home leg; the reverse
        legs against X are excluded), and A can host only one match a night. A
        `date_ranges` cap of `limit` then bounds how many of the three home legs
        may land inside the listed range(s)."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        a3 = fmodel.Team(division=3, club="A", index=3)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        x3 = fmodel.Team(division=3, club="X", index=3)
        return _params(
            teams=[a1, a2, a3, x1, x2, x3],
            home_dates={"A": a_home_dates, "X": []},
            unavailable_away_dates={"A": [], "X": []},
            min_gap_days=7,
            max_concurrent_matches={"A": _home_limit(1)},
            excluded_fixtures=[
                fmodel.Fixture(home_team=x1, away_team=a1),
                fmodel.Fixture(home_team=x2, away_team=a2),
                fmodel.Fixture(home_team=x3, away_team=a3),
            ],
            match_count_limits=[
                fmodel.RangeLimit(
                    teams=[a1, a2, a3], match_cap=fmodel.Cap(limit), ranges=date_ranges
                )
            ],
        )

    def test_cap_binds_inside_the_range_only(self) -> None:
        """The range covers January; A also has two February home dates. max_matches=1
        inside the range forces at least two of the three home legs into
        February, leaving at most one in January."""
        params = self._three_a_teams(
            a_home_dates=[
                date(2025, 1, 7),
                date(2025, 1, 14),
                date(2025, 2, 4),
                date(2025, 2, 11),
            ],
            date_ranges=(fmodel.DateRange(date(2025, 1, 1), date(2025, 1, 31)),),
            limit=1,
        )
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertEqual(len(fixtures), 3)
        in_january = [sf for sf in fixtures if sf.date.month == 1]
        self.assertLessEqual(len(in_january), 1)

    def test_infeasible_when_range_cap_leaves_nowhere(self) -> None:
        """Three in-range home dates plus one outside; with a one-a-night venue
        and max_matches=1 inside the range, only two of the three required home legs can
        be placed -> infeasible."""
        params = self._three_a_teams(
            a_home_dates=[
                date(2025, 1, 7),
                date(2025, 1, 14),
                date(2025, 1, 21),
                date(2025, 2, 4),
            ],
            date_ranges=(fmodel.DateRange(date(2025, 1, 1), date(2025, 1, 31)),),
            limit=1,
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_second_range_is_enforced_independently(self) -> None:
        """Two ranges, each capped at 1: A's home dates are two in January and
        two in March, so at most one leg per range -> the third leg has nowhere
        to go and the solve is infeasible."""
        params = self._three_a_teams(
            a_home_dates=[
                date(2025, 1, 7),
                date(2025, 1, 14),
                date(2025, 3, 4),
                date(2025, 3, 11),
            ],
            date_ranges=(
                fmodel.DateRange(date(2025, 1, 1), date(2025, 1, 31)),
                fmodel.DateRange(date(2025, 3, 1), date(2025, 3, 31)),
            ),
            limit=1,
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_max_zero_forces_matches_out_of_the_range(self) -> None:
        """max_matches=0 bars every counted match inside the range: A1's one home leg,
        with an in-range and an out-of-range home date available, is forced onto
        the out-of-range one."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        x1 = fmodel.Team(division=1, club="X", index=1)
        params = _params(
            teams=[a1, x1],
            home_dates={"A": [date(2025, 1, 7), date(2025, 2, 4)], "X": []},
            unavailable_away_dates={"A": [], "X": []},
            min_gap_days=7,
            excluded_fixtures=[fmodel.Fixture(home_team=x1, away_team=a1)],
            match_count_limits=[
                fmodel.RangeLimit(
                    teams=[a1],
                    match_cap=fmodel.Cap(0),
                    ranges=(fmodel.DateRange(date(2025, 1, 1), date(2025, 1, 31)),),
                )
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertEqual([sf.date for sf in fixtures], [date(2025, 2, 4)])

    def test_single_team_set_range_cap_still_binds(self) -> None:
        """The 'a shared cap >= group size can't bind' shortcut must not fire for
        a date-range rule: one team can play more than once inside a multi-day
        range. A1 has two required home legs, both home dates inside the range
        and none outside it, so a cap of 1 makes the solve infeasible."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        x1 = fmodel.Team(division=1, club="X", index=1)
        y1 = fmodel.Team(division=1, club="Y", index=1)
        params = _params(
            teams=[a1, x1, y1],
            home_dates={"A": [date(2025, 1, 7), date(2025, 1, 14)], "X": [], "Y": []},
            unavailable_away_dates={"A": [], "X": [], "Y": []},
            min_gap_days=7,
            excluded_fixtures=[
                fmodel.Fixture(home_team=x1, away_team=a1),
                fmodel.Fixture(home_team=y1, away_team=a1),
                fmodel.Fixture(home_team=x1, away_team=y1),
                fmodel.Fixture(home_team=y1, away_team=x1),
            ],
            match_count_limits=[
                fmodel.RangeLimit(
                    teams=[a1],
                    match_cap=fmodel.Cap(1),
                    ranges=(fmodel.DateRange(date(2025, 1, 1), date(2025, 1, 31)),),
                )
            ],
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    # There is no longer a "RangeLimit rejects a non-default window_days" test:
    # RangeLimit has no window_days field at all (windows come from `ranges`), so
    # the combination is now a structural impossibility rather than a runtime
    # check. fixturespec_test.py still covers the YAML-level rejection (see
    # test_match_count_limits_date_ranges_reject_time_window_days), since a YAML
    # mapping can still name both keys.

    def test_rejects_cap_overrides(self) -> None:
        """A RangeLimit's caps carry no per-date overrides (an override only lines
        up with a single-date rolling window) -- leave the date out of `ranges`
        instead."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        with self.assertRaises(ValueError):
            fmodel.RangeLimit(
                teams=[a1],
                match_cap=fmodel.Cap(1, {date(2025, 1, 1): 1}),
                ranges=(fmodel.DateRange(date(2025, 1, 1), date(2025, 1, 31)),),
            )

    def test_date_range_rejects_start_after_end(self) -> None:
        with self.assertRaises(ValueError):
            fmodel.DateRange(date(2025, 2, 1), date(2025, 1, 1))

    def test_date_range_contains_is_inclusive(self) -> None:
        rng = fmodel.DateRange(date(2025, 1, 1), date(2025, 1, 31))
        self.assertIn(date(2025, 1, 1), rng)
        self.assertIn(date(2025, 1, 31), rng)
        self.assertNotIn(date(2025, 2, 1), rng)

    def test_max_zero_prunes_candidate_var(self) -> None:
        """A max_matches: 0 limit makes its candidates a certain zero, so _build_model
        never creates a decision variable for them -- observable because a
        fixed_fixtures entry pinned onto such a date then fails with the
        descriptive 'not schedulable on that date' error (the fixture is still
        placeable on its other, unbarred home date, so this is the fixed-fixture
        lookup failing, not the whole fixture being unschedulable)."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        x1 = fmodel.Team(division=1, club="X", index=1)
        params = _params(
            teams=[a1, x1],
            home_dates={
                "A": [date(2025, 12, 29), date(2026, 1, 19)],
                "X": [date(2026, 1, 12)],
            },
            unavailable_away_dates={"A": [], "X": []},
            min_gap_days=7,
            fixed_fixtures=[
                fmodel.ScheduledFixture(
                    fmodel.Fixture(home_team=a1, away_team=x1), date(2025, 12, 29)
                )
            ],
            match_count_limits=[
                fmodel.RangeLimit(
                    teams=[a1, x1],
                    match_cap=fmodel.Cap(0),
                    ranges=(fmodel.DateRange(date(2025, 12, 22), date(2026, 1, 4)),),
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "not schedulable on that date"):
            fmodel.solve(params)

    def test_forbids_respects_venue_scope_and_range(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        x1 = fmodel.Team(division=1, club="X", index=1)
        home_fix = fmodel.Fixture(home_team=a1, away_team=x1)
        away_fix = fmodel.Fixture(home_team=x1, away_team=a1)
        rng = fmodel.DateRange(date(2025, 12, 22), date(2026, 1, 4))
        away_only = fmodel.RangeLimit(
            teams=[a1],
            match_cap=fmodel.Cap(0),
            venue_scope=fmodel.VenueScope.AWAY,
            ranges=(rng,),
        )
        # AWAY scope: bars a1's away fixture in the range, not its home one.
        self.assertTrue(away_only.forbids(away_fix, date(2025, 12, 29)))
        self.assertFalse(away_only.forbids(home_fix, date(2025, 12, 29)))
        # Outside the range: not forbidden.
        self.assertFalse(away_only.forbids(away_fix, date(2026, 1, 5)))
        # A non-zero cap is never an up-front bar.
        keep = fmodel.RangeLimit(teams=[a1], match_cap=fmodel.Cap(1), ranges=(rng,))
        self.assertFalse(keep.forbids(away_fix, date(2025, 12, 29)))


class TestPerClubGap(unittest.TestCase):
    """A per-team gap expressed per club: each club gets its own apply_per=EACH_TEAM
    RollingLimit, so one club can be given a closer gap than another."""

    def _params(self, gaps: dict[str, int]) -> fmodel.Parameters:
        """Two teams per club, each club's pair sharing a division so they play a
        double round against each other. Each club has exactly two home dates
        three days apart -- inside a 7-day gap, so a club given a 7-day window has
        nowhere to put its return leg."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        b1 = fmodel.Team(division=2, club="B", index=1)
        b2 = fmodel.Team(division=2, club="B", index=2)
        teams_by_club = {"A": [a1, a2], "B": [b1, b2]}
        return fmodel.Parameters(
            teams=[a1, a2, b1, b2],
            home_dates={
                "A": [date(2025, 1, 6), date(2025, 1, 9)],
                "B": [date(2025, 2, 3), date(2025, 2, 6)],
            },
            unavailable_away_dates={"A": [], "B": []},
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=teams_by_club[club],
                    match_cap=fmodel.Cap(1),
                    window_days=gap,
                    apply_per=fmodel.ApplyPer.EACH_TEAM,
                )
                for club, gap in gaps.items()
            ],
        )

    def test_seven_day_gap_for_both_is_infeasible(self) -> None:
        with self.assertRaises(ValueError):
            fmodel.solve(self._params({"A": 7, "B": 7}))

    def test_closer_gap_for_one_club_only_still_infeasible(self) -> None:
        """A gap of 2 for club A only: A's two fixtures fill A's two dates three
        days apart, while B keeps the 7-day gap, so the solve as a whole stays
        infeasible -- the gap is per club."""
        with self.assertRaises(ValueError):
            fmodel.solve(self._params({"A": 2, "B": 7}))

    def test_closer_gap_for_every_club_makes_it_feasible(self) -> None:
        fixtures = list(fmodel.solve(self._params({"A": 2, "B": 2})).fixtures)
        by_club_dates = collections.defaultdict(set)
        for sf in fixtures:
            by_club_dates[sf.fixture.home_team.club].add(sf.date)
        self.assertEqual(by_club_dates["A"], {date(2025, 1, 6), date(2025, 1, 9)})
        self.assertEqual(by_club_dates["B"], {date(2025, 2, 3), date(2025, 2, 6)})


class TestSharedHomeLimitNullAndOverrides(unittest.TestCase):
    """A shared home-scope MatchCountLimit with max_matches=None imposes no cap; a
    `max_matches_overrides` entry of None lifts it for that one date, an int replaces it.
    """

    def test_finite_default_makes_it_infeasible(self) -> None:
        """A has two teams (different divisions, so no direct fixture forces them
        apart) but only one home date; with a limit of 1, both teams needing to host
        on that one date is infeasible."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1)],
                "X": [date(2025, 3, 1), date(2025, 3, 8)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={
                "A": _home_limit(1),
                "X": _home_limit(2),
            },
            min_gap_days=7,
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_none_default_lifts_the_limit(self) -> None:
        """Same setup, but A's default is None -- both A1 and A2 can now host on
        their one shared date."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1)],
                "X": [date(2025, 3, 1), date(2025, 3, 8)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={
                "A": _home_limit(None),
                "X": _home_limit(2),
            },
            min_gap_days=7,
        )
        fixtures = list(fmodel.solve(params).fixtures)
        a1_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a1)
        a2_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a2)
        self.assertEqual(a1_home_date, date(2025, 1, 1))
        self.assertEqual(a2_home_date, date(2025, 1, 1))

    def test_none_override_lifts_the_limit_for_one_date_only(self) -> None:
        """A's default limit of 1 applies generally, but is overridden to None (no
        limit) specifically on 2025-01-01 -- so both A1 and A2 can host there, even
        though a finite default of 1 would otherwise forbid it."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1)],
                "X": [date(2025, 3, 1), date(2025, 3, 8)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={"X": _home_limit(2)},
            match_count_limits=(
                fmodel.RollingLimit(
                    teams=[a1, a2],
                    match_cap=fmodel.Cap(1, {date(2025, 1, 1): None}),
                    venue_scope=fmodel.VenueScope.HOME,
                ),
            ),
            min_gap_days=7,
        )
        fixtures = list(fmodel.solve(params).fixtures)
        a1_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a1)
        a2_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a2)
        self.assertEqual(a1_home_date, date(2025, 1, 1))
        self.assertEqual(a2_home_date, date(2025, 1, 1))


class TestSharedLimitAwayAndAllScopes(unittest.TestCase):
    """A shared-budget RollingLimit with venue_scope AWAY or ALL (HOME is covered
    by the classes above). Scenario: club A's two teams (different divisions, so no
    direct fixture) play their away legs at club X, whose single home date forces
    both onto the same night unless something says otherwise.
    """

    def _params(
        self,
        a_limit: fmodel.RollingLimit,
        *,
        x_home_dates: list[date],
    ) -> fmodel.Parameters:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        return _params(
            teams=[a1, a2, x1, x2],
            home_dates={"A": [date(2025, 3, 1)], "X": x_home_dates},
            unavailable_away_dates={"A": [], "X": []},
            # X can host both its teams' matches whenever it has the dates for them.
            match_count_limits=(dataclasses.replace(a_limit, teams=[a1, a2]),),
            min_gap_days=7,
        )

    def _any(self, limit: int | None) -> fmodel.RollingLimit:
        return fmodel.RollingLimit(
            teams=[], match_cap=fmodel.Cap(limit), venue_scope=fmodel.VenueScope.ALL
        )

    def _away(self, limit: int | None) -> fmodel.RollingLimit:
        return fmodel.RollingLimit(
            teams=[], match_cap=fmodel.Cap(limit), venue_scope=fmodel.VenueScope.AWAY
        )

    def test_any_scope_limit_blocks_two_matches_on_one_date(self) -> None:
        # X has one home date, so A1 and A2 must both play away that night; an ALL
        # limit of 1 forbids A playing two matches (home or away) on a date.
        params = self._params(self._any(1), x_home_dates=[date(2025, 1, 1)])
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_any_scope_limit_allows_up_to_the_limit(self) -> None:
        params = self._params(self._any(2), x_home_dates=[date(2025, 1, 1)])
        fixtures = list(fmodel.solve(params).fixtures)
        away = sorted(sf.date for sf in fixtures if sf.fixture.away_team.club == "A")
        self.assertEqual(away, [date(2025, 1, 1), date(2025, 1, 1)])

    def test_away_scope_limit_blocks_two_away_matches_on_one_date(self) -> None:
        params = self._params(self._away(1), x_home_dates=[date(2025, 1, 1)])
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_away_scope_does_not_restrict_home_matches(self) -> None:
        # With two X home dates the away legs fall on separate nights, satisfying
        # away: 1; A's own two home matches still share A's single home date,
        # because the AWAY scope doesn't count home matches.
        params = self._params(
            self._away(1), x_home_dates=[date(2025, 1, 1), date(2025, 1, 8)]
        )
        fixtures = list(fmodel.solve(params).fixtures)
        home = sorted(sf.date for sf in fixtures if sf.fixture.home_team.club == "A")
        self.assertEqual(home, [date(2025, 3, 1), date(2025, 3, 1)])
        away = sorted(sf.date for sf in fixtures if sf.fixture.away_team.club == "A")
        self.assertEqual(away, [date(2025, 1, 1), date(2025, 1, 8)])


class TestInternalMatchAwayScope(unittest.TestCase):
    """An internal match (both teams the same club) is played at that club's venue,
    so it is never counted under AWAY venue_scope: its "away" team isn't away in the
    sense an away-load cap is about. It still counts under HOME and ALL scope.
    """

    h1 = fmodel.Team(division=1, club="H", index=1)
    h2 = fmodel.Team(division=1, club="H", index=2)
    x1 = fmodel.Team(division=1, club="X", index=1)
    derby = fmodel.Fixture(home_team=h1, away_team=h2)
    away_at_x = fmodel.Fixture(home_team=x1, away_team=h1)

    def test_counts_fixture_skips_internal_derby_under_away_scope(self) -> None:
        away = fmodel.RollingLimit(
            teams=[self.h1, self.h2],
            match_cap=fmodel.Cap(1),
            venue_scope=fmodel.VenueScope.AWAY,
        )
        self.assertFalse(away.counts_fixture(self.derby))
        # A genuine away match for one of the teams is still counted.
        self.assertTrue(away.counts_fixture(self.away_at_x))

    def test_counts_fixture_keeps_internal_derby_under_home_and_all_scope(self) -> None:
        for scope in (fmodel.VenueScope.HOME, fmodel.VenueScope.ALL):
            rule = fmodel.RollingLimit(
                teams=[self.h1, self.h2], match_cap=fmodel.Cap(1), venue_scope=scope
            )
            self.assertTrue(rule.counts_fixture(self.derby), scope)

    def test_forbids_ignores_internal_derby_under_away_scope(self) -> None:
        rng = fmodel.DateRange(date(2025, 1, 1), date(2025, 1, 31))
        away_blackout = fmodel.RangeLimit(
            teams=[self.h1, self.h2],
            match_cap=fmodel.Cap(0),
            venue_scope=fmodel.VenueScope.AWAY,
            ranges=(rng,),
        )
        self.assertFalse(away_blackout.forbids(self.derby, date(2025, 1, 15)))
        self.assertTrue(away_blackout.forbids(self.away_at_x, date(2025, 1, 15)))

    def test_two_internal_derbies_share_a_night_under_an_away_playing_teams_cap(
        self,
    ) -> None:
        """The draft3 case in miniature: two of a club's own derbies are pinned to
        the same night, and the club has an AWAY-scope max_playing_teams: 2 cap. The
        derbies play at the club's own venue, so they don't count as away and the
        cap is satisfied. Before internal matches were excluded from AWAY scope each
        derby put 2 of the club's teams "away" (4 > 2) and this was INFEASIBLE.
        """
        h3 = fmodel.Team(division=2, club="H", index=3)
        h4 = fmodel.Team(division=2, club="H", index=4)
        night = date(2025, 1, 6)
        params = _params(
            teams=[self.h1, self.h2, h3, h4],
            home_dates={"H": [night, date(2025, 1, 13), date(2025, 1, 20)]},
            unavailable_away_dates={"H": []},
            fixed_fixtures=[
                fmodel.ScheduledFixture(
                    fmodel.Fixture(home_team=self.h1, away_team=self.h2), night
                ),
                fmodel.ScheduledFixture(
                    fmodel.Fixture(home_team=h3, away_team=h4), night
                ),
            ],
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[self.h1, self.h2, h3, h4],
                    playing_teams_cap=fmodel.Cap(2),
                    venue_scope=fmodel.VenueScope.AWAY,
                )
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertEqual(len(fixtures), 4)  # both derbies, both legs
        on_night = {sf.date for sf in fixtures if sf.date == night}
        self.assertEqual(on_night, {night})

    def test_away_playing_teams_cap_still_limits_genuine_away_matches(self) -> None:
        """The same cap still bites when the matches really are away: club A's two
        teams (different divisions, so no internal match between them) both have to
        play their away leg on X's single home date, exceeding max_playing_teams: 1.
        """
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = _params(
            teams=[a1, a2, x1, x2],
            home_dates={"A": [date(2025, 3, 1)], "X": [date(2025, 1, 6)]},
            unavailable_away_dates={"A": [], "X": []},
            match_count_limits=[
                fmodel.RollingLimit(
                    teams=[a1, a2],
                    playing_teams_cap=fmodel.Cap(1),
                    venue_scope=fmodel.VenueScope.AWAY,
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "INFEASIBLE"):
            fmodel.solve(params)


class TestDuplicateRejection(unittest.TestCase):
    """Parameters construction relies on each (Fixture, date) pair mapping to at most
    one solver variable; these are the two ways it could otherwise be violated."""

    def test_duplicate_team_rejected(self) -> None:
        teams = [
            fmodel.Team(division=1, club="A", index=1),
            fmodel.Team(division=1, club="A", index=1),  # duplicate
            fmodel.Team(division=1, club="B", index=1),
        ]
        with self.assertRaisesRegex(ValueError, "Duplicate team"):
            _params(
                teams=teams,
                home_dates={"A": [date(2025, 1, 1)], "B": [date(2025, 2, 1)]},
                unavailable_away_dates={"A": [], "B": []},
                max_concurrent_matches={
                    "A": _home_limit(1),
                    "B": _home_limit(1),
                },
            )

    def test_duplicate_home_date_rejected(self) -> None:
        teams = [
            fmodel.Team(division=1, club="A", index=1),
            fmodel.Team(division=1, club="B", index=1),
        ]
        with self.assertRaisesRegex(ValueError, "Duplicate home date"):
            _params(
                teams=teams,
                home_dates={
                    "A": [date(2025, 1, 1), date(2025, 1, 1)],  # duplicate
                    "B": [date(2025, 2, 1)],
                },
                unavailable_away_dates={"A": [], "B": []},
                max_concurrent_matches={
                    "A": _home_limit(1),
                    "B": _home_limit(1),
                },
            )

    def test_duplicate_team_home_date_rejected(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        b1 = fmodel.Team(division=1, club="B", index=1)
        with self.assertRaisesRegex(ValueError, "Duplicate home date"):
            _params(
                teams=[a1, b1],
                home_dates={
                    "A": [date(2025, 1, 1), date(2025, 1, 2)],
                    "B": [date(2025, 2, 1)],
                },
                unavailable_away_dates={"A": [], "B": []},
                max_concurrent_matches={
                    "A": _home_limit(1),
                    "B": _home_limit(1),
                },
                team_home_dates={
                    a1: [date(2025, 1, 1), date(2025, 1, 1)]  # duplicate
                },
            )


if __name__ == "__main__":
    unittest.main()
