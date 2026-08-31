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
import enum
import functools
import itertools
from collections.abc import Collection, Iterable, Mapping, MutableMapping
from datetime import date
from typing import Any

from ortools.sat.python import cp_model

import berger


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


@dataclasses.dataclass(frozen=True)
class SolveResult:
    """A solved schedule plus the OR-Tools model and solver summary text
    (model.model_stats() and solver.response_stats()).

    This is both what solve() returns and, via fixturesolution, exactly what a
    solution.yaml round-trips to: save_solution() writes one out and
    load_solution() reads one back, so the type is the single description of a
    solution's on-disk contents. The stats are plain strings -- persisting and
    rendering them (fixturesolution.save_solution into solution.yaml, the report's
    "Solver diagnostics" section) needs no structure. They default to "" for a
    solution file written before the text was recorded.

    spec_checksum, when set, is a self-describing digest ("sha256:<hex>") of the
    spec file this schedule was solved from -- see fixturespec.spec_checksum().
    solve() copies it straight off Parameters.spec_checksum, which
    fixturespec.load_spec() fills in; a solution file written before checksums
    were recorded loads as "". The report verifies it against the spec it's given.
    """

    fixtures: list[ScheduledFixture]
    model_stats: str = ""
    solve_stats: str = ""
    spec_checksum: str = ""


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
class HomeDatesUsedBounds:
    """Bounds on how many distinct dates a club actually hosts home matches on in
    the solved schedule (out of the dates offered in its home_dates).

    A `maximum` packs the club's home matches onto fewer dates -- more matches per
    evening on average; a `minimum` spreads them over more dates -- fewer per
    evening. Either bound may be None (unbounded on that side), but at least one
    must be set.
    """

    minimum: int | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        if self.minimum is None and self.maximum is None:
            raise ValueError("HomeDatesUsedBounds needs a minimum or a maximum")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError(
                f"HomeDatesUsedBounds minimum {self.minimum} exceeds "
                f"maximum {self.maximum}"
            )


class FixtureScheme(enum.Enum):
    """How a division's fixtures are generated.

    DOUBLE_ROUND: every pair of teams plays twice, once at each team's home venue
    -- the Middlesex League's default "Double Round All-Play-All" basis (League
    Rules 7c).

    SINGLE_ROUND: every pair plays once, with the home/away side of each match
    taken from the Berger table for the division's entrants (see berger.py). The
    draw order is the order of Division.teams. League Rules 7c switches a division
    to this basis once it has more than eight teams.
    """

    DOUBLE_ROUND = "double_round"
    SINGLE_ROUND = "single_round"


@dataclasses.dataclass(frozen=True)
class Division:
    """One division's teams and how its fixtures are generated -- a grouped,
    scheme-carrying view of Parameters.teams, derived by Parameters.divisions.

    `teams` is ordered: for FixtureScheme.SINGLE_ROUND it is the Berger table draw
    (teams[0] is table position 1, teams[1] position 2, and so on), which fixes the
    home/away side of every match; this order comes straight from Parameters.teams.
    """

    number: int
    scheme: FixtureScheme
    teams: tuple[Team, ...]

    def required_fixtures(self) -> list[Fixture]:
        """Every fixture that must appear in the schedule for this division: both
        directions of each pairing for DOUBLE_ROUND, one directed fixture per
        pairing (home/away from the Berger table) for SINGLE_ROUND."""
        if self.scheme is FixtureScheme.SINGLE_ROUND:
            pairs: Iterable[tuple[Team, Team]] = berger.single_round_pairings(
                self.teams
            )
        else:
            pairs = itertools.permutations(self.teams, 2)
        return [Fixture(home_team=home, away_team=away) for home, away in pairs]


class VenueScope(enum.Enum):
    """Which of a MatchCountLimit's teams' matches are counted towards its cap:
    only their home matches, only their away matches, or all of them."""

    HOME = "home"
    AWAY = "away"
    ALL = "all"


