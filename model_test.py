# Copyright 2025 Andrew Medworth
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
from datetime import date

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
            self.assertLessEqual(
                count,
                self.params.max_concurrent_home_matches,
                f"Club {club} has {count} home matches on {fixture_date}, exceeding limit of {self.params.max_concurrent_home_matches}",
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
            max_concurrent_home_matches=1,
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


if __name__ == "__main__":
    unittest.main()
