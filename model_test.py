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

import fmodel
import genfixtures


class TestSolve(unittest.TestCase):
    """Test cases for the solve() function."""

    @classmethod
    def setUpClass(cls):
        """Set up class-level data by solving once with real parameters."""
        # Seed random number generator for reproducible test results
        random.seed(42)
        cls.params = genfixtures.build_params()
        cls.fixtures = list(fmodel.solve(cls.params))

    def test_basic_solve(self):
        """Test that solve produces fixtures with real parameters."""
        self.assertGreater(len(self.fixtures), 0, "Should generate some fixtures")
        for sf in self.fixtures:
            self.assertIsInstance(sf, fmodel.ScheduledFixture)

    def test_fixture_uniqueness(self):
        """Test that all fixtures are unique."""
        fixture_pairs = set()
        for sf in self.fixtures:
            pair = (sf.fixture.home_team, sf.fixture.away_team)
            self.assertNotIn(pair, fixture_pairs, f"Duplicate fixture: {pair}")
            fixture_pairs.add(pair)

    def test_valid_home_dates(self):
        """Test that all fixtures are on valid home dates."""
        for sf in self.fixtures:
            home_club = sf.fixture.home_team.club
            self.assertIn(
                sf.date,
                self.params.home_dates[home_club],
                f"Fixture on {sf.date} not on valid home date for {home_club}",
            )

    def test_team_constraints(self):
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

    def test_min_gap_constraint(self):
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

    def test_max_concurrent_home_constraint(self):
        """Test max concurrent home matches constraint with real parameters."""
        # Count home fixtures per club per date
        home_fixtures_by_club_date = collections.defaultdict(int)
        for sf in self.fixtures:
            key = (sf.fixture.home_team.club, sf.date)
            home_fixtures_by_club_date[key] += 1

        for (club, fixture_date), count in home_fixtures_by_club_date.items():
            limit = self.params.max_concurrent_home_matches_for(club, fixture_date)
            self.assertLessEqual(
                count,
                limit,
                f"Club {club} has {count} home matches on {fixture_date}, exceeding limit of {limit}",
            )

    def test_unavailable_away_dates(self):
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

    def test_division_separation(self):
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

    def test_completeness(self):
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

    def test_fixture_count_by_division(self):
        """Test that fixture counts are correct for each division with real parameters."""
        # Count fixtures by division
        fixtures_by_division = collections.defaultdict(int)
        teams_by_division = collections.defaultdict(int)

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

    def test_teams_play_both_home_and_away(self):
        """Test that each team plays both home and away fixtures with real parameters."""
        home_fixtures_by_team = collections.defaultdict(int)
        away_fixtures_by_team = collections.defaultdict(int)

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

    def test_simple_impossible_constraint(self):
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
            max_concurrent_home_matches={
                "Test Club A": fmodel.MaxConcurrentHomeMatches(default=1),
                "Test Club B": fmodel.MaxConcurrentHomeMatches(default=1),
            },
        )

        # This should be impossible to schedule any fixtures due to conflicting constraints
        result = list(fmodel.solve(params))
        # Since constraints make it impossible to schedule required fixtures,
        # the solver returns an empty list (no feasible schedule)
        self.assertEqual(
            len(result),
            0,
            "Expected no fixtures to be scheduled due to impossible constraints",
        )