class ApplyPer(enum.Enum):
    """How a MatchCountLimit's `max` applies to its `teams`. ACROSS_TEAMS: one
    shared budget for the whole set (a venue capacity, or a keep-these-teams-apart
    rule). EACH_TEAM: the cap is enforced separately for every team in the set (a
    per-team gap -- `teams` is then typically every team of a club and `max` is 1).
    """

    EACH_TEAM = "each_team"
    ACROSS_TEAMS = "across_teams"


@dataclasses.dataclass(frozen=True)
class MatchCountLimit:
    """At most `max` matches involving `teams` may fall within any window of
    `time_window_days` consecutive calendar days. This is the sole match-count
    constraint mechanism -- a per-team gap ("each team plays at most once a week"),
    a venue's concurrent-match capacity ("at most two home matches a night"), and a
    keep-these-teams-apart rule are all expressed as instances of it.

    `time_window_days` counts a run of consecutive days, not a gap between two
    endpoints: `time_window_days=1` (the default) limits matches on a single date;
    `time_window_days=7` limits matches in any seven-consecutive-day period, so two
    matches exactly a week apart (day N and day N+7) never share a window and are
    unrestricted relative to each other.

    `apply_per` (see ApplyPer) decides whether `max` is one shared budget for the
    whole `teams` set (ACROSS_TEAMS, the default) or enforced separately per team
    (EACH_TEAM).

    `venue_scope` narrows which of those teams' matches count: VenueScope.HOME only
    their home matches, VenueScope.AWAY only their away matches, VenueScope.ALL (the
    default) every match they play. So an AWAY cap counts only the teams' away
    fixtures, still allowing one to play away on a night another is hosting.

    `max` may be None, meaning no cap from the plain value -- only useful alongside
    `date_max_overrides`, which replace `max` on specific dates (an int, or None to
    lift the cap that day). These per-date overrides are only meaningful, and only
    permitted, when `time_window_days` is 1, so each window is a single date.
    """

    teams: Collection[Team]
    max: int | None
    time_window_days: int = 1
    venue_scope: VenueScope = VenueScope.ALL
    apply_per: ApplyPer = ApplyPer.ACROSS_TEAMS
    date_max_overrides: Mapping[date, int | None] = dataclasses.field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        if self.date_max_overrides and self.time_window_days != 1:
            raise ValueError(
                "MatchCountLimit.date_max_overrides requires time_window_days == 1"
            )

    def max_for_window(self, window: Collection[date]) -> int | None:
        """The effective cap for one window: a `date_max_overrides` entry when the
        window is a single overridden date, otherwise `max`."""
        if self.date_max_overrides and len(window) == 1:
            (d,) = tuple(window)
            return self.date_max_overrides.get(d, self.max)
        return self.max


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
    home_dates_used: Mapping[ClubT, HomeDatesUsedBounds] = dataclasses.field(
        default_factory=dict
    )
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
    # Per-club cutoff: no fixture involving one of a listed club's teams -- home or
    # away -- may be scheduled after that club's date here. A club with no entry has
    # no such cutoff. Unlike latest_internal_match_date this covers every match the
    # club plays, not just same-club derbies; like it (and unlike earliest_match_date)
    # a fixed_fixtures entry past a club's cutoff is a contradiction, rejected by
    # fixturespec.load_spec().
    club_latest_match_date: Mapping[ClubT, date] = dataclasses.field(
        default_factory=dict
    )
    # Per-team overrides/additions to a club's home_dates/unavailable_away_dates, for
    # clubs whose teams don't all share the same availability (e.g. different squads
    # of players). A team not present here just uses its club's dates as before.
    team_home_dates: Mapping[Team, list[date]] = dataclasses.field(default_factory=dict)
    team_unavailable_away_dates: Mapping[Team, list[date]] = dataclasses.field(
        default_factory=dict
    )
    match_count_limits: Collection[MatchCountLimit] = ()
    # Fixture generation scheme per division number (see FixtureScheme); a division
    # with no entry here uses FixtureScheme.DOUBLE_ROUND. This is the only
    # per-division input -- the `divisions` view below, including each SINGLE_ROUND
    # division's Berger draw order, is derived from `teams` (grouped by
    # Team.division, keeping `teams` order), so `teams` order is significant for a
    # SINGLE_ROUND division, not just a listing convenience.
    division_schemes: Mapping[int, FixtureScheme] = dataclasses.field(
        default_factory=dict
    )
    # Pure provenance metadata: a self-describing digest ("sha256:<hex>") of the
    # spec file these parameters were loaded from, set by fixturespec.load_spec()
    # (see fixturespec.spec_checksum()). It has no effect on solving; solve() just
    # copies it onto its SolveResult so it can be written into solution.yaml.
    spec_checksum: str = ""

    def __post_init__(self) -> None:
        _check_no_duplicate_teams(self.teams)
        _check_no_duplicate_home_dates(self.home_dates)
        _check_no_duplicate_home_dates(self.team_home_dates)
        unknown = self.division_schemes.keys() - {t.division for t in self.teams}
        if unknown:
            raise ValueError(
                f"division_schemes has entries for division(s) {sorted(unknown)} "
                "with no teams"
            )

    @functools.cached_property
    def divisions(self) -> tuple[Division, ...]:
        """`teams` grouped into divisions, in first-seen order, each division's
        teams kept in `teams` order (a SINGLE_ROUND division's Berger draw) and its
        scheme taken from `division_schemes` (DOUBLE_ROUND if absent)."""
        by_number: dict[int, list[Team]] = {}
        for team in self.teams:
            by_number.setdefault(team.division, []).append(team)
        return tuple(
            Division(
                number=number,
                scheme=self.division_schemes.get(number, FixtureScheme.DOUBLE_ROUND),
                teams=tuple(division_teams),
            )
            for number, division_teams in by_number.items()
        )

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

    def latest_match_date_for(self, team: Team) -> date | None:
        """The last date `team` may play any match, home or away: its club's
        club_latest_match_date entry, or None if the club has no such cutoff."""
        return self.club_latest_match_date.get(team.club)


