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

    @property
    def name(self) -> str:
        return f"{self.club} {self.index}"


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
class Parameters:
    teams: Collection[Team]
    home_dates: Mapping[ClubT, list[date]]
    unavailable_away_dates: Mapping[ClubT, list[date]]
    min_gap_days: int = 7
    max_concurrent_home_matches: int = 2


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


def solve(params: Parameters) -> Collection[ScheduledFixture]:
    model = cp_model.CpModel()
    teams_by_division = collections.defaultdict(list)
    for team in params.teams:
        teams_by_division[team.division].append(team)

    vars_by_fixture: MutableMapping[Fixture, list[cp_model.IntVar]] = (
        collections.defaultdict(list)
    )
    vars_by_fixture_date: MutableMapping[
        tuple[Fixture, date], list[cp_model.IntVar]
    ] = collections.defaultdict(list)
    vars_by_team_date: MutableMapping[
        Team, MutableMapping[date, list[cp_model.IntVar]]
    ] = collections.defaultdict(lambda: collections.defaultdict(list))
    vars_by_club_home_date: MutableMapping[tuple[str, date], list[cp_model.IntVar]] = (
        collections.defaultdict(list)
    )

    for division_teams in teams_by_division.values():
        for home_team, away_team in itertools.permutations(division_teams, 2):
            for match_date in params.home_dates[home_team.club]:
                if match_date in params.unavailable_away_dates.get(away_team.club, []):
                    continue
                var = model.new_bool_var(
                    f"{home_team.name}_vs_{away_team.name}_{match_date.isoformat()}"
                )
                vars_by_fixture[
                    Fixture(home_team=home_team, away_team=away_team)
                ].append(var)
                vars_by_fixture_date[
                    (Fixture(home_team=home_team, away_team=away_team), match_date)
                ].append(var)
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

    for club_home_date_vars in vars_by_club_home_date.values():
        # Each club can host at most max_concurrent_home_matches matches per date
        model.add(
            cp_model.LinearExpr.Sum(club_home_date_vars)
            <= params.max_concurrent_home_matches
        )

    solver = cp_model.CpSolver()
    status = solver.Solve(model)
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        result = []
        for (fixture, match_date), fixture_vars in vars_by_fixture_date.items():
            for var in fixture_vars:
                if solver.BooleanValue(var):
                    result.append(ScheduledFixture(fixture=fixture, date=match_date))
        return result
    else:
        raise ValueError("No solution found")
