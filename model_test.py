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
import random
import unittest
from datetime import date, timedelta
from typing import Any

import berger
import fmodel
import genfixtures


def _home_limit(n: int | None) -> fmodel.MaxConcurrentMatches:
    """A club's MaxConcurrentMatches carrying just a HOME-scope limit with default
    n (no per-date overrides) -- the shape most of these tests need."""
    return fmodel.MaxConcurrentMatches(
        by_scope={fmodel.ConcurrencyScope.HOME: fmodel.ConcurrencyLimit(default=n)}
    )


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
                    self.params.min_gap_days,
                    f"Team {team.name} has fixtures too close: {sorted_dates[i - 1]} and {sorted_dates[i]} (gap: {gap} days)",
                )

    def test_max_concurrent_home_constraint(self) -> None:
        """Test max concurrent home matches constraint with real parameters."""
        # Count home fixtures per club per date
        home_fixtures_by_club_date: dict[tuple[str, date], int] = (
            collections.defaultdict(int)
        )
        for sf in self.fixtures:
            key = (sf.fixture.home_team.club, sf.date)
            home_fixtures_by_club_date[key] += 1

        for (club, fixture_date), count in home_fixtures_by_club_date.items():
            limit = self.params.max_concurrent_matches_for(
                club, fmodel.ConcurrencyScope.HOME, fixture_date
            )
            # None means unlimited -- including a configured limit that's >= the
            # club's number of teams, which can never actually bind (see
            # TestMaxConcurrentMatchesFor) -- so there's nothing to check.
            if limit is None:
                continue
            self.assertLessEqual(
                count,
                limit,
                f"Club {club} has {count} home matches on {fixture_date}, exceeding limit of {limit}",
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
        """Test that impossible constraints result in no fixtures being scheduled."""
        # Create a scenario that's impossible to solve
        team1 = fmodel.Team(division=1, club="Test Club A", index=1)
        team2 = fmodel.Team(division=1, club="Test Club B", index=1)

        params = fmodel.Parameters(
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

        # This should be impossible to schedule any fixtures due to conflicting constraints
        result = list(fmodel.solve(params).fixtures)
        # Since constraints make it impossible to schedule required fixtures,
        # the solver returns an empty list (no feasible schedule)
        self.assertEqual(
            len(result),
            0,
            "Expected no fixtures to be scheduled due to impossible constraints",
        )


class TestSolveStats(unittest.TestCase):
    """solve() returns the OR-Tools model/solver summary text alongside the
    schedule, and raises when the model is genuinely unsatisfiable."""

    def test_captures_model_and_solve_stats(self) -> None:
        random.seed(42)
        result = fmodel.solve(genfixtures.build_params())
        self.assertIn("#Variables", result.model_stats)
        self.assertIn("CpSolverResponse summary", result.solve_stats)
        self.assertIn("status:", result.solve_stats)

    def test_raises_when_infeasible(self) -> None:
        # Two teams of one club, only a single shared home date: both the A1 v A2
        # and A2 v A1 fixtures are forced onto it, but neither team may play twice
        # in a window -- an unsatisfiable model, not just an empty schedule.
        team1 = fmodel.Team(division=1, club="A", index=1)
        team2 = fmodel.Team(division=1, club="A", index=2)
        params = fmodel.Parameters(
            teams=[team1, team2],
            home_dates={"A": [date(2025, 1, 1)]},
            unavailable_away_dates={"A": []},
        )
        with self.assertRaisesRegex(ValueError, "No solution found"):
            fmodel.solve(params)


class TestMaxConcurrentMatchesFor(unittest.TestCase):
    """A configured max_concurrent_matches limit that is >= a club's number of
    teams can never actually restrict anything (the club can never play more
    simultaneous matches than it has teams), so max_concurrent_matches_for()
    should report it as unlimited (None) too. See issue #22.
    """

    def _params(self, num_teams: int, limit: int | None) -> fmodel.Parameters:
        teams = [
            fmodel.Team(division=1, club="A", index=i) for i in range(1, num_teams + 1)
        ]
        return fmodel.Parameters(
            teams=teams,
            home_dates={"A": [date(2025, 1, 1)]},
            unavailable_away_dates={"A": []},
            max_concurrent_matches={
                "A": _home_limit(limit),
            },
        )

    def test_limit_below_team_count_is_kept(self) -> None:
        params = self._params(num_teams=3, limit=2)
        self.assertEqual(
            params.max_concurrent_matches_for(
                "A", fmodel.ConcurrencyScope.HOME, date(2025, 1, 1)
            ),
            2,
        )

    def test_limit_equal_to_team_count_is_reported_as_unlimited(self) -> None:
        params = self._params(num_teams=2, limit=2)
        self.assertIsNone(
            params.max_concurrent_matches_for(
                "A", fmodel.ConcurrencyScope.HOME, date(2025, 1, 1)
            )
        )

    def test_limit_above_team_count_is_reported_as_unlimited(self) -> None:
        params = self._params(num_teams=2, limit=5)
        self.assertIsNone(
            params.max_concurrent_matches_for(
                "A", fmodel.ConcurrencyScope.HOME, date(2025, 1, 1)
            )
        )

    def test_explicit_unlimited_stays_unlimited(self) -> None:
        params = self._params(num_teams=2, limit=None)
        self.assertIsNone(
            params.max_concurrent_matches_for(
                "A", fmodel.ConcurrencyScope.HOME, date(2025, 1, 1)
            )
        )

    def test_club_with_no_entry_is_unlimited(self) -> None:
        params = self._params(num_teams=3, limit=2)
        # "B" isn't in max_concurrent_matches at all.
        self.assertIsNone(
            params.max_concurrent_matches_for(
                "B", fmodel.ConcurrencyScope.HOME, date(2025, 1, 1)
            )
        )

    def test_scope_with_no_entry_is_unlimited(self) -> None:
        params = self._params(num_teams=3, limit=2)
        # Only the HOME scope is configured; AWAY/ANY have no limit.
        self.assertIsNone(
            params.max_concurrent_matches_for(
                "A", fmodel.ConcurrencyScope.AWAY, date(2025, 1, 1)
            )
        )


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
        params = fmodel.Parameters(
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
        params = fmodel.Parameters(
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
        params = fmodel.Parameters(
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
        params = fmodel.Parameters(
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
        return fmodel.Parameters(
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
        params = fmodel.Parameters(
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
        params = fmodel.Parameters(
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
        return fmodel.Parameters(
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

    def test_cutoff_before_any_home_date_drops_the_internal_fixture(self) -> None:
        """No A home date qualifies, so the internal fixture has zero candidate
        variables: consistent with how a fixture that unavailable_away_dates makes
        wholly unschedulable is silently omitted (see
        TestSolve.test_simple_impossible_constraint) rather than erroring, it's simply
        left out of the result -- other fixtures are unaffected.
        """
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        params = self._params(latest_internal_match_date=date(2024, 12, 1))
        fixtures = list(fmodel.solve(params).fixtures)
        internal = [
            sf
            for sf in fixtures
            if {sf.fixture.home_team, sf.fixture.away_team} == {a1, a2}
        ]
        self.assertEqual(internal, [])
        self.assertTrue(fixtures)  # the other (non-internal) fixtures still solve

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
        return fmodel.Parameters(
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

    def test_cutoff_after_all_of_a_clubs_home_dates_drops_its_fixtures(self) -> None:
        """A cutoff after all of club A's home dates (but before club B's) makes
        every A-hosted fixture unschedulable -- each has zero candidate
        variables, so (consistent with
        TestLatestInternalMatchDate.test_cutoff_before_any_home_date_drops_the_internal_fixture)
        it's silently left out of the result rather than erroring. B-hosted
        fixtures are unaffected since none of B's dates are excluded.
        """
        params = self._params(earliest_match_date=date(2025, 4, 1))
        fixtures = list(fmodel.solve(params).fixtures)
        self.assertFalse([sf for sf in fixtures if sf.fixture.home_team.club == "A"])
        self.assertTrue([sf for sf in fixtures if sf.fixture.home_team.club == "B"])

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
        return fmodel.Parameters(
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
        return fmodel.Parameters(
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
        params = fmodel.Parameters(
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
        B's home date, even though club A has no such club-wide restriction."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        b1 = fmodel.Team(division=1, club="B", index=1)
        params = fmodel.Parameters(
            teams=[a1, b1],
            home_dates={"A": [date(2025, 1, 1)], "B": [date(2025, 2, 1)]},
            unavailable_away_dates={"A": [], "B": []},
            max_concurrent_matches={
                "A": _home_limit(1),
                "B": _home_limit(1),
            },
            min_gap_days=7,
            team_unavailable_away_dates={a1: [date(2025, 2, 1)]},
        )
        fixtures = list(fmodel.solve(params).fixtures)
        # A1 v B1 (A1 away at B) can't be scheduled: B's only home date is blocked
        # for A1 specifically, so that fixture is left unschedulable.
        self.assertFalse(
            any(
                sf.fixture.home_team == b1 and sf.fixture.away_team == a1
                for sf in fixtures
            )
        )
        # B1 v A1 (A1 at home) is unaffected.
        self.assertTrue(
            any(
                sf.fixture.home_team == a1 and sf.fixture.away_team == b1
                for sf in fixtures
            )
        )

    def test_team_without_override_falls_back_to_club(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        params = fmodel.Parameters(
            teams=[a1],
            home_dates={"A": [date(2025, 1, 1), date(2025, 2, 1)]},
            unavailable_away_dates={"A": []},
            max_concurrent_matches={"A": _home_limit(1)},
        )
        self.assertEqual(
            params.home_dates_for(a1), [date(2025, 1, 1), date(2025, 2, 1)]
        )
        self.assertEqual(params.unavailable_away_dates_for(a1), set())


class TestAvoidCoschedulingTeams(unittest.TestCase):
    """Test cases for AvoidCoschedulingConstraint: at most one match involving any of
    a given set of teams may be scheduled within any window of within_days days.
    """

    def test_within_days_defaults_to_zero(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        self.assertEqual(
            fmodel.AvoidCoschedulingConstraint(teams=[a1, a2]).within_days, 0
        )

    def test_applies_to_defaults_to_both(self) -> None:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        self.assertEqual(
            fmodel.AvoidCoschedulingConstraint(teams=[a1, a2]).applies_to,
            fmodel.CoschedulingScope.BOTH,
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
        params = fmodel.Parameters(
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
            avoid_coscheduling_teams=[
                fmodel.AvoidCoschedulingConstraint(teams=[a1, a2])
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
        params = fmodel.Parameters(
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
            avoid_coscheduling_teams=[
                fmodel.AvoidCoschedulingConstraint(teams=[a1, a2])
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
        params = fmodel.Parameters(
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
            avoid_coscheduling_teams=[
                fmodel.AvoidCoschedulingConstraint(teams=[a1, a2])
            ],
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_within_days_window_enforced(self) -> None:
        """With within_days=3, A1 and A2's home matches must land on dates more than
        3 days apart -- of A's three candidate dates (Jan 1, 3, 10), only pairings
        involving Jan 10 satisfy that, so the solver must pick one of those. (X's two
        home dates, for A1/A2's away legs, are also more than 3 days apart, so they
        don't introduce a second conflict of their own.)"""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = fmodel.Parameters(
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
            avoid_coscheduling_teams=[
                fmodel.AvoidCoschedulingConstraint(teams=[a1, a2], within_days=3)
            ],
        )
        fixtures = list(fmodel.solve(params).fixtures)
        a1_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a1)
        a2_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a2)
        self.assertGreater(abs((a1_home_date - a2_home_date).days), 3)

    def test_direct_fixture_between_constrained_teams_not_double_counted(self) -> None:
        """A single match between two constrained teams should count once towards
        the <=1 limit, not twice -- if the two teams' variables were combined
        naively (e.g. by concatenating each team's own match list) rather than by
        filtering the single var per (fixture, date), this fixture's variable would
        be counted twice and wrongly made unschedulable."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        params = fmodel.Parameters(
            teams=[a1, a2],
            home_dates={"A": [date(2025, 1, 1)]},
            unavailable_away_dates={"A": []},
            max_concurrent_matches={"A": _home_limit(1)},
            min_gap_days=7,
            excluded_fixtures=[fmodel.Fixture(home_team=a2, away_team=a1)],
            avoid_coscheduling_teams=[
                fmodel.AvoidCoschedulingConstraint(teams=[a1, a2])
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
        params = fmodel.Parameters(
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
            avoid_coscheduling_teams=[
                fmodel.AvoidCoschedulingConstraint(
                    teams=[a1, a2], applies_to=fmodel.CoschedulingScope.AWAY
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
        params = fmodel.Parameters(
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
            avoid_coscheduling_teams=[
                fmodel.AvoidCoschedulingConstraint(
                    teams=[a1, a2], applies_to=fmodel.CoschedulingScope.AWAY
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
        params = fmodel.Parameters(
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
            avoid_coscheduling_teams=[
                fmodel.AvoidCoschedulingConstraint(
                    teams=[a1, a2], applies_to=fmodel.CoschedulingScope.HOME
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
        params = fmodel.Parameters(
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
            avoid_coscheduling_teams=[
                fmodel.AvoidCoschedulingConstraint(
                    teams=[a1, a2], applies_to=fmodel.CoschedulingScope.HOME
                )
            ],
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)


class TestMaxConcurrentMatchesUnlimited(unittest.TestCase):
    """Test cases for ConcurrencyLimit(default=None) / overrides=None: no
    limit imposed by this mechanism, as opposed to a finite one.
    """

    def test_finite_default_makes_it_infeasible(self) -> None:
        """A has two teams (different divisions, so no direct fixture forces them
        apart) but only one home date; with a limit of 1, both teams needing to host
        on that one date is infeasible."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        params = fmodel.Parameters(
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
        params = fmodel.Parameters(
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
        params = fmodel.Parameters(
            teams=[a1, a2, x1, x2],
            home_dates={
                "A": [date(2025, 1, 1)],
                "X": [date(2025, 3, 1), date(2025, 3, 8)],
            },
            unavailable_away_dates={"A": [], "X": []},
            max_concurrent_matches={
                "A": fmodel.MaxConcurrentMatches(
                    by_scope={
                        fmodel.ConcurrencyScope.HOME: fmodel.ConcurrencyLimit(
                            default=1, overrides={date(2025, 1, 1): None}
                        )
                    }
                ),
                "X": _home_limit(2),
            },
            min_gap_days=7,
        )
        fixtures = list(fmodel.solve(params).fixtures)
        a1_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a1)
        a2_home_date = next(sf.date for sf in fixtures if sf.fixture.home_team == a2)
        self.assertEqual(a1_home_date, date(2025, 1, 1))
        self.assertEqual(a2_home_date, date(2025, 1, 1))


class TestMaxConcurrentMatchesScopes(unittest.TestCase):
    """The AWAY and ANY ConcurrencyScopes at the solve() level (HOME is covered by
    the classes above). Scenario: club A's two teams (different divisions, so no
    direct fixture) play their away legs at club X, whose single home date forces
    both onto the same night unless something says otherwise.
    """

    def _params(
        self,
        a_limits: fmodel.MaxConcurrentMatches,
        *,
        x_home_dates: list[date],
    ) -> fmodel.Parameters:
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=2, club="A", index=2)
        x1 = fmodel.Team(division=1, club="X", index=1)
        x2 = fmodel.Team(division=2, club="X", index=2)
        return fmodel.Parameters(
            teams=[a1, a2, x1, x2],
            home_dates={"A": [date(2025, 3, 1)], "X": x_home_dates},
            unavailable_away_dates={"A": [], "X": []},
            # X can host both its teams' matches whenever it has the dates for them.
            max_concurrent_matches={"A": a_limits},
            min_gap_days=7,
        )

    def _any(self, limit: int | None) -> fmodel.MaxConcurrentMatches:
        return fmodel.MaxConcurrentMatches(
            by_scope={fmodel.ConcurrencyScope.ANY: fmodel.ConcurrencyLimit(limit)}
        )

    def _away(self, limit: int | None) -> fmodel.MaxConcurrentMatches:
        return fmodel.MaxConcurrentMatches(
            by_scope={fmodel.ConcurrencyScope.AWAY: fmodel.ConcurrencyLimit(limit)}
        )

    def test_any_scope_limit_blocks_two_matches_on_one_date(self) -> None:
        # X has one home date, so A1 and A2 must both play away that night; an ANY
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
            fmodel.Parameters(
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
            fmodel.Parameters(
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
            fmodel.Parameters(
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