def date_windows(dates: Collection[date], window_days: int) -> list[frozenset[date]]:
    """Given a list of dates and a window size, return the maximal subsets of dates
    which fall within the window size. The bound is inclusive: two dates exactly
    `window_days` apart share a window. Callers enforcing a minimum gap of N days
    between events therefore pass `window_days = N - 1`, so that a gap of exactly N
    lands outside every window."""
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


class _FixtureVars:
    """Every candidate (fixture, date) decision variable in the model, kept in the
    several grouped forms the constraints read them back in.

    _build_model calls register() once per candidate (fixture, date) as it creates
    the variables; everything after that is read-only through the accessors. The
    point is to keep the fan-out -- which groups a new variable belongs to -- in
    one place, so _build_model and its constraint helpers read as constraints
    rather than bookkeeping.
    """

    def __init__(self) -> None:
        self._by_fixture_date: dict[tuple[Fixture, date], cp_model.IntVar] = {}
        self._by_fixture: MutableMapping[Fixture, list[cp_model.IntVar]] = (
            collections.defaultdict(list)
        )
        self._by_team_date: MutableMapping[
            Team, MutableMapping[date, list[cp_model.IntVar]]
        ] = collections.defaultdict(lambda: collections.defaultdict(list))
        # (club, date) -> vars for that club's home matches on that date (for the
        # home_dates_used bound).
        self._by_club_home_date: MutableMapping[
            tuple[ClubT, date], list[cp_model.IntVar]
        ] = collections.defaultdict(list)

    def register(
        self, fixture: Fixture, match_date: date, var: cp_model.IntVar
    ) -> None:
        """Record `var` as the decision variable for scheduling `fixture` on
        `match_date`, adding it to each grouped view. Raises if that (fixture,
        date) has already been registered (callers rely on the 1:1 mapping)."""
        key = (fixture, match_date)
        if key in self._by_fixture_date:
            raise ValueError(
                f"Duplicate variable for fixture {fixture.home_team.name} vs "
                f"{fixture.away_team.name} on {match_date.isoformat()}"
            )
        home, away = fixture.home_team, fixture.away_team
        self._by_fixture_date[key] = var
        self._by_fixture[fixture].append(var)
        self._by_team_date[home][match_date].append(var)
        self._by_team_date[away][match_date].append(var)
        self._by_club_home_date[home.club, match_date].append(var)

    def fixture_date_vars(self) -> Mapping[tuple[Fixture, date], cp_model.IntVar]:
        """The master map: the single bool var for each candidate (fixture, date)."""
        return self._by_fixture_date

    def per_fixture(self) -> Iterable[list[cp_model.IntVar]]:
        """Each fixture's vars over all its candidate dates (exactly one is true)."""
        return self._by_fixture.values()

    def by_club_home_date(
        self,
    ) -> Mapping[tuple[ClubT, date], list[cp_model.IntVar]]:
        """Per (club, date), the vars for that club's home matches on that date."""
        return self._by_club_home_date


