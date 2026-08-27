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
import functools
import itertools
import logging
from collections.abc import Collection, Mapping, MutableMapping
from datetime import date
from typing import Any

from ortools.sat.python import cp_model

logger = logging.getLogger(__name__)


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
    """A club's home-match concurrency limit: a default, overridable for specific
    dates. A value of None (for the default or an override) means no limit is
    imposed by this mechanism -- e.g. for a club whose concurrency is already
    bounded by another constraint (such as avoid_coscheduling_teams), where adding
    an explicit number here would just be a redundant, easy-to-forget-to-update
    restatement of that bound.
    """

    default: int | None
    overrides: Mapping[date, int | None] = dataclasses.field(default_factory=dict)

    def for_date(self, d: date) -> int | None:
        return self.overrides.get(d, self.default)


@dataclasses.dataclass(frozen=True)
class AvoidCoschedulingConstraint:
    """At most one match involving any of `teams` may be scheduled within any window
    of `within_days` days -- e.g. within_days=0 (the default) means no two of them
    may share a date. Typically used for a club's own teams that draw from the same
    pool of players (e.g. adjacent-division teams), but not restricted to that.
    """

    teams: Collection[Team]
    within_days: int = 0


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


def _check_no_duplicate_home_dates(home_dates: Mapping[Any, list[date]]) -> None:
    """Reject repeated dates in a club's (or team's) home_dates: solve() relies on
    each (Fixture, date) pair mapping to at most one solver variable, which would
    break if a date appeared twice in the same home_dates list.
    """
    for key, dates in home_dates.items():
        seen: set[date] = set()
        for d in dates:
            if d in seen:
                raise ValueError(f"Duplicate home date {d.isoformat()} for {key!r}")
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
    # Excludes candidate dates before this one from newly scheduled fixtures -- e.g.
    # so re-running the solver after some home dates have already passed doesn't
    # place a fixture in the past. Unlike latest_internal_match_date, a fixed_fixtures
    # entry dated before this cutoff is *not* rejected: fixed_fixtures records matches
    # that are already committed (possibly already played), so an old date there is
    # expected, not an error, and must keep solving correctly however far into the
    # season this is re-run.
    earliest_match_date: date | None = None
    # Per-team overrides/additions to a club's home_dates/unavailable_away_dates, for
    # clubs whose teams don't all share the same availability (e.g. different squads
    # of players). A team not present here just uses its club's dates as before.
    team_home_dates: Mapping[Team, list[date]] = dataclasses.field(default_factory=dict)
    team_unavailable_away_dates: Mapping[Team, list[date]] = dataclasses.field(
        default_factory=dict
    )
    avoid_coscheduling_teams: Collection[AvoidCoschedulingConstraint] = ()

    def __post_init__(self) -> None:
        _check_no_duplicate_teams(self.teams)
        _check_no_duplicate_home_dates(self.home_dates)
        _check_no_duplicate_home_dates(self.team_home_dates)

    @functools.cached_property
    def _teams_per_club(self) -> Mapping[ClubT, int]:
        return collections.Counter(team.club for team in self.teams)

    def max_concurrent_home_matches_for(self, club: ClubT, d: date) -> int | None:
        """The most home matches `club` may host on date `d`, or None if unlimited.

        A configured limit that is >= the club's own number of teams is reported as
        unlimited too: the club can never field more simultaneous home matches than
        it has teams, so such a limit could never actually bind.
        """
        limit = self.max_concurrent_home_matches[club].for_date(d)
        if limit is not None and limit >= self._teams_per_club[club]:
            return None
        return limit

    def home_dates_for(self, team: Team) -> list[date]:
        """The candidate home dates for `team`: its own override if it has one
        (team_home_dates), otherwise its club's home_dates."""
        return self.team_home_dates.get(team, self.home_dates[team.club])

    def unavailable_away_dates_for(self, team: Team) -> Collection[date]:
        """The dates `team` can't play away: its club's unavailable_away_dates, plus
        any team-specific additions (team_unavailable_away_dates)."""
        return set(self.unavailable_away_dates.get(team.club, ())) | set(
            self.team_unavailable_away_dates.get(team, ())
        )


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


