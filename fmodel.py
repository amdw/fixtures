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


class ConcurrencyScope(enum.Enum):
    """Which of a club's matches on a date a MaxConcurrentMatches limit counts:
    HOME only its home matches, AWAY only its away matches, ANY every match its
    teams play (an internal match -- both teams the club's -- counted once).

    Parallel to CoschedulingScope but kept separate: that one's third value is
    BOTH ("home") and it names a different feature.
    """

    HOME = "home"
    AWAY = "away"
    ANY = "any"


@dataclasses.dataclass(frozen=True)
class ConcurrencyLimit:
    """A match-count limit for one ConcurrencyScope: a default, overridable for
    specific dates. A value of None (for the default or an override) means no limit
    is imposed by this mechanism -- e.g. for a club whose concurrency is already
    bounded by another constraint (such as avoid_coscheduling_teams), where adding
    an explicit number here would just be a redundant, easy-to-forget-to-update
    restatement of that bound.
    """

    default: int | None
    overrides: Mapping[date, int | None] = dataclasses.field(default_factory=dict)

    def for_date(self, d: date) -> int | None:
        return self.overrides.get(d, self.default)


@dataclasses.dataclass(frozen=True)
class MaxConcurrentMatches:
    """A club's per-scope limits on how many matches its teams may play on the same
    date. Each ConcurrencyScope present in `by_scope` has its own ConcurrencyLimit;
    a scope with no entry imposes no limit. See ConcurrencyScope for what each
    scope counts.
    """

    by_scope: Mapping[ConcurrencyScope, ConcurrencyLimit] = dataclasses.field(
        default_factory=dict
    )

    def for_date(self, scope: ConcurrencyScope, d: date) -> int | None:
        limit = self.by_scope.get(scope)
        return None if limit is None else limit.for_date(d)


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


class CoschedulingScope(enum.Enum):
    """Which of an AvoidCoschedulingConstraint's teams' matches are counted towards
    its limit: only their home matches, only their away matches, or both."""

    HOME = "home"
    AWAY = "away"
    BOTH = "both"


@dataclasses.dataclass(frozen=True)
class AvoidCoschedulingConstraint:
    """Any two matches involving `teams` must be scheduled at least `min_gap_days`
    days apart -- i.e. a separation of exactly `min_gap_days` days is allowed, and
    only shorter gaps are forbidden. This is the per-group counterpart of
    Parameters.min_gap_days (which applies to every team); min_gap_days=1 (the
    default) and min_gap_days=0 both mean simply that no two of them may share a
    date. Typically used for a club's own teams that draw from the same pool of
    players (e.g. adjacent-division teams), but not restricted to that.

    `applies_to` narrows which of those teams' matches count: CoschedulingScope.HOME
    only their home matches, CoschedulingScope.AWAY only their away matches,
    CoschedulingScope.BOTH (the default) every match they play. So an AWAY constraint
    keeps the teams' away fixtures on separate dates while still allowing one to play
    away on a night another is hosting.
    """

    teams: Collection[Team]
    min_gap_days: int = 1
    applies_to: CoschedulingScope = CoschedulingScope.BOTH


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
    min_gap_days: int = 7
    # Per-club overrides of min_gap_days: a club listed here uses its value for the
    # minimum gap between any two matches involving one of its teams, in place of
    # the spec-wide min_gap_days. A club not present here uses min_gap_days as
    # before. (This is about the every-team window; the per-group
    # avoid_coscheduling_teams constraints are separate and always additive.)
    club_min_gap_days: Mapping[ClubT, int] = dataclasses.field(default_factory=dict)
    max_concurrent_matches: Mapping[ClubT, MaxConcurrentMatches] = dataclasses.field(
        default_factory=dict
    )
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
    # Per-team overrides/additions to a club's home_dates/unavailable_away_dates, for
    # clubs whose teams don't all share the same availability (e.g. different squads
    # of players). A team not present here just uses its club's dates as before.
    team_home_dates: Mapping[Team, list[date]] = dataclasses.field(default_factory=dict)
    team_unavailable_away_dates: Mapping[Team, list[date]] = dataclasses.field(
        default_factory=dict
    )
    avoid_coscheduling_teams: Collection[AvoidCoschedulingConstraint] = ()
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

    @functools.cached_property
    def _teams_per_club(self) -> Mapping[ClubT, int]:
        return collections.Counter(team.club for team in self.teams)

    def max_concurrent_matches_for(
        self, club: ClubT, scope: ConcurrencyScope, d: date
    ) -> int | None:
        """The most matches of `scope` (see ConcurrencyScope) `club` may play on
        date `d`, or None if unlimited.

        A configured limit that is >= the club's own number of teams is reported as
        unlimited too: the club can never play more simultaneous matches (of any
        scope) than it has teams, so such a limit could never actually bind.
        """
        entry = self.max_concurrent_matches.get(club)
        if entry is None:
            return None
        limit = entry.for_date(scope, d)
        if limit is not None and limit >= self._teams_per_club[club]:
            return None
        return limit

    def min_gap_days_for(self, team: Team) -> int:
        """The minimum gap in days between any two matches involving `team`: its
        club's club_min_gap_days override if it has one, otherwise the spec-wide
        min_gap_days."""
        return self.club_min_gap_days.get(team.club, self.min_gap_days)

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
        # One (club, date) -> vars map per ConcurrencyScope (see ConcurrencyScope
        # for what each counts).
        self._by_club_date: Mapping[
            ConcurrencyScope, MutableMapping[tuple[ClubT, date], list[cp_model.IntVar]]
        ] = {scope: collections.defaultdict(list) for scope in ConcurrencyScope}

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
        self._by_club_date[ConcurrencyScope.HOME][home.club, match_date].append(var)
        self._by_club_date[ConcurrencyScope.AWAY][away.club, match_date].append(var)
        self._by_club_date[ConcurrencyScope.ANY][home.club, match_date].append(var)
        if away.club != home.club:
            self._by_club_date[ConcurrencyScope.ANY][away.club, match_date].append(var)

    def fixture_date_vars(self) -> Mapping[tuple[Fixture, date], cp_model.IntVar]:
        """The master map: the single bool var for each candidate (fixture, date)."""
        return self._by_fixture_date

    def per_fixture(self) -> Iterable[list[cp_model.IntVar]]:
        """Each fixture's vars over all its candidate dates (exactly one is true)."""
        return self._by_fixture.values()

    def per_team_dates(
        self,
    ) -> Iterable[tuple[Team, Mapping[date, list[cp_model.IntVar]]]]:
        """Per team, the team paired with its vars grouped by date (for the
        min-gap window limit, whose length can vary by the team's club)."""
        return self._by_team_date.items()

    def by_club_date(
        self, scope: ConcurrencyScope
    ) -> Mapping[tuple[ClubT, date], list[cp_model.IntVar]]:
        """Per (club, date), the vars counting towards `scope` (see ConcurrencyScope)."""
        return self._by_club_date[scope]


