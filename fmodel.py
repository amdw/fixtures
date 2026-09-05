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

import abc
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

    expected_invalid_reason is never set by solve() -- it's a hand-written
    annotation on a solution.yaml that is being deliberately kept even though it
    no longer satisfies its spec's constraints (e.g. a schedule kept for
    reference after tightening a constraint, or one that pins a known solver
    bug). "" (the default) means the solution is expected to validate normally.
    See check_schedule, validate.py and report.py, which all treat a mismatch
    between this and the actual outcome as worth flagging.
    """

    fixtures: list[ScheduledFixture]
    model_stats: str = ""
    solve_stats: str = ""
    spec_checksum: str = ""
    expected_invalid_reason: str = ""


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


@dataclasses.dataclass(frozen=True)
class DateRange:
    """An inclusive span of calendar dates, `start` on or before `end`. Either may
    be None for an open-ended bound (no earliest, or no latest, date) -- but not
    both, since an unbounded-on-both-sides range is never what's meant.

    Used by RangeLimit.ranges to pin a cap to specific calendar periods (a
    school-holiday week, say) rather than a rolling window. `d in date_range`
    tests membership.
    """

    start: date | None = None
    end: date | None = None

    def __post_init__(self) -> None:
        if self.start is None and self.end is None:
            raise ValueError("DateRange needs a start and/or an end")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError(
                f"DateRange start {self.start.isoformat()} is after end "
                f"{self.end.isoformat()}"
            )

    def __contains__(self, d: object) -> bool:
        if not isinstance(d, date):
            return False
        if self.start is not None and d < self.start:
            return False
        if self.end is not None and d > self.end:
            return False
        return True


class VenueScope(enum.Enum):
    """Which of a MatchCountLimit's teams' matches are counted towards its cap:
    only their home matches, only their away matches, or all of them."""

    HOME = "home"
    AWAY = "away"
    ALL = "all"


class ApplyPer(enum.Enum):
    """How a MatchCountLimit's caps (`match_max`/`match_min`/`playing_teams_max`/
    `playing_teams_min`) apply to its `teams`. ACROSS_TEAMS: one shared budget for
    the whole set (a venue capacity, or a keep-these-teams-apart rule). EACH_TEAM:
    the cap is enforced separately for every team in the set (a per-team gap --
    `teams` is then typically every team of a club and `match_max` is `Cap(1)`).
    """

    EACH_TEAM = "each_team"
    ACROSS_TEAMS = "across_teams"


def _fixture_counts_for_scope(
    fixture: Fixture, teams: Collection[Team], venue_scope: VenueScope
) -> bool:
    """Whether a MatchCountLimit over `teams` with `venue_scope` counts `fixture`.

    HOME / ALL scope: the fixture's home team is one of `teams`.
    AWAY / ALL scope: its away team is one of `teams` -- except that under AWAY
    scope an internal match (both teams the same club) never counts: that "away"
    team is playing at its own club's venue, so it is not away in the sense an
    away-load cap is about. Such a match still counts under HOME or ALL scope via
    the home-team test (its players are committed either way).
    """
    if venue_scope in (VenueScope.HOME, VenueScope.ALL) and fixture.home_team in teams:
        return True
    if venue_scope in (VenueScope.AWAY, VenueScope.ALL) and fixture.away_team in teams:
        internal = fixture.home_team.club == fixture.away_team.club
        return not (venue_scope is VenueScope.AWAY and internal)
    return False


@dataclasses.dataclass(frozen=True)
class Cap:
    """A per-window integer bound: `base` (None = unbounded) together with
    optional per-date `overrides`. Used as either a ceiling (MatchCountLimit's
    `match_max`/`playing_teams_max`) or a floor (`match_min`/`playing_teams_min`)
    -- the direction lives in which field it's assigned to, not in Cap itself.

    An override applies only where a window is a single date (see for_window) --
    it names that date's replacement bound (an int, or None to lift the bound for
    that date). Overrides are therefore only meaningful, and only accepted, on a
    RollingLimit with window_days == 1: an override names one date, which lines up
    with exactly one window only then.

    A Cap does the same window/override resolution for both of a MatchCountLimit's
    measures -- how many matches count, and how many teams'-worth of players they
    ask for -- rather than each duplicating it.
    """

    base: int | None = None
    overrides: Mapping[date, int | None] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.base is not None and self.base < 0:
            raise ValueError(f"Cap base must be >= 0 or None, got {self.base}")
        for d, value in self.overrides.items():
            if value is not None and value < 0:
                raise ValueError(
                    f"Cap override for {d.isoformat()} must be >= 0 or None, got "
                    f"{value}"
                )

    def for_window(self, window: Collection[date]) -> int | None:
        """This cap's effective bound for `window`: an `overrides` entry when the
        window is a single overridden date, otherwise `base`."""
        if self.overrides and len(window) == 1:
            (d,) = tuple(window)
            return self.overrides.get(d, self.base)
        return self.base

    def zero_on(self, d: date) -> bool:
        """Whether this cap is an effective 0 on `d`: a `base` of 0 (every day), or
        an `overrides` entry of 0 for `d` specifically. Feeds the up-front
        candidate pruning in MatchCountLimit.forbids (a ceiling-only concept -- see
        MatchCountLimit._active_max_caps)."""
        return self.base == 0 or self.overrides.get(d) == 0


class _Measure(enum.Enum):
    """The quantity one of a MatchCountLimit's Caps bounds within a window.

    MATCHES counts each counted candidate once. PLAYING_TEAMS counts how many of
    the rule's `teams` that candidate puts on to play: one for an ordinary match,
    two for an internal match between two of `teams` (e.g. a same-club derby) --
    see _FixtureVars.teams_playing.
    """

    MATCHES = "matches"
    PLAYING_TEAMS = "playing_teams"

    def term(
        self,
        fixture_vars: _FixtureVars,
        fixture: Fixture,
        match_date: date,
        team_set: Collection[Team],
    ) -> cp_model.LinearExprT:
        """This measure's contribution from one counted (fixture, match_date)
        candidate, as a linear expression in that candidate's decision variable."""
        if self is _Measure.MATCHES:
            return fixture_vars.fixture_date_vars()[fixture, match_date]
        return fixture_vars.teams_playing(fixture, match_date, team_set)


