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

"""Library to model and solve Middlesex League fixtures scheduling."""

import collections
import dataclasses
import itertools
from collections.abc import Collection, Mapping, MutableMapping
from datetime import date

from ortools.sat.python import cp_model


@dataclasses.dataclass(frozen=True)
class Team:
    division: int
    club: str
    index: int
    name_override: str | None = None

    @property
    def name(self) -> str:
        """A display name usable when no richer club/team metadata is available.

        Reports built from a fixturespec.Spec should prefer resolving names via
        its clubs mapping (so club display names and name_override are used
        consistently); this is a fallback for contexts (e.g. genfixtures.py)
        where `club` already doubles as a display name.
        """
        return self.name_override if self.name_override else f"{self.club} {self.index}"


@dataclasses.dataclass(frozen=True)
class Fixture:
    home_team: Team
    away_team: Team


@dataclasses.dataclass(frozen=True)
class ScheduledFixture:
    fixture: Fixture
    date: date


ClubT = str


@dataclasses.dataclass(frozen=True)
class Club:
    """Reporting metadata for a club. Not used by the solver itself."""

    name: str
    home_venue_name: str
    home_venue_address: str
    home_start_time: str
    home_time_limit: str  # chess time control, e.g. "75+15" for 75 min + 15 sec/move


@dataclasses.dataclass(frozen=True)
class MaxConcurrentHomeMatches:
    """A club's home-match concurrency limit: a default, overridable for specific dates."""

    default: int
    overrides: Mapping[date, int] = dataclasses.field(default_factory=dict)

    def for_date(self, d: date) -> int:
        return self.overrides.get(d, self.default)


def _check_no_duplicate_teams(teams: Collection[Team]) -> None:
    """Reject repeated teams: solve() relies on each (Fixture, date) pair mapping to
    at most one solver variable, which would break if the same team (by value) appeared
    twice in the same division.
    """
    seen: set[Team] = set()
    for team in teams:
        if team in seen:
            raise ValueError(f"Duplicate team in Parameters.teams: {team!r}")
        seen.add(team)


def _check_no_duplicate_home_dates(home_dates: Mapping[ClubT, list[date]]) -> None:
    """Reject repeated dates in a club's home_dates: solve() relies on each (Fixture,
    date) pair mapping to at most one solver variable, which would break if a date
    appeared twice in the same club's home_dates list.
    """
    for club, dates in home_dates.items():
        seen: set[date] = set()
        for d in dates:
            if d in seen:
                raise ValueError(
                    f"Duplicate home date {d.isoformat()} for club {club!r}"
                )
            seen.add(d)


@dataclasses.dataclass(frozen=True)
class Parameters:
    teams: Collection[Team]
    home_dates: Mapping[ClubT, list[date]]
    unavailable_away_dates: Mapping[ClubT, list[date]]
    max_concurrent_home_matches: Mapping[ClubT, MaxConcurrentHomeMatches]
    min_gap_days: int = 7
    max_home_dates_used: Mapping[ClubT, int] = dataclasses.field(default_factory=dict)
    fixed_fixtures: Collection[ScheduledFixture] = ()
    excluded_fixtures: Collection[Fixture] = ()
    latest_internal_match_date: date | None = None

    def __post_init__(self) -> None:
        _check_no_duplicate_teams(self.teams)
        _check_no_duplicate_home_dates(self.home_dates)

    def max_concurrent_home_matches_for(self, club: ClubT, d: date) -> int:
        """The most home matches `club` may host on date `d`."""
        return self.max_concurrent_home_matches[club].for_date(d)


def date_windows(dates: Collection[date], window_days: int) -> list[frozenset[date]]:
    """Given a list of dates and a window size, return the maximal subsets of dates which fall within the window size."""
    all_windows: list[frozenset[date]] = []
    dates = sorted(dates)
    for i, d in enumerate(dates):
        w = {d}
        for j in range(i + 1, len(dates)):
            if (dates[j] - d).days <= window_days:
                w.add(dates[j])
            else:
                break
        all_windows.append(frozenset(w))

    all_windows.sort(key=lambda w: len(w), reverse=True)
    result: list[frozenset[date]] = []
    for window in all_windows:
        if not any(window.issubset(w) for w in result):
            result.append(window)

    return result


def _add_max_home_dates_used_constraints(
    model: cp_model.CpModel,
    max_home_dates_used: Mapping[ClubT, int],
    vars_by_club_home_date: Mapping[tuple[str, date], list[cp_model.IntVar]],
) -> None:
    """For each club in max_home_dates_used, add constraints limiting the number of home dates used."""
    # Collect the set of home dates per club that appear in the variable map
    clubs_home_dates: MutableMapping[str, list[date]] = collections.defaultdict(list)
    for club, d in vars_by_club_home_date:
        if club in max_home_dates_used:
            clubs_home_dates[club].append(d)

    for club, limit in max_home_dates_used.items():
        date_used_vars = []
        for d in clubs_home_dates[club]:
            date_vars = vars_by_club_home_date[(club, d)]
            date_used = model.new_bool_var(f"{club}_date_used_{d.isoformat()}")
            # date_used == 1 iff sum(date_vars) >= 1
            model.add(cp_model.LinearExpr.Sum(date_vars) >= 1).only_enforce_if(
                date_used
            )
            model.add(cp_model.LinearExpr.Sum(date_vars) == 0).only_enforce_if(
                date_used.negated()
            )
            date_used_vars.append(date_used)
        model.add(cp_model.LinearExpr.Sum(date_used_vars) <= limit)