class TestMaxHomeDatesUsed(unittest.TestCase):
    """Test cases for the max_home_dates_used constraint."""

    def test_constraint_limits_dates_used(self):
        """Solver uses at most max_home_dates_used home dates for a club.

        Club "A" has two teams in a division with six other teams (one each from
        clubs B-G).  Each A team plays seven home matches (one vs each of the
        other seven teams, including the intra-club match).  A has twelve weekly
        home dates available but max_home_dates_used is set to 8.  With
        max_concurrent_home_matches=2, the solver can pack two home matches per
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
            max_concurrent_home_matches={
                "A": fmodel.MaxConcurrentHomeMatches(default=2),
                **{c: fmodel.MaxConcurrentHomeMatches(default=1) for c in other_clubs},
            },
            min_gap_days=0,
            max_home_dates_used={"A": 8},
        )
        fixtures = list(fmodel.solve(params))

        a_dates_used = {sf.date for sf in fixtures if sf.fixture.home_team.club == "A"}
        self.assertLessEqual(len(a_dates_used), 8)
        unused_a_dates = set(a_home_dates) - a_dates_used
        self.assertGreaterEqual(len(unused_a_dates), 4)

    def test_constraint_enforced_strictly(self):
        """A club with two teams needs two home dates; a limit of 1 makes it infeasible."""
        # A has two teams in the same division, requiring 2 home matches on different dates
        # (min_gap_days=7 forces different dates). But max_home_dates_used=1 for A means
        # only 1 date can be used — impossible.
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
            max_concurrent_home_matches={
                "A": fmodel.MaxConcurrentHomeMatches(default=1),
                "B": fmodel.MaxConcurrentHomeMatches(default=2),
            },
            min_gap_days=7,
            max_home_dates_used={"A": 1, "B": 3},
        )
        with self.assertRaises(ValueError):
            fmodel.solve(params)


class TestFixedFixtures(unittest.TestCase):
    """Test cases for the fixed_fixtures constraint."""

    def _params(self, **kwargs):
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
            max_concurrent_home_matches={
                "A": fmodel.MaxConcurrentHomeMatches(default=2),
                "B": fmodel.MaxConcurrentHomeMatches(default=1),
            },
            min_gap_days=7,
            **kwargs,
        )

    def test_fixed_fixture_is_scheduled_on_given_date(self):
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixed = fmodel.ScheduledFixture(
            fixture=fmodel.Fixture(home_team=a1, away_team=a2), date=date(2025, 1, 1)
        )
        params = self._params(fixed_fixtures=[fixed])
        fixtures = list(fmodel.solve(params))
        self.assertIn(fixed, fixtures)

    def test_fixed_fixture_on_non_home_date_rejected(self):
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixed = fmodel.ScheduledFixture(
            fixture=fmodel.Fixture(home_team=a1, away_team=a2),
            date=date(2025, 7, 1),  # not one of A's home dates
        )
        params = self._params(fixed_fixtures=[fixed])
        with self.assertRaises(ValueError):
            fmodel.solve(params)

    def test_fixed_fixture_forces_other_fixtures_off_that_date(self):
        """Pinning A1 v A2 to a date means A1 and A2 are both unavailable that date,
        so the reverse fixture (which involves both of the same teams) must land
        elsewhere."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixed = fmodel.ScheduledFixture(
            fixture=fmodel.Fixture(home_team=a1, away_team=a2), date=date(2025, 1, 1)
        )
        params = self._params(fixed_fixtures=[fixed])
        fixtures = list(fmodel.solve(params))
        # a2 v a1 (the reverse fixture) must be on a different date
        reverse = next(
            sf
            for sf in fixtures
            if sf.fixture.home_team == a2 and sf.fixture.away_team == a1
        )
        self.assertNotEqual(reverse.date, date(2025, 1, 1))