@dataclasses.dataclass(frozen=True, kw_only=True)
class MatchCountLimit(abc.ABC):
    """A cap on how many of `teams`' matches -- and/or how many teams'-worth of
    their players -- may fall within a window of dates. This is the sole
    match-count constraint mechanism: a per-team gap ("each team plays at most
    once a week"), a venue's concurrent-match capacity ("at most two home matches
    a night"), a keep-these-teams-apart rule and a school-holiday blackout are all
    expressed as one of the two concrete subclasses below, which differ only in
    how a window is defined:

      - RollingLimit: every run of `window_days` consecutive calendar days
        (window_days == 1, the default, is one window per date).
      - RangeLimit: exactly the given explicit calendar ranges, each counted
        independently -- for a limit tied to specific weeks (a school-holiday
        week, a congress fortnight) that a rolling window can't express.

    Everything else is shared here:

      `teams` is the set whose matches are counted. `venue_scope` (see VenueScope)
      narrows which of those matches count: HOME only their home matches, AWAY
      only their away matches, ALL (the default) every match they play. An
      internal match (both teams the same club) is never counted under AWAY
      scope -- its "away" team plays at its own club's venue -- but still counts
      under HOME and ALL.

      `apply_per` (see ApplyPer) decides whether the caps below are one shared
      budget for the whole `teams` set (ACROSS_TEAMS, the default) or enforced
      separately for every team (EACH_TEAM -- `teams` is then typically a whole
      club and the cap a per-team gap).

      `match_max` caps the number of counted matches in a window from above;
      `match_min` floors it from below (e.g. "each team must play at least once
      every 14 days"). `playing_teams_max`/`playing_teams_min` do the same for how
      many teams'-worth of players a window asks for -- each counted match
      contributes one, or two if it's an internal match between two of `teams`
      (e.g. a same-club derby counted by that club's own venue-capacity rule): one
      entry towards the matches measure, but two teams from the set on to play.
      Any of the four may be None (that bound left unset); at least one must be
      set. A venue that can physically host 3 simultaneous matches but only has 3
      teams' worth of players to field wants both `match_max=Cap(3)` and
      `playing_teams_max=Cap(3)` -- a matches cap alone can't tell that 3 matches,
      one an internal derby, need 4 teams' worth of players. Over a window
      spanning more than a single date, the same pair meeting twice counts twice
      towards a playing-teams bound: it is a running tally of teams'-worth of
      players asked to play, not a count of distinct teams touched.

      A floor of 0 is never meaningful (it can never bind), so `match_min`/
      `playing_teams_min` are expected to always carry a `base`/override `>= 1`;
      unlike the max side, callers are responsible for that -- Cap itself only
      rejects negative values. When both a max and a min are set for the same
      measure and both have a `base`, the min's `base` may not exceed the max's.
    """

    teams: Collection[Team]
    match_max: Cap | None = None
    match_min: Cap | None = None
    playing_teams_max: Cap | None = None
    playing_teams_min: Cap | None = None
    venue_scope: VenueScope = VenueScope.ALL
    apply_per: ApplyPer = ApplyPer.ACROSS_TEAMS

    def __post_init__(self) -> None:
        if (
            self.match_max is None
            and self.match_min is None
            and self.playing_teams_max is None
            and self.playing_teams_min is None
        ):
            raise ValueError(
                "MatchCountLimit needs at least one of match_max, match_min, "
                "playing_teams_max or playing_teams_min"
            )
        for label, max_cap, min_cap in (
            ("match", self.match_max, self.match_min),
            ("playing_teams", self.playing_teams_max, self.playing_teams_min),
        ):
            if (
                max_cap is not None
                and min_cap is not None
                and max_cap.base is not None
                and min_cap.base is not None
                and min_cap.base > max_cap.base
            ):
                raise ValueError(
                    f"{label}_min base {min_cap.base} exceeds {label}_max base "
                    f"{max_cap.base}"
                )

    def counts_fixture(self, fixture: Fixture) -> bool:
        """Whether this limit counts `fixture` at all: its home team (for a HOME or
        ALL scope) or away team (AWAY or ALL) is among `teams` -- except that an
        internal match never counts under AWAY scope (see _fixture_counts_for_scope
        and the `venue_scope` note above)."""
        return _fixture_counts_for_scope(fixture, set(self.teams), self.venue_scope)

    def forbids(self, fixture: Fixture, d: date) -> bool:
        """Whether this limit makes `fixture` on `d` outright impossible -- an
        effective cap of 0 over a window that covers `d`. _build_model skips
        creating a decision variable for such a candidate (it could only ever be
        0)."""
        return self.counts_fixture(fixture) and self._covers_zero(d)

    def add_to_model(self, model: cp_model.CpModel, fixture_vars: _FixtureVars) -> None:
        """Emit this limit's constraints into `model`: for each team group (one per
        team under EACH_TEAM, else the whole set) and each of this rule's windows,
        bound every cap it carries over the counted candidates falling in that
        window."""
        fixture_date_vars = fixture_vars.fixture_date_vars()
        for team_set in self._team_groups():
            # Every candidate this rule counts (per its venue_scope), grouped by
            # date. A match between two teams both in `team_set` (e.g. a same-club
            # derby counted by that club's own rule) appears once here, under one
            # variable -- counted once towards the matches measure below, but
            # twice towards the playing-teams measure (see
            # _Measure.PLAYING_TEAMS), since it puts two of `team_set` on to play.
            counted_by_date = self._counted_by_date(
                _counted_fixtures_by_date(fixture_date_vars, team_set, self.venue_scope)
            )
            for window in self._windows(list(counted_by_date)):
                window_fixture_dates = [
                    (fixture, d) for d in window for fixture in counted_by_date[d]
                ]
                for measure, cap in self._active_max_caps():
                    ceiling = cap.for_window(window)
                    if ceiling is None:
                        continue
                    # A shared-budget matches cap that is >= the group size can
                    # never bind on a single instant -- the teams can't play more
                    # simultaneous matches than there are of them (this is how a
                    # venue capacity stated above a club's team count stays a
                    # no-op). Subclasses/measures that can't make that guarantee
                    # (a per-team cap, a playing-teams cap, or a window spanning
                    # more than one date) report no such shortcut (None).
                    skip_size = self._trivial_skip_size(measure, team_set)
                    if skip_size is not None and ceiling >= skip_size:
                        continue
                    terms = [
                        measure.term(fixture_vars, fixture, d, team_set)
                        for fixture, d in window_fixture_dates
                    ]
                    if not terms:
                        continue
                    if measure is _Measure.MATCHES and len(terms) <= ceiling:
                        continue
                    model.add(cp_model.LinearExpr.Sum(terms) <= ceiling)
                for measure, cap in self._active_min_caps():
                    floor = cap.for_window(window)
                    if floor is None:
                        continue
                    terms = [
                        measure.term(fixture_vars, fixture, d, team_set)
                        for fixture, d in window_fixture_dates
                    ]
                    # Unlike the max side, an empty `terms` here is not a no-op --
                    # a floor with nothing at all to count towards it is genuinely
                    # infeasible, and the constraint must still be added so the
                    # solver reports that rather than silently ignoring it.
                    model.add(cp_model.LinearExpr.Sum(terms) >= floor)

    def _team_groups(self) -> list[set[Team]]:
        """The groups this rule's caps apply to: one singleton set per team under
        EACH_TEAM, or the whole `teams` set as one shared-budget group."""
        if self.apply_per is ApplyPer.EACH_TEAM:
            return [{team} for team in self.teams]
        return [set(self.teams)]

    def _active_max_caps(self) -> Iterable[tuple[_Measure, Cap]]:
        """This rule's set ceilings, paired with the measure each bounds. Used by
        add_to_model and by forbids/_covers_zero -- a floor can never make a
        single candidate impossible outright the way a ceiling of 0 does, so
        those stay ceiling-only."""
        if self.match_max is not None:
            yield _Measure.MATCHES, self.match_max
        if self.playing_teams_max is not None:
            yield _Measure.PLAYING_TEAMS, self.playing_teams_max

    def _active_min_caps(self) -> Iterable[tuple[_Measure, Cap]]:
        """This rule's set floors, paired with the measure each bounds."""
        if self.match_min is not None:
            yield _Measure.MATCHES, self.match_min
        if self.playing_teams_min is not None:
            yield _Measure.PLAYING_TEAMS, self.playing_teams_min

    def _counted_by_date(
        self, counted_by_date: Mapping[date, list[Fixture]]
    ) -> Mapping[date, list[Fixture]]:
        """Hook for a subclass to drop dates from the counted set before windows
        are formed (RollingLimit applies `exclude_dates` here). The base keeps
        every date as counted."""
        return counted_by_date

    def _trivial_skip_size(
        self, measure: _Measure, team_set: Collection[Team]
    ) -> int | None:
        """The group size at or above which `measure`'s cap provably can't bind for
        this rule, or None if no such shortcut is sound here. See add_to_model."""
        return None

    @abc.abstractmethod
    def _windows(self, counted_dates: Collection[date]) -> Iterable[Collection[date]]:
        """The windows this rule enforces its caps over, given the dates on which
        it counts at least one candidate."""

    @abc.abstractmethod
    def _covers_zero(self, d: date) -> bool:
        """Whether some cap of this rule is an effective 0 over a window covering
        `d` -- so a counted candidate on `d` could only ever be 0. See forbids."""