def _add_home_dates_used_constraints(
    model: cp_model.CpModel,
    home_dates_used: Mapping[ClubT, HomeDatesUsedBounds],
    fixture_vars: _FixtureVars,
) -> None:
    """For each club in home_dates_used, bound the number of its home dates that
    end up hosting at least one match (below by `minimum`, above by `maximum`)."""
    vars_by_club_home_date = fixture_vars.by_club_home_date()
    # Collect the set of home dates per club that appear in the variable map
    clubs_home_dates: MutableMapping[str, list[date]] = collections.defaultdict(list)
    for club, d in vars_by_club_home_date:
        if club in home_dates_used:
            clubs_home_dates[club].append(d)

    for club, bounds in home_dates_used.items():
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
        total_used = cp_model.LinearExpr.Sum(date_used_vars)
        if bounds.maximum is not None:
            model.add(total_used <= bounds.maximum)
        if bounds.minimum is not None:
            model.add(total_used >= bounds.minimum)


def _add_match_count_limit_constraints(
    model: cp_model.CpModel,
    limits: Collection[MatchCountLimit],
    fixture_vars: _FixtureVars,
) -> None:
    """Apply every MatchCountLimit: no window of `time_window_days` consecutive days
    may hold more than the rule's effective cap (see max_for_window) matches from
    the counted set. `venue_scope` selects which of the teams' matches count (home
    only, away only, or all); `apply_per` decides whether `max` is a shared budget
    for the whole group or enforced separately per team.
    """
    fixture_date_vars = fixture_vars.fixture_date_vars()
    for rule in limits:
        count_home = rule.venue_scope in (VenueScope.HOME, VenueScope.ALL)
        count_away = rule.venue_scope in (VenueScope.AWAY, VenueScope.ALL)
        each_team = rule.apply_per is ApplyPer.EACH_TEAM
        groups = [{team} for team in rule.teams] if each_team else [set(rule.teams)]
        for team_set in groups:
            # Each (fixture, date) maps to a single variable, visited once here, so
            # a match between two teams both in `team_set` is counted once.
            vars_by_date: MutableMapping[date, list[cp_model.IntVar]] = (
                collections.defaultdict(list)
            )
            for (fixture, d), var in fixture_date_vars.items():
                if (count_home and fixture.home_team in team_set) or (
                    count_away and fixture.away_team in team_set
                ):
                    vars_by_date[d].append(var)

            # date_windows groups dates spanning up to its window arg in days, so
            # pass time_window_days - 1: a window is then any run of
            # time_window_days consecutive calendar days, and two dates exactly
            # time_window_days apart (e.g. a week apart when time_window_days=7)
            # fall in separate windows. time_window_days=1 gives one window per
            # date.
            for window in date_windows(vars_by_date.keys(), rule.time_window_days - 1):
                cap = rule.max_for_window(window)
                if cap is None:
                    continue
                # A shared-budget cap that is >= the group size can never bind: the
                # teams can't play more simultaneous matches than there are of them
                # (this is how a stated venue capacity above a club's team count
                # stays a no-op).
                if not each_team and cap >= len(team_set):
                    continue
                window_vars = [v for d in window for v in vars_by_date[d]]
                if len(window_vars) > cap:
                    model.add(cp_model.LinearExpr.Sum(window_vars) <= cap)