class TestLatestInternalMatchDate(unittest.TestCase):
    """Test cases for the latest_internal_match_date constraint."""

    def _params(self, **kwargs):
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
            max_concurrent_home_matches={
                "A": fmodel.MaxConcurrentHomeMatches(default=2),
                "B": fmodel.MaxConcurrentHomeMatches(default=1),
            },
            min_gap_days=7,
            **kwargs,
        )

    def test_internal_matches_respect_the_cutoff(self):
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        params = self._params(latest_internal_match_date=date(2025, 2, 15))
        fixtures = list(fmodel.solve(params))
        internal = [
            sf
            for sf in fixtures
            if {sf.fixture.home_team, sf.fixture.away_team} == {a1, a2}
        ]
        self.assertEqual(len(internal), 2)  # A1 v A2 and A2 v A1
        for sf in internal:
            self.assertLessEqual(sf.date, date(2025, 2, 15))

    def test_non_internal_matches_unaffected(self):
        """A cross-club fixture (different clubs) may still use a late home date."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        b1 = fmodel.Team(division=1, club="B", index=1)
        params = self._params(latest_internal_match_date=date(2025, 2, 15))
        fixtures = list(fmodel.solve(params))
        cross_club = next(
            sf
            for sf in fixtures
            if sf.fixture.home_team == a1 and sf.fixture.away_team == b1
        )
        self.assertGreater(cross_club.date, date(2025, 2, 15))

    def test_cutoff_before_any_home_date_drops_the_internal_fixture(self):
        """No A home date qualifies, so the internal fixture has zero candidate
        variables: consistent with how a fixture that unavailable_away_dates makes
        wholly unschedulable is silently omitted (see
        TestSolve.test_simple_impossible_constraint) rather than erroring, it's simply
        left out of the result -- other fixtures are unaffected.
        """
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        params = self._params(latest_internal_match_date=date(2024, 12, 1))
        fixtures = list(fmodel.solve(params))
        internal = [
            sf
            for sf in fixtures
            if {sf.fixture.home_team, sf.fixture.away_team} == {a1, a2}
        ]
        self.assertEqual(internal, [])
        self.assertTrue(fixtures)  # the other (non-internal) fixtures still solve

    def test_fixed_internal_fixture_after_cutoff_rejected(self):
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

    def test_no_cutoff_by_default(self):
        """Without latest_internal_match_date, internal matches can use any date."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        params = self._params()
        fixtures = list(fmodel.solve(params))
        internal_dates = {
            sf.date
            for sf in fixtures
            if {sf.fixture.home_team, sf.fixture.away_team} == {a1, a2}
        }
        self.assertTrue(internal_dates)


class TestExcludedFixtures(unittest.TestCase):
    """Test cases for the excluded_fixtures parameter."""

    def _params(self, **kwargs):
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
            max_concurrent_home_matches={
                "A": fmodel.MaxConcurrentHomeMatches(default=2),
                "B": fmodel.MaxConcurrentHomeMatches(default=1),
            },
            min_gap_days=7,
            **kwargs,
        )

    def test_excluded_fixture_is_not_scheduled(self):
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        excluded_fixture = fmodel.Fixture(home_team=a1, away_team=a2)
        params = self._params(excluded_fixtures=[excluded_fixture])
        fixtures = list(fmodel.solve(params))
        self.assertNotIn(excluded_fixture, [sf.fixture for sf in fixtures])
        # The other 5 fixtures in this 3-team division must still be scheduled
        self.assertEqual(len(fixtures), 5)

    def test_exclusion_is_directional(self):
        """Excluding A1 v A2 must not also exclude the reverse fixture A2 v A1."""
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        excluded_fixture = fmodel.Fixture(home_team=a1, away_team=a2)
        params = self._params(excluded_fixtures=[excluded_fixture])
        fixtures = list(fmodel.solve(params))
        self.assertTrue(
            any(
                sf.fixture.home_team == a2 and sf.fixture.away_team == a1
                for sf in fixtures
            )
        )

    def test_fixed_and_excluded_conflict_rejected(self):
        a1 = fmodel.Team(division=1, club="A", index=1)
        a2 = fmodel.Team(division=1, club="A", index=2)
        fixture = fmodel.Fixture(home_team=a1, away_team=a2)
        fixed = fmodel.ScheduledFixture(fixture=fixture, date=date(2025, 1, 1))
        params = self._params(fixed_fixtures=[fixed], excluded_fixtures=[fixture])
        with self.assertRaises(ValueError):
            fmodel.solve(params)


class TestDuplicateRejection(unittest.TestCase):
    """Parameters construction relies on each (Fixture, date) pair mapping to at most
    one solver variable; these are the two ways it could otherwise be violated."""

    def test_duplicate_team_rejected(self):
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
                max_concurrent_home_matches={
                    "A": fmodel.MaxConcurrentHomeMatches(default=1),
                    "B": fmodel.MaxConcurrentHomeMatches(default=1),
                },
            )

    def test_duplicate_home_date_rejected(self):
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
                max_concurrent_home_matches={
                    "A": fmodel.MaxConcurrentHomeMatches(default=1),
                    "B": fmodel.MaxConcurrentHomeMatches(default=1),
                },
            )


if __name__ == "__main__":
    unittest.main()