@dataclasses.dataclass(frozen=True, kw_only=True)
class RollingLimit(MatchCountLimit):
    """A MatchCountLimit whose windows are every run of `window_days` consecutive
    calendar days. `window_days` counts a run of days, not a gap between two
    endpoints: `window_days=1` (the default) limits matches on a single date;
    `window_days=7` limits matches in any seven-consecutive-day period, so two
    matches exactly a week apart (day N and day N+7) never share a window and are
    unrestricted relative to each other.

    `exclude_dates` drops every counted match falling on one of the listed dates
    from this rule: each window is evaluated as if nothing counted happened then,
    for every one of `match_max`/`match_min`/`playing_teams_max`/`playing_teams_min`.
    Unlike a Cap override it is not tied to a single-date window, so it is the way
    to exempt one date from a multi-day rolling cap -- typically a date whose load
    is already pinned by `fixed_fixtures` and bounded by its own Cap override.

    A Cap with per-date `overrides` requires `window_days == 1` (see Cap).
    """

    window_days: int = 1
    exclude_dates: frozenset[date] = frozenset()

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.window_days < 1:
            raise ValueError(
                f"RollingLimit.window_days must be >= 1, got {self.window_days}"
            )
        if self.window_days != 1 and any(
            cap.overrides
            for _, cap in itertools.chain(
                self._active_max_caps(), self._active_min_caps()
            )
        ):
            raise ValueError(
                "RollingLimit per-date Cap overrides require window_days == 1"
            )

    def _windows(self, counted_dates: Collection[date]) -> Iterable[Collection[date]]:
        return date_windows(counted_dates, self.window_days - 1)

    def _counted_by_date(
        self, counted_by_date: Mapping[date, list[Fixture]]
    ) -> Mapping[date, list[Fixture]]:
        if not self.exclude_dates:
            return counted_by_date
        return {
            d: fixtures
            for d, fixtures in counted_by_date.items()
            if d not in self.exclude_dates
        }

    def _trivial_skip_size(
        self, measure: _Measure, team_set: Collection[Team]
    ) -> int | None:
        if measure is _Measure.MATCHES and self.apply_per is ApplyPer.ACROSS_TEAMS:
            return len(team_set)
        return None

    def _covers_zero(self, d: date) -> bool:
        return any(cap.zero_on(d) for _, cap in self._active_max_caps())