def _add_fixed_fixtures_constraints(
    model: cp_model.CpModel,
    fixed_fixtures: Collection[ScheduledFixture],
    vars_by_fixture_date: Mapping[tuple[Fixture, date], cp_model.IntVar],
) -> None:
    """Force each pre-specified fixture onto its given date."""
    for scheduled in fixed_fixtures:
        key = (scheduled.fixture, scheduled.date)
        var = vars_by_fixture_date.get(key)
        if var is None:
            home_team = scheduled.fixture.home_team
            away_team = scheduled.fixture.away_team
            raise ValueError(
                f"Fixed fixture {home_team.name} vs {away_team.name} on "
                f"{scheduled.date.isoformat()} is not schedulable on that date "
                "(check that the two teams are in the same division, that the date "
                "is a home date for the home team's club, that it isn't an "
                "unavailable away date for the away team's club, that the fixture "
                "isn't also in excluded_fixtures, and -- if the two teams share a "
                "club -- that the date isn't after latest_internal_match_date)"
            )
        model.add(var == 1)


def solve(params: Parameters) -> Collection[ScheduledFixture]:
    model = cp_model.CpModel()
    teams_by_division = collections.defaultdict(list)
    for team in params.teams:
        teams_by_division[team.division].append(team)

    vars_by_fixture: MutableMapping[Fixture, list[cp_model.IntVar]] = (
        collections.defaultdict(list)
    )
    vars_by_fixture_date: MutableMapping[tuple[Fixture, date], cp_model.IntVar] = {}
    vars_by_team_date: MutableMapping[
        Team, MutableMapping[date, list[cp_model.IntVar]]
    ] = collections.defaultdict(lambda: collections.defaultdict(list))
    vars_by_club_home_date: MutableMapping[tuple[str, date], list[cp_model.IntVar]] = (
        collections.defaultdict(list)
    )

    excluded = set(params.excluded_fixtures)

    for division_teams in teams_by_division.values():
        for home_team, away_team in itertools.permutations(division_teams, 2):
            fixture = Fixture(home_team=home_team, away_team=away_team)
            if fixture in excluded:
                continue
            is_internal = home_team.club == away_team.club
            for match_date in params.home_dates[home_team.club]:
                if match_date in params.unavailable_away_dates.get(away_team.club, []):
                    continue
                if (
                    is_internal
                    and params.latest_internal_match_date is not None
                    and match_date > params.latest_internal_match_date
                ):
                    continue
                var = model.new_bool_var(
                    f"{home_team.name}_vs_{away_team.name}_{match_date.isoformat()}"
                )
                key = (fixture, match_date)
                if key in vars_by_fixture_date:
                    raise ValueError(
                        f"Duplicate variable for fixture {fixture.home_team.name} vs "
                        f"{fixture.away_team.name} on {match_date.isoformat()}"
                    )
                vars_by_fixture[fixture].append(var)
                vars_by_fixture_date[key] = var
                vars_by_team_date[home_team][match_date].append(var)
                vars_by_team_date[away_team][match_date].append(var)
                vars_by_club_home_date[(home_team.club, match_date)].append(var)

    for fixture_vars in vars_by_fixture.values():
        # Each fixture must be scheduled exactly once
        model.add(cp_model.LinearExpr.Sum(fixture_vars) == 1)

    for team_vars_by_date in vars_by_team_date.values():
        # Each team can play at most one match in each window
        for window in date_windows(team_vars_by_date.keys(), params.min_gap_days):
            window_vars = [v for d in window for v in team_vars_by_date[d]]
            model.add(cp_model.LinearExpr.Sum(window_vars) <= 1)

    for (club, match_date), club_home_date_vars in vars_by_club_home_date.items():
        # Each club can host at most max_concurrent_home_matches matches per date
        max_matches = params.max_concurrent_home_matches_for(club, match_date)
        model.add(cp_model.LinearExpr.Sum(club_home_date_vars) <= max_matches)

    _add_max_home_dates_used_constraints(
        model, params.max_home_dates_used, vars_by_club_home_date
    )
    _add_fixed_fixtures_constraints(model, params.fixed_fixtures, vars_by_fixture_date)

    solver = cp_model.CpSolver()
    status = solver.Solve(model)
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        result = []
        for (fixture, match_date), var in vars_by_fixture_date.items():
            if solver.BooleanValue(var):
                result.append(ScheduledFixture(fixture=fixture, date=match_date))
        return result
    else:
        raise ValueError("No solution found")