def _add_home_dates_used_constraints(
    model: cp_model.CpModel,
    home_dates_used: Mapping[ClubT, HomeDatesUsedBounds],
    fixture_vars: _FixtureVars,
) -> None:
    """For each club in home_dates_used, bound the number of its home dates that
    end up hosting at least one match (below by `minimum`, above by `maximum`)."""
    vars_by_club_home_date = fixture_vars.by_club_date(ConcurrencyScope.HOME)
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


def _add_avoid_coscheduling_constraints(
    model: cp_model.CpModel,
    constraints: Collection[AvoidCoschedulingConstraint],
    fixture_vars: _FixtureVars,
) -> None:
    """For each AvoidCoschedulingConstraint, ensure any two matches involving its
    teams are scheduled at least min_gap_days days apart (a gap of exactly
    min_gap_days days is allowed; only shorter gaps are forbidden). The constraint's
    `applies_to` scope limits which of those teams' matches are counted -- home only,
    away only, or (by default) both.
    """
    for constraint in constraints:
        team_set = set(constraint.teams)
        count_home = constraint.applies_to in (
            CoschedulingScope.HOME,
            CoschedulingScope.BOTH,
        )
        count_away = constraint.applies_to in (
            CoschedulingScope.AWAY,
            CoschedulingScope.BOTH,
        )
        # Each (fixture, date) maps to a single variable, visited once here, so a
        # match between two teams both in `constraint.teams` is counted once.
        vars_by_date: MutableMapping[date, list[cp_model.IntVar]] = (
            collections.defaultdict(list)
        )
        for (fixture, d), var in fixture_vars.fixture_date_vars().items():
            if (count_home and fixture.home_team in team_set) or (
                count_away and fixture.away_team in team_set
            ):
                vars_by_date[d].append(var)

        # date_windows groups dates up to and including its window arg apart, so
        # pass min_gap_days - 1: a separation of exactly min_gap_days (e.g. two
        # matches a week apart when min_gap_days=7) must be allowed, not treated as
        # a violation -- the same convention as the per-team min_gap_days constraint
        # in _build_model. min_gap_days <= 1 still forbids sharing a date (each
        # date's own singleton window collects every counted match on it).
        for window in date_windows(vars_by_date.keys(), constraint.min_gap_days - 1):
            window_vars = [v for d in window for v in vars_by_date[d]]
            if len(window_vars) > 1:
                model.add(cp_model.LinearExpr.Sum(window_vars) <= 1)


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
                "isn't also in excluded_fixtures, and -- if the two teams share a "
                "club -- that the date isn't after latest_internal_match_date)"
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

    for team, team_date_vars in fixture_vars.per_team_dates():
        # Each team can play at most one match in each window. date_windows groups
        # dates up to and including window_days apart, so pass min_gap_days - 1: a
        # gap of exactly min_gap_days (e.g. two matches a week apart when
        # min_gap_days=7) must be allowed, not treated as a violation. The gap can
        # be overridden per club (club_min_gap_days), so resolve it per team.
        gap = params.min_gap_days_for(team)
        for window in date_windows(team_date_vars.keys(), gap - 1):
            window_vars = [v for d in window for v in team_date_vars[d]]
            model.add(cp_model.LinearExpr.Sum(window_vars) <= 1)

    for scope in ConcurrencyScope:
        for (club, match_date), club_date_vars in fixture_vars.by_club_date(
            scope
        ).items():
            # Each club may play at most max_concurrent_matches matches of this
            # scope per date (None means unlimited: no constraint to add).
            max_matches = params.max_concurrent_matches_for(club, scope, match_date)
            if max_matches is not None:
                model.add(cp_model.LinearExpr.Sum(club_date_vars) <= max_matches)

    _add_home_dates_used_constraints(model, params.home_dates_used, fixture_vars)
    _add_avoid_coscheduling_constraints(
        model, params.avoid_coscheduling_teams, fixture_vars
    )
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