@dataclasses.dataclass(frozen=True, kw_only=True)
class RangeLimit(MatchCountLimit):
    """A MatchCountLimit whose windows are exactly the given inclusive calendar
    `ranges`, each counted independently, instead of a rolling window. A
    `match_max` of `Cap(0)` bars every counted match across the ranges -- a
    whole-club, all-teams RangeLimit with `match_max=Cap(0)` is how a spec-wide
    "nobody plays these dates" block is expressed. A `match_min`/`playing_teams_min`
    instead requires at least that many within each range.

    Caps here carry no per-date overrides, and there is no `exclude_dates`: both
    are meaningless once the windows are already explicit calendar ranges --
    leave the date out of `ranges` instead.
    """

    ranges: tuple[DateRange, ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.ranges:
            raise ValueError("RangeLimit needs at least one DateRange")
        if any(
            cap.overrides
            for _, cap in itertools.chain(
                self._active_max_caps(), self._active_min_caps()
            )
        ):
            raise ValueError(
                "RangeLimit caps can't carry per-date overrides (leave the date "
                "out of the ranges instead)"
            )

    def _windows(self, counted_dates: Collection[date]) -> Iterable[Collection[date]]:
        dates = list(counted_dates)
        return [[d for d in dates if d in rng] for rng in self.ranges]

    def _covers_zero(self, d: date) -> bool:
        return any(cap.zero_on(d) for _, cap in self._active_max_caps()) and any(
            d in rng for rng in self.ranges
        )


type MatchLimit = RollingLimit | RangeLimit


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
    match_count_limits: Collection[MatchLimit] = ()
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
        # (team, date) -> vars for every match -- home or away -- that team could
        # play on that date (at most one may end up true: see
        # _add_one_match_per_team_per_date_constraints).
        self._by_team_date: MutableMapping[tuple[Team, date], list[cp_model.IntVar]] = (
            collections.defaultdict(list)
        )
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
        self._by_team_date[home, match_date].append(var)
        self._by_team_date[away, match_date].append(var)
        self._by_club_home_date[home.club, match_date].append(var)

    def fixture_date_vars(self) -> Mapping[tuple[Fixture, date], cp_model.IntVar]:
        """The master map: the single bool var for each candidate (fixture, date)."""
        return self._by_fixture_date

    def vars_for_fixture(self, fixture: Fixture) -> list[cp_model.IntVar]:
        """`fixture`'s vars over all its candidate dates (exactly one is true in a
        solved model). Empty if every candidate date was filtered out while
        building -- the caller must treat that as the schedule being infeasible,
        since nothing else records that the fixture is still required."""
        return self._by_fixture.get(fixture, [])

    def by_club_home_date(
        self,
    ) -> Mapping[tuple[ClubT, date], list[cp_model.IntVar]]:
        """Per (club, date), the vars for that club's home matches on that date."""
        return self._by_club_home_date

    def by_team_date(self) -> Mapping[tuple[Team, date], list[cp_model.IntVar]]:
        """Per (team, date), the vars for every match -- home or away -- that team
        could play on that date."""
        return self._by_team_date

    def teams_playing(
        self, fixture: Fixture, match_date: date, teams: Collection[Team]
    ) -> cp_model.LinearExprT:
        """How many of `teams` play in `fixture` on `match_date`, in terms of that
        candidate's own decision variable: 0 if neither of the fixture's two teams
        is in `teams`; the variable itself if exactly one is (an ordinary match,
        from `teams`' perspective); twice the variable if both are (an internal
        match between two of `teams`, e.g. a same-club derby -- one candidate, but
        two of `teams` playing if it's scheduled). Summing this over every counted
        candidate in a window gives exactly how many teams'-worth of players from
        `teams` the window asks for -- once per match a team plays in, not just once
        per team, so the same pair meeting twice in the window counts twice (see
        MatchCountLimit's playing_teams_max/playing_teams_min)."""
        count = (fixture.home_team in teams) + (fixture.away_team in teams)
        if count == 0:
            return 0
        var = self._by_fixture_date[fixture, match_date]
        return var if count == 1 else 2 * var


def _counted_fixtures_by_date(
    fixture_date_vars: Mapping[tuple[Fixture, date], cp_model.IntVar],
    teams: Collection[Team],
    venue_scope: VenueScope,
) -> Mapping[date, list[Fixture]]:
    """Every candidate fixture in `fixture_date_vars`, grouped by date, that counts
    towards a MatchCountLimit over `teams` with the given `venue_scope` (see
    _fixture_counts_for_scope: home team among `teams` for HOME/ALL, away team for
    AWAY/ALL, but an internal match never counts under AWAY scope)."""
    result: MutableMapping[date, list[Fixture]] = collections.defaultdict(list)
    for fixture, match_date in fixture_date_vars:
        if _fixture_counts_for_scope(fixture, teams, venue_scope):
            result[match_date].append(fixture)
    return result


def _add_one_match_per_team_per_date_constraints(
    model: cp_model.CpModel, fixture_vars: _FixtureVars
) -> None:
    """No team -- home or away -- may play more than one match on the same date: a
    team's players can't be in two places at once. Unconditional, unlike the rest of
    _build_model's constraints: there's no spec field to turn it off, since no real
    fixture list should ever want to violate it."""
    for vars_for_team_date in fixture_vars.by_team_date().values():
        if len(vars_for_team_date) > 1:
            model.add(cp_model.LinearExpr.Sum(vars_for_team_date) <= 1)


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
    limits: Collection[MatchLimit],
    fixture_vars: _FixtureVars,
) -> None:
    """Apply every match-count limit (see MatchCountLimit.add_to_model): within
    each of a rule's windows -- rolling runs of `window_days` consecutive days for
    a RollingLimit, or the listed calendar ranges for a RangeLimit -- the counted
    set is bounded above by `match_max`/`playing_teams_max` and/or below by
    `match_min`/`playing_teams_min`, as configured.
    """
    for rule in limits:
        rule.add_to_model(model, fixture_vars)


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
                "unavailable away date for the away team's club, that no "
                "match_count_limits entry bars all play on that date, that the "
                "fixture isn't also in excluded_fixtures, that the date isn't after "
                "either club's latest_match_date, and -- if the two teams share a "
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

    required_fixtures = [
        fixture
        for division in params.divisions
        for fixture in division.required_fixtures()
        if fixture not in excluded
    ]

    for fixture in required_fixtures:
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
            # A match_count_limits entry with an effective cap of 0 over this date
            # (e.g. a spec-wide "nobody plays these dates" block) makes the
            # candidate a certain zero -- don't create a variable for it. Unlike
            # earliest_match_date this is not waived for fixed_fixtures: a fixture
            # pinned onto a barred date is a real contradiction, and
            # _add_fixed_fixtures_constraints reports it clearly.
            if any(
                limit.forbids(fixture, match_date)
                for limit in params.match_count_limits
            ):
                continue
            var = model.new_bool_var(
                f"{home_team.name}_vs_{away_team.name}_{match_date.isoformat()}"
            )
            fixture_vars.register(fixture, match_date, var)

    # Each required fixture must be scheduled exactly once. Drive this off the
    # required_fixtures list, not the vars actually registered: a fixture whose
    # every candidate date was filtered out above registers no vars, and iterating
    # only what registered would silently skip its constraint -- letting the solve
    # return a schedule quietly missing it. Zero candidates means the spec is
    # infeasible; collect every such fixture and say so.
    unschedulable: list[Fixture] = []
    for fixture in required_fixtures:
        scheduled_vars = fixture_vars.vars_for_fixture(fixture)
        if not scheduled_vars:
            unschedulable.append(fixture)
            continue
        model.add(cp_model.LinearExpr.Sum(scheduled_vars) == 1)

    if unschedulable:
        shown = ", ".join(
            f"{f.home_team.name} vs {f.away_team.name}" for f in unschedulable[:12]
        )
        if len(unschedulable) > 12:
            shown += f", ... (+{len(unschedulable) - 12} more)"
        raise ValueError(
            f"{len(unschedulable)} required fixture(s) have no schedulable date: "
            f"{shown}. For each, every home date of the home team's club is ruled "
            "out for that fixture -- by the away team's unavailable away dates, a "
            "latest_match_date / latest_internal_match_date / earliest_match_date "
            "cutoff, or a match_count_limits entry barring all play on those dates. "
            "Add a usable date, or exclude the fixture."
        )

    _add_one_match_per_team_per_date_constraints(model, fixture_vars)
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