def _add_avoid_coscheduling_constraints(
    model: cp_model.CpModel,
    constraints: Collection[AvoidCoschedulingConstraint],
    vars_by_team_date: Mapping[Team, Mapping[date, list[cp_model.IntVar]]],
) -> None:
    """For each AvoidCoschedulingConstraint, ensure at most one match involving any
    of its teams is scheduled within any window of within_days days.
    """
    for constraint in constraints:
        # vars_by_team_date stores the same variable for both the home and away
        # team of a fixture, so a match between two teams that are both in
        # `constraint.teams` would be combined twice; key by id(var) per date to
        # avoid double-counting it.
        vars_by_date: MutableMapping[date, dict[int, cp_model.IntVar]] = (
            collections.defaultdict(dict)
        )
        for team in constraint.teams:
            for d, team_vars in vars_by_team_date.get(team, {}).items():
                for var in team_vars:
                    vars_by_date[d][id(var)] = var

        for window in date_windows(vars_by_date.keys(), constraint.within_days):
            window_vars = [v for d in window for v in vars_by_date[d].values()]
            if len(window_vars) > 1:
                model.add(cp_model.LinearExpr.Sum(window_vars) <= 1)


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
    fixed_fixture_keys = {(sf.fixture, sf.date) for sf in params.fixed_fixtures}

    for division_teams in teams_by_division.values():
        for home_team, away_team in itertools.permutations(division_teams, 2):
            fixture = Fixture(home_team=home_team, away_team=away_team)
            if fixture in excluded:
                continue
            is_internal = home_team.club == away_team.club
            for match_date in params.home_dates_for(home_team):
                if match_date in params.unavailable_away_dates_for(away_team):
                    continue
                if (
                    is_internal
                    and params.latest_internal_match_date is not None
                    and match_date > params.latest_internal_match_date
                ):
                    continue
                if (
                    params.earliest_match_date is not None
                    and match_date < params.earliest_match_date
                    and (fixture, match_date) not in fixed_fixture_keys
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
        # Each team can play at most one match in each window. date_windows groups
        # dates up to and including window_days apart, so pass min_gap_days - 1: a
        # gap of exactly min_gap_days (e.g. two matches a week apart when
        # min_gap_days=7) must be allowed, not treated as a violation.
        for window in date_windows(team_vars_by_date.keys(), params.min_gap_days - 1):
            window_vars = [v for d in window for v in team_vars_by_date[d]]
            model.add(cp_model.LinearExpr.Sum(window_vars) <= 1)

    for (club, match_date), club_home_date_vars in vars_by_club_home_date.items():
        # Each club can host at most max_concurrent_home_matches matches per date
        # (None means unlimited: no constraint to add).
        max_matches = params.max_concurrent_home_matches_for(club, match_date)
        if max_matches is not None:
            model.add(cp_model.LinearExpr.Sum(club_home_date_vars) <= max_matches)

    _add_max_home_dates_used_constraints(
        model, params.max_home_dates_used, vars_by_club_home_date
    )
    _add_avoid_coscheduling_constraints(
        model, params.avoid_coscheduling_teams, vars_by_team_date
    )
    _add_fixed_fixtures_constraints(model, params.fixed_fixtures, vars_by_fixture_date)

    # Guarded by isEnabledFor, not just left to logger.info's own lazy %-formatting,
    # since model_stats()/response_stats() themselves have a real (if modest) cost to
    # compute -- not just to format -- and that argument is evaluated eagerly either way.
    if logger.isEnabledFor(logging.INFO):
        logger.info("Model stats:\n%s", model.model_stats())

    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    if logger.isEnabledFor(logging.INFO):
        logger.info("Solve stats:\n%s", solver.response_stats())

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        result = []
        for (fixture, match_date), var in vars_by_fixture_date.items():
            if solver.BooleanValue(var):
                result.append(ScheduledFixture(fixture=fixture, date=match_date))
        return result
    else:
        raise ValueError(
            f"No solution found (solver status: {solver.StatusName(status)})"
        )