def _add_fixed_fixtures_constraints(
    model: cp_model.CpModel,
    fixed_fixtures: Collection[ScheduledFixture],
    fixture_vars: _FixtureVars,
) -> None:
    """Force each pre-specified fixture onto its given date."""
    vars_by_fixture_date = fixture_vars.fixture_date_vars()
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
                "isn't also in excluded_fixtures, that the date isn't after either "
                "club's latest_match_date, and -- if the two teams share a club -- "
                "that the date isn't after latest_internal_match_date)"
            )
        model.add(var == 1)


def _build_model(params: Parameters) -> tuple[cp_model.CpModel, _FixtureVars]:
    """Build the CP-SAT model for params, returning it with the _FixtureVars the
    schedule is read back off after solving."""
    model = cp_model.CpModel()
    fixture_vars = _FixtureVars()

    excluded = set(params.excluded_fixtures)
    fixed_fixture_keys = {(sf.fixture, sf.date) for sf in params.fixed_fixtures}

    for division in params.divisions:
        for fixture in division.required_fixtures():
            if fixture in excluded:
                continue
            home_team = fixture.home_team
            away_team = fixture.away_team
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
                home_cutoff = params.latest_match_date_for(home_team)
                away_cutoff = params.latest_match_date_for(away_team)
                if home_cutoff is not None and match_date > home_cutoff:
                    continue
                if away_cutoff is not None and match_date > away_cutoff:
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
                fixture_vars.register(fixture, match_date, var)

    for scheduled_vars in fixture_vars.per_fixture():
        # Each fixture must be scheduled exactly once
        model.add(cp_model.LinearExpr.Sum(scheduled_vars) == 1)

    _add_home_dates_used_constraints(model, params.home_dates_used, fixture_vars)
    _add_match_count_limit_constraints(model, params.match_count_limits, fixture_vars)
    _add_fixed_fixtures_constraints(model, params.fixed_fixtures, fixture_vars)

    return model, fixture_vars


def _extract_fixtures(
    solver: cp_model.CpSolver, fixture_vars: _FixtureVars
) -> list[ScheduledFixture]:
    """The scheduled fixtures of a solved model: every (fixture, date) whose
    bool var the solver set to true."""
    return [
        ScheduledFixture(fixture=fixture, date=match_date)
        for (fixture, match_date), var in fixture_vars.fixture_date_vars().items()
        if solver.BooleanValue(var)
    ]


def solve(params: Parameters) -> SolveResult:
    """Solve params, returning the scheduled fixtures together with the OR-Tools
    model and solver summary text (see SolveResult). Raises ValueError if the
    solver finds no feasible schedule.

    The two stats blocks are always captured -- they're cheap next to solving
    itself -- so callers can persist them next to the schedule
    (fixturesolution.save_solution writes them into solution.yaml, and the report
    shows them in its "Solver diagnostics" section).
    """
    model, fixture_vars = _build_model(params)
    model_stats = model.model_stats()

    solver = cp_model.CpSolver()
    status = solver.Solve(model)
    solve_stats = solver.response_stats()

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise ValueError(
            f"No solution found (solver status: {solver.StatusName(status)})"
        )

    return SolveResult(
        fixtures=_extract_fixtures(solver, fixture_vars),
        model_stats=model_stats,
        solve_stats=solve_stats,
        spec_checksum=params.spec_checksum,
    )