def check_schedule(
    params: Parameters, fixtures: Collection[ScheduledFixture]
) -> list[str]:
    """Check `fixtures` against `params`, returning a list of reasons it is not a
    valid solved schedule -- empty iff it is one.

    The check is built straight on the solver's own model. _build_model()
    constructs exactly the CP-SAT model solve() would; then every candidate
    (fixture, date) variable is pinned -- to 1 if `fixtures` schedules it, to 0 if
    not -- and the solver is asked whether that assignment satisfies the model.
    Pinning the zeros as well means a missing or duplicated fixture surfaces as
    infeasibility through the model's own "each required fixture exactly once"
    constraint, so the only thing checked outside the model is whether each
    supplied fixture names a real candidate slot at all.

    The upside is that the check can't drift from solve()'s semantics -- it *is*
    solve()'s model. The price is that a genuine constraint violation comes back
    only as one generic "inconsistent with the spec's constraints", with no
    per-rule breakdown.
    """
    try:
        model, fixture_vars = _build_model(params)
    except ValueError as e:
        return [f"the spec has no feasible schedule at all: {e}"]

    candidate_vars = fixture_vars.fixture_date_vars()

    seen: set[tuple[Fixture, date]] = set()
    scheduled: set[tuple[Fixture, date]] = set()
    unknown: list[ScheduledFixture] = []
    for sf in fixtures:
        key = (sf.fixture, sf.date)
        if key in seen:
            continue  # a duplicate entry -- already accounted for
        seen.add(key)
        if key in candidate_vars:
            scheduled.add(key)
        else:
            unknown.append(sf)

    # A fixture whose (fixture, date) the model never created a variable for isn't
    # a slot this spec offers: the two teams aren't paired in a division, the date
    # isn't a home date for the home team, a date cutoff or an exclusion rules it
    # out, or (with match_count_limits) an effective cap of 0 bars it. Its other
    # candidates would be pinned to 0 below and make the model infeasible anyway,
    # but naming it is more use than a bare "inconsistent".
    if unknown:
        return [
            f"{sf.fixture.home_team.name} vs {sf.fixture.away_team.name} on "
            f"{sf.date.isoformat()} is not a slot this spec can schedule"
            for sf in unknown
        ]

    for key, var in candidate_vars.items():
        model.add(var == (1 if key in scheduled else 0))

    solver = cp_model.CpSolver()
    status = solver.Solve(model)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return []
    return [
        "the schedule is inconsistent with the spec's constraints "
        f"(solver status: {solver.StatusName(status)})"
    ]
