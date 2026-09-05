# Copyright 2026 Andrew Medworth
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

"""Load league fixture specifications (clubs, teams, divisions and constraints) from YAML files.

See spec-schema.json (rendered to HTML by build_schema_docs.py) for a description
of the expected YAML structure; README.md covers how to use it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import logging
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

import fmodel

logger = logging.getLogger(__name__)


class SpecError(ValueError):
    """Raised when a fixture specification file is invalid."""


class _NoDuplicateKeysSafeLoader(yaml.SafeLoader):
    """A SafeLoader that rejects duplicate keys in any mapping.

    PyYAML's default loaders silently let a later duplicate key clobber an earlier
    one (last one wins), which has already caused a real mix-up in this repo (a
    top-level section edited in place while a stale copy of the same key lingered
    elsewhere in the file). Failing loudly instead catches that class of mistake.
    """

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


@dataclasses.dataclass(frozen=True)
class Spec:
    """The result of loading a fixture specification: solver input plus reporting metadata."""

    parameters: fmodel.Parameters
    clubs: Mapping[str, fmodel.Club]
    name: str = ""
    is_final: bool = False
    description: str = ""


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise SpecError(f"{context} must be a non-empty mapping")
    return value


def _require_str(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise SpecError(f"{context} must be a string, got {value!r}")
    return value


def _require_int(value: Any, context: str) -> int:
    if not isinstance(value, int):
        raise SpecError(f"{context} must be an integer, got {value!r}")
    return value


def _require_bool(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise SpecError(f"{context} must be a boolean, got {value!r}")
    return value


def _require_int_or_none(value: Any, context: str) -> int | None:
    if value is None:
        return None
    return _require_int(value, context)


def _parse_date(value: Any, context: str) -> date:
    """Coerce a YAML scalar into a date.

    PyYAML parses unquoted yyyy-mm-dd scalars into datetime.date already, but
    quoted date strings come through as plain str, so accept both.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as e:
            raise SpecError(
                f"{context}: {value!r} is not a valid ISO8601 (yyyy-mm-dd) date"
            ) from e
    raise SpecError(f"{context}: expected a date, got {value!r}")


def _parse_date_list(value: Any, context: str) -> list[date]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SpecError(f"{context}: expected a list of dates, got {value!r}")
    dates = [_parse_date(v, context) for v in value]
    seen: set[date] = set()
    for d in dates:
        if d in seen:
            raise SpecError(f"{context}: duplicate date {d.isoformat()}")
        seen.add(d)
    return dates


def _parse_clubs(data: Mapping[str, Any], path: Path) -> dict[str, fmodel.Club]:
    clubs_spec = _require_mapping(data.get("clubs"), f"{path}: 'clubs'")

    clubs = {}
    for club_id, club_spec in clubs_spec.items():
        context = f"{path}: clubs[{club_id!r}]"
        if not isinstance(club_spec, dict):
            raise SpecError(f"{context} must be a mapping")
        required = {
            "name",
            "home_venue_name",
            "home_venue_address",
            "home_start_time",
            "home_time_limit",
        }
        missing = required - club_spec.keys()
        if missing:
            raise SpecError(f"{context} missing required field(s) {sorted(missing)}")
        clubs[club_id] = fmodel.Club(
            name=_require_str(club_spec["name"], f"{context}.name"),
            home_venue_name=_require_str(
                club_spec["home_venue_name"], f"{context}.home_venue_name"
            ),
            home_venue_address=_require_str(
                club_spec["home_venue_address"], f"{context}.home_venue_address"
            ),
            home_start_time=_require_str(
                club_spec["home_start_time"], f"{context}.home_start_time"
            ),
            home_time_limit=_require_str(
                club_spec["home_time_limit"], f"{context}.home_time_limit"
            ),
        )
    return clubs


@dataclasses.dataclass(frozen=True)
class _TeamShell:
    """A team's fields other than its division, which comes solely from 'divisions'."""

    club: str
    index: int
    name_override: str | None


def _parse_teams(
    data: Mapping[str, Any], clubs: Mapping[str, fmodel.Club], path: Path
) -> dict[str, _TeamShell]:
    teams_spec = _require_mapping(data.get("teams"), f"{path}: 'teams'")

    teams: dict[str, _TeamShell] = {}
    seen_club_index: dict[tuple[str, int], str] = {}
    for team_id, team_spec in teams_spec.items():
        context = f"{path}: teams[{team_id!r}]"
        if not isinstance(team_spec, dict):
            raise SpecError(f"{context} must be a mapping")
        required = {"club", "index"}
        missing = required - team_spec.keys()
        if missing:
            raise SpecError(f"{context} missing required field(s) {sorted(missing)}")

        club_id = team_spec["club"]
        if club_id not in clubs:
            raise SpecError(f"{context} references unknown club {club_id!r}")

        index = _require_int(team_spec["index"], f"{context}.index")

        name_override = team_spec.get("name_override")
        if name_override is not None:
            name_override = _require_str(name_override, f"{context}.name_override")

        key = (club_id, index)
        if key in seen_club_index:
            raise SpecError(
                f"{context} and {seen_club_index[key]!r} both refer to "
                f"club {club_id!r} index {index}"
            )
        seen_club_index[key] = team_id

        teams[team_id] = _TeamShell(
            club=club_id, index=index, name_override=name_override
        )
    return teams


@dataclasses.dataclass(frozen=True)
class _Divisions:
    """The parsed 'divisions' section: everything load_spec() needs to build teams
    and hand fmodel.Parameters its per-division schemes. fmodel derives the grouped
    fmodel.Division view itself, from teams + schemes."""

    team_divisions: dict[str, int]  # team ID -> division number
    ordered_team_ids: list[str]  # every team ID, in (division key, list position) order
    schemes: dict[int, fmodel.FixtureScheme]  # division number -> fixture scheme


_DIVISION_FIELD_KEYS = {"scheme", "teams"}


def _parse_divisions(
    data: Mapping[str, Any], teams: Mapping[str, _TeamShell], path: Path
) -> _Divisions:
    """Parse the 'divisions' section: each division's fixture scheme and the team IDs
    competing in it, keyed by division number. This is the only place a team's
    division is given - 'teams' entries don't repeat it.

    Every division entry is a mapping with a required 'scheme' (one of the
    fmodel.FixtureScheme values, e.g. 'double_round' or 'single_round') and a
    required non-empty 'teams' list. For a 'single_round' division the order of that
    list is the Berger table draw - the first team is table position 1, the next
    position 2, and so on - so it is significant, not merely cosmetic; ordered_team_ids
    keeps it, and load_spec() builds fmodel.Parameters.teams in that order.
    """
    divisions_spec = _require_mapping(data.get("divisions"), f"{path}: 'divisions'")

    team_divisions: dict[str, int] = {}
    ordered_team_ids: list[str] = []
    schemes: dict[int, fmodel.FixtureScheme] = {}
    for division_key, division_spec in divisions_spec.items():
        context = f"{path}: divisions[{division_key!r}]"
        division = _require_int(division_key, f"{context} key")

        if not isinstance(division_spec, dict):
            raise SpecError(f"{context} must be a mapping with 'scheme' and 'teams'")
        unsupported = division_spec.keys() - _DIVISION_FIELD_KEYS
        if unsupported:
            raise SpecError(
                f"{context}: unsupported field(s) {sorted(unsupported)} "
                f"(only {sorted(_DIVISION_FIELD_KEYS)} are)"
            )
        missing_fields = _DIVISION_FIELD_KEYS - division_spec.keys()
        if missing_fields:
            raise SpecError(
                f"{context} missing required field(s) {sorted(missing_fields)}"
            )

        scheme_str = _require_str(division_spec["scheme"], f"{context}.scheme")
        try:
            schemes[division] = fmodel.FixtureScheme(scheme_str)
        except ValueError:
            allowed = ", ".join(repr(s.value) for s in fmodel.FixtureScheme)
            raise SpecError(
                f"{context}.scheme must be one of {allowed}, got {scheme_str!r}"
            ) from None

        team_ids = division_spec["teams"]
        if not isinstance(team_ids, list) or not team_ids:
            raise SpecError(f"{context}.teams must be a non-empty list of team IDs")
        for team_id in team_ids:
            if team_id not in teams:
                raise SpecError(f"{context}.teams references unknown team {team_id!r}")
            if team_id in team_divisions:
                raise SpecError(
                    f"{path}: team {team_id!r} listed in more than one division"
                )
            team_divisions[team_id] = division
            ordered_team_ids.append(team_id)

    missing = teams.keys() - team_divisions.keys()
    if missing:
        raise SpecError(
            f"{path}: team(s) {sorted(missing)} not listed under 'divisions'"
        )
    return _Divisions(
        team_divisions=team_divisions,
        ordered_team_ids=ordered_team_ids,
        schemes=schemes,
    )


def _parse_home_dates_used_value(
    value: Any, context: str
) -> fmodel.HomeDatesUsedBounds:
    """A club's home_dates_used value: a mapping with 'min', 'max', or both (each a
    positive integer). Bounds how many of the club's home_dates end up hosting a
    match -- 'max' to pack matches onto fewer evenings, 'min' to spread them out.
    """
    if not isinstance(value, dict):
        raise SpecError(
            f"{context}: expected a mapping with 'min' and/or 'max', got {value!r}"
        )
    unsupported = value.keys() - _HOME_DATES_USED_FIELD_KEYS
    if unsupported:
        raise SpecError(
            f"{context}.{sorted(unsupported)} not supported (only "
            f"{sorted(_HOME_DATES_USED_FIELD_KEYS)} are)"
        )
    if not value:
        raise SpecError(f"{context}: needs at least one of 'min', 'max'")

    bounds: dict[str, int] = {}
    for key in ("min", "max"):
        if key in value:
            n = _require_int(value[key], f"{context}.{key}")
            if n < 1:
                raise SpecError(f"{context}.{key} must be at least 1, got {n}")
            bounds[key] = n
    try:
        return fmodel.HomeDatesUsedBounds(
            minimum=bounds.get("min"), maximum=bounds.get("max")
        )
    except ValueError as e:
        raise SpecError(f"{context}: {e}") from e


_CLUB_CONSTRAINT_FIELD_KEYS = {
    "home_dates",
    "home_dates_used",
    "latest_match_date",
    "match_count_limits",
}

_MATCH_COUNT_LIMIT_FIELD_KEYS = {
    "teams",
    "matches",
    "playing_teams",
    "time_window_days",
    "venue_scope",
    "apply_per",
    "date_ranges",
    "dates",
    "exclude_dates",
    "override_key",
}

_MATCH_COUNT_MEASURE_FIELD_KEYS = {"max", "max_overrides", "min", "min_overrides"}

_HOME_DATES_USED_FIELD_KEYS = {"min", "max"}

# match_count_limits is the one constraint type accepted under 'defaults' (a
# spec-wide list applied to every club). The rest (home_dates, home_dates_used,
# latest_match_date) are inherently per-club and have no meaningful spec-wide
# default.
_CLUB_CONSTRAINT_DEFAULTS_KEYS = {"match_count_limits"}

_TOP_LEVEL_KEYS = {
    "name",
    "is_final",
    "description",
    "latest_internal_match_date",
    "earliest_match_date",
    "clubs",
    "teams",
    "divisions",
    "club_constraints",
    "fixed_fixtures",
    "exclude_fixtures",
}


@dataclasses.dataclass(frozen=True, kw_only=True)
class _ParsedMatchCountLimit:
    """One match_count_limits entry, parsed but not yet resolved to concrete Teams.

    `team_ids` is None when the entry omitted 'teams' (meaning "every team of the
    club it applies to"). `override_key`, on a club entry, names the
    club_constraints.defaults entry this one replaces for that club; None means the
    entry is purely additive. Every defaults entry has an `override_key`.
    `match_max`/`match_min`/`playing_teams_max`/`playing_teams_min` (from the
    entry's 'matches'/'playing_teams' mappings) are None wherever that bound
    wasn't given -- all four may be None at once, for an entry that only cancels
    an inherited default (a club entry with an override_key and nothing else).
    `date_ranges`, when non-empty, restricts the rule to those explicit inclusive
    calendar ranges instead of every rolling `time_window_days` window.
    """

    team_ids: tuple[str, ...] | None
    time_window_days: int
    venue_scope: fmodel.VenueScope
    apply_per: fmodel.ApplyPer
    date_ranges: tuple[fmodel.DateRange, ...]
    override_key: str | None
    match_max: int | None = None
    match_max_overrides: Mapping[date, int | None] = dataclasses.field(
        default_factory=dict
    )
    match_min: int | None = None
    match_min_overrides: Mapping[date, int | None] = dataclasses.field(
        default_factory=dict
    )
    playing_teams_max: int | None = None
    playing_teams_max_overrides: Mapping[date, int | None] = dataclasses.field(
        default_factory=dict
    )
    playing_teams_min: int | None = None
    playing_teams_min_overrides: Mapping[date, int | None] = dataclasses.field(
        default_factory=dict
    )
    exclude_dates: frozenset[date] = frozenset()


@dataclasses.dataclass(frozen=True)
class _ClubConstraints:
    home_dates: dict[str, list[date]]
    club_latest_match_date: dict[str, date]
    home_dates_used: dict[str, fmodel.HomeDatesUsedBounds]
    match_count_limits: list[fmodel.MatchLimit]


def _parse_club_constraints(
    data: Mapping[str, Any],
    clubs: Mapping[str, fmodel.Club],
    teams: Mapping[str, fmodel.Team],
    path: Path,
) -> _ClubConstraints:
    """Parse the 'club_constraints' section: per-club home_dates, home_dates_used,
    latest_match_date and match_count_limits, keyed directly by club ID.

    A club's optional 'latest_match_date' entry is the last date on which any fixture
    involving one of that club's teams -- home or away -- may be scheduled (a
    fixed_fixtures entry involving the club after it is an error). It has no spec-wide
    default, so it isn't accepted under 'defaults'.

    An optional 'defaults' entry (a sibling of the club entries) supplies a spec-wide
    'match_count_limits' list applied to every club. Every defaults entry carries a
    unique 'override_key'; a club's own match_count_limits entry that repeats one of
    those keys replaces that default for the club, and any club entry without an
    'override_key' is purely additive. See _parse_match_count_limits() and
    _resolve_match_count_limits().

    Dates a club (or one of its teams) can't play away, and dates a team can't
    host, are expressed as match_count_limits entries -- a 'matches: {max: 0}'
    entry with 'venue_scope: away' (or 'home') and a 'dates' list, scoped to
    specific 'teams' where it's not club-wide.
    """
    section_name = "club_constraints"
    section_spec = data.get(section_name, {})
    if not isinstance(section_spec, dict):
        raise SpecError(f"{path}: {section_name!r} must be a mapping")

    defaults_spec = section_spec.get("defaults", {})
    if not isinstance(defaults_spec, dict):
        raise SpecError(f"{path}: {section_name}.defaults must be a mapping")
    unsupported_defaults = defaults_spec.keys() - _CLUB_CONSTRAINT_DEFAULTS_KEYS
    if unsupported_defaults:
        raise SpecError(
            f"{path}: {section_name}.defaults.{sorted(unsupported_defaults)} not "
            f"supported (only {sorted(_CLUB_CONSTRAINT_DEFAULTS_KEYS)} are)"
        )
    default_limits = _parse_match_count_limits(
        defaults_spec.get("match_count_limits"),
        None,
        teams,
        f"{path}: {section_name}.defaults.match_count_limits",
    )

    unknown_clubs = section_spec.keys() - clubs.keys() - {"defaults"}
    if unknown_clubs:
        raise SpecError(
            f"{path}: {section_name} references unknown club(s) {sorted(unknown_clubs)}"
        )

    home_dates: dict[str, list[date]] = {}
    club_latest_match_date: dict[str, date] = {}
    home_dates_used: dict[str, fmodel.HomeDatesUsedBounds] = {}
    club_limits: dict[str, list[_ParsedMatchCountLimit]] = {}

    for club_id in clubs:
        club_spec = section_spec.get(club_id, {})
        if not isinstance(club_spec, dict):
            raise SpecError(f"{path}: {section_name}[{club_id!r}] must be a mapping")
        unsupported = club_spec.keys() - _CLUB_CONSTRAINT_FIELD_KEYS
        if unsupported:
            raise SpecError(
                f"{path}: {section_name}[{club_id!r}].{sorted(unsupported)} not "
                f"supported (only {sorted(_CLUB_CONSTRAINT_FIELD_KEYS)} are)"
            )

        home_dates[club_id] = _parse_date_list(
            club_spec.get("home_dates"),
            f"{path}: {section_name}[{club_id!r}].home_dates",
        )

        if "latest_match_date" in club_spec:
            club_latest_match_date[club_id] = _parse_date(
                club_spec["latest_match_date"],
                f"{path}: {section_name}[{club_id!r}].latest_match_date",
            )

        if "home_dates_used" in club_spec:
            home_dates_used[club_id] = _parse_home_dates_used_value(
                club_spec["home_dates_used"],
                f"{path}: {section_name}[{club_id!r}].home_dates_used",
            )

        club_limits[club_id] = _parse_match_count_limits(
            club_spec.get("match_count_limits"),
            club_id,
            teams,
            f"{path}: {section_name}[{club_id!r}].match_count_limits",
        )

    match_count_limits = _resolve_match_count_limits(
        default_limits, club_limits, clubs, teams, path
    )

    return _ClubConstraints(
        home_dates=home_dates,
        club_latest_match_date=club_latest_match_date,
        home_dates_used=home_dates_used,
        match_count_limits=match_count_limits,
    )


def _club_team_ids(club_id: str, teams: Mapping[str, fmodel.Team]) -> list[str]:
    """The IDs of `club_id`'s teams, in `teams` iteration order."""
    return [tid for tid, team in teams.items() if team.club == club_id]


def _parse_match_count_overrides(
    value: Any, context: str, min_value: int = 1
) -> dict[date, int | None]:
    """Parse one of a 'matches'/'playing_teams' mapping's 'max_overrides' or
    'min_overrides': a date-keyed mapping of per-date limits (an integer >=
    min_value, or null to lift the limit that day)."""
    if not isinstance(value, dict):
        raise SpecError(f"{context} must be a mapping keyed by date")
    overrides: dict[date, int | None] = {}
    for date_key, count in value.items():
        override_date = _parse_date(date_key, context)
        count = _require_int_or_none(count, f"{context}[{date_key!r}]")
        if count is not None and count < min_value:
            raise SpecError(f"{context}[{date_key!r}] must be >= {min_value} or null")
        overrides[override_date] = count
    return overrides


def _parse_match_count_date_ranges(
    value: Any, context: str
) -> tuple[fmodel.DateRange, ...]:
    """Parse a match_count_limits entry's optional 'date_ranges': a non-empty list
    of {start_date, end_date} mappings, each an inclusive fmodel.DateRange (which
    enforces start on or before end). Either 'start_date' or 'end_date' may be
    omitted for a range that's open-ended on that side (no earliest, or no latest,
    date) -- but not both."""
    if not isinstance(value, list) or not value:
        raise SpecError(f"{context} must be a non-empty list")
    ranges: list[fmodel.DateRange] = []
    for i, item in enumerate(value):
        item_context = f"{context}[{i}]"
        if not isinstance(item, dict):
            raise SpecError(f"{item_context} must be a mapping")
        unsupported = item.keys() - {"start_date", "end_date"}
        if unsupported:
            raise SpecError(
                f"{item_context}.{sorted(unsupported)} not supported (only "
                "['end_date', 'start_date'] are)"
            )
        if "start_date" not in item and "end_date" not in item:
            raise SpecError(f"{item_context} needs 'start_date' and/or 'end_date'")
        start = (
            _parse_date(item["start_date"], f"{item_context}.start_date")
            if "start_date" in item
            else None
        )
        end = (
            _parse_date(item["end_date"], f"{item_context}.end_date")
            if "end_date" in item
            else None
        )
        try:
            ranges.append(fmodel.DateRange(start=start, end=end))
        except ValueError as e:
            raise SpecError(f"{item_context}: {e}") from e
    return tuple(ranges)


def _parse_match_count_dates(value: Any, context: str) -> tuple[fmodel.DateRange, ...]:
    """Parse a match_count_limits entry's optional 'dates': a non-empty list of
    individual dates, each turned into a single-day fmodel.DateRange. Sugar for a
    'date_ranges' list of one {start_date: d, end_date: d} per date -- the tidy
    way to pin a rule (typically 'matches: {max: 0}', a blackout) to a scattered
    set of dates rather than a contiguous span. Duplicate dates are rejected, like
    every other date list."""
    if not isinstance(value, list) or not value:
        raise SpecError(f"{context} must be a non-empty list")
    dates = _parse_date_list(value, context)
    return tuple(fmodel.DateRange(start=d, end=d) for d in dates)


def _has_measure_bound(
    max_: int | None,
    max_overrides: Mapping[date, int | None],
    min_: int | None,
    min_overrides: Mapping[date, int | None],
) -> bool:
    """Whether a parsed 'matches'/'playing_teams' measure carries an actual bound
    -- as opposed to a 'matches: {max: null}'-shaped entry that names the key but
    supplies nothing (meaningless on its own, e.g. as a bare cancel-a-default
    marker without 'override_key', or as a pointless 'date_ranges' entry)."""
    return (
        max_ is not None
        or bool(max_overrides)
        or min_ is not None
        or bool(min_overrides)
    )


def _parse_match_count_measure(
    value: Any,
    context: str,
    *,
    time_window_days: int,
    has_date_ranges: bool,
    max_zero_allowed: bool,
) -> tuple[int | None, dict[date, int | None], int | None, dict[date, int | None]]:
    """Parse a match_count_limits entry's 'matches' or 'playing_teams' mapping:
    'max'/'max_overrides' cap the measure from above, 'min'/'min_overrides' floor
    it from below. Returns (max_, max_overrides, min_, min_overrides).

    'max_overrides'/'min_overrides' are only allowed when 'time_window_days' is 1
    (each window is then a single date) and are never combinable with
    'date_ranges' or its 'dates' sugar (whose windows are already explicit).
    'max_overrides' and 'min_overrides' values may always be >= 0 and >= 1
    respectively -- an override ties to one specific date, and "no counted
    match/team at all that day" (0) is meaningful for either measure, the
    single-day equivalent of a RangeLimit 'max: 0' blackout; a floor override,
    unlike a ceiling one, can never usefully be 0 (it can never bind), so
    'min_overrides' stays >= 1. Only the un-overridden 'max' base keeps the
    matches/playing_teams asymmetry (see 'max_zero_allowed'): a plain matches cap
    of 0 only makes sense together with 'date_ranges'/'dates', a full blackout; a
    playing_teams cap of 0 -- "nobody from this set plays" -- is meaningful either
    way.
    """
    if not isinstance(value, dict) or not value:
        raise SpecError(f"{context} must be a non-empty mapping")
    unsupported = value.keys() - _MATCH_COUNT_MEASURE_FIELD_KEYS
    if unsupported:
        raise SpecError(
            f"{context}.{sorted(unsupported)} not supported (only "
            f"{sorted(_MATCH_COUNT_MEASURE_FIELD_KEYS)} are)"
        )

    max_ = None
    if "max" in value:
        max_ = _require_int_or_none(value["max"], f"{context}.max")
        min_max = 0 if max_zero_allowed else 1
        if max_ is not None and max_ < min_max:
            raise SpecError(f"{context}.max must be >= {min_max} or null")

    min_ = None
    if "min" in value:
        min_ = _require_int_or_none(value["min"], f"{context}.min")
        if min_ is not None and min_ < 1:
            raise SpecError(f"{context}.min must be >= 1 or null")

    max_overrides: dict[date, int | None] = {}
    if "max_overrides" in value:
        if time_window_days != 1:
            raise SpecError(
                f"{context}.max_overrides is only allowed when time_window_days is 1"
            )
        if has_date_ranges:
            raise SpecError(
                f"{context}.max_overrides and 'date_ranges'/'dates' can't be combined"
            )
        max_overrides = _parse_match_count_overrides(
            value["max_overrides"], f"{context}.max_overrides", min_value=0
        )

    min_overrides: dict[date, int | None] = {}
    if "min_overrides" in value:
        if time_window_days != 1:
            raise SpecError(
                f"{context}.min_overrides is only allowed when time_window_days is 1"
            )
        if has_date_ranges:
            raise SpecError(
                f"{context}.min_overrides and 'date_ranges'/'dates' can't be combined"
            )
        min_overrides = _parse_match_count_overrides(
            value["min_overrides"], f"{context}.min_overrides", min_value=1
        )

    return max_, max_overrides, min_, min_overrides


def _parse_match_count_limits(
    entries_spec: Any,
    club_id: str | None,
    teams: Mapping[str, fmodel.Team],
    context: str,
) -> list[_ParsedMatchCountLimit]:
    """Parse a 'match_count_limits' list -- a club's own entry, or (when club_id is
    None) the club_constraints.defaults list applied to every club.

    Each entry needs a 'matches' and/or 'playing_teams' mapping carrying an actual
    bound -- either alone is fine (a rule can bound just matches, just distinct
    playing teams, or both) -- unless (for a club entry) an 'override_key'
    cancelling a default supplies the point of the entry instead.
      - 'matches' (optional): bounds on how many matches (of the kind selected by
        'venue_scope') fall within a window, via up to four sub-keys:
          - 'max': an integer >= 1 (or >= 0 when 'date_ranges' is set), or null
            (meaningless on its own).
          - 'max_overrides': per-date replacements of 'max' (an integer >= 0, or
            null to lift the cap that day -- 0 bars every counted match that one
            date, the single-day equivalent of a 'date_ranges' blackout). Only
            allowed when 'time_window_days' is 1; not combinable with
            'date_ranges'.
          - 'min': the fewest matches required within a window -- an integer >= 1,
            or null. A floor of 0 is never meaningful (it can never bind), so
            there's no 'date_ranges'-shaped exception the way 'max' has one.
          - 'min_overrides': per-date replacements of 'min' (an integer >= 1, or
            null to lift the floor that day), mirroring 'max_overrides'.
      - 'playing_teams' (optional): the same four sub-keys ('max', 'max_overrides',
        'min', 'min_overrides'), but bounding how many teams'-worth of players
        from 'teams' a window asks for, as opposed to 'matches' (a count of
        matches). Each counted match adds one of 'teams' playing, or two if it's
        an internal match between two of 'teams' (e.g. a same-club derby counted
        by that club's own venue-capacity rule): one match towards 'matches.max',
        but two teams from the set on to play. (Under 'away' venue_scope an
        internal match is not counted at all, so it adds nothing here either.) A
        venue that can physically host 3 simultaneous matches but only has 3
        teams' worth of players to field wants both 'matches: {max: 3}' and
        'playing_teams: {max: 3}' -- otherwise 3 matches, one an internal derby,
        would need 4 teams' worth of players. Given 'playing_teams' alone (no
        'matches'), the number of matches is left uncapped directly, though it's
        usually bounded in practice by the same team limit. Over a wider window
        (a multi-day 'time_window_days', or 'date_ranges'), the same pair meeting
        twice counts twice here -- a running tally of teams asked to play, not a
        count of distinct teams touched. Unlike 'matches.max', 'playing_teams.max'
        may be 0 regardless of 'date_ranges' -- "nobody from this set plays" is
        meaningful either way (both measures' 'max_overrides' already allow 0,
        since an override ties to one specific date regardless).
        When both a 'max' and a 'min' are given for the same measure, 'min' may
        not exceed 'max'.
      - 'teams' (optional): IDs of this club's teams whose matches are counted.
        Omitted => every team of the club. Not allowed under 'defaults', nor
        alongside 'override_key' (an override replaces a spec-wide, all-teams
        default).
      - 'apply_per' (optional, default 'across_teams'): 'across_teams' => the listed
        teams share one budget per window; 'each_team' => enforced per team.
      - 'time_window_days' (optional, default 1): window length in consecutive
        calendar days. 7 limits matches in any 7-consecutive-day period -- two
        matches exactly a week apart fall in separate windows.
      - 'venue_scope' (optional, default 'all'): 'home', 'away' or 'all'. An
        internal match (both teams the same club) is never counted under 'away'
        scope -- its 'away' team plays at its own club's venue -- but still
        counts under 'home' and 'all'.
      - 'date_ranges' (optional): a non-empty list of {start_date, end_date}
        inclusive ranges. Either 'start_date' or 'end_date' may be omitted for a
        range that's open-ended on that side (no earliest, or no latest, date),
        but not both. When given, the rule applies to exactly those ranges
        instead of every rolling 'time_window_days' window. Allowed on both club
        and 'defaults' entries; not combinable with 'time_window_days' or any
        '*_overrides', nor (on a club entry) with 'override_key'. 'matches.max'
        must then be a non-negative integer (0 bars every counted match in the
        range -- a whole-club, all-teams 'defaults' entry with 'matches: {max: 0}'
        is how a spec-wide "nobody plays these dates" block is expressed).
      - 'dates' (optional): a non-empty list of individual dates, sugar for a
        'date_ranges' list of one single-day range per date -- the tidy way to
        pin a scattered set of dates rather than a contiguous span (e.g. the
        Saturdays a club's teams can't travel: 'venue_scope: away',
        'matches: {max: 0}', 'dates: [...]'). Same rules and effect as
        'date_ranges'; not combinable with it.
      - 'exclude_dates' (optional): a non-empty list of dates whose counted
        matches this rule ignores entirely -- every window is evaluated as if
        nothing counted falls on them. Unlike the '*_overrides' maps it is not tied
        to a single-date window, so it can exempt one date from a multi-day rolling
        cap. Not combinable with 'date_ranges'/'dates'.
      - 'override_key': required for a 'defaults' entry (and unique within that
        list); optional for a club entry, where it names the default this one
        replaces for the club wholesale.
    """
    if entries_spec is None:
        return []
    if not isinstance(entries_spec, list):
        raise SpecError(f"{context} must be a list")

    is_defaults = club_id is None
    club_team_ids = [] if is_defaults else _club_team_ids(club_id, teams)  # type: ignore[arg-type]

    limits: list[_ParsedMatchCountLimit] = []
    seen_default_keys: set[str] = set()
    for i, entry in enumerate(entries_spec):
        entry_context = f"{context}[{i}]"
        if not isinstance(entry, dict):
            raise SpecError(f"{entry_context} must be a mapping")
        unsupported = entry.keys() - _MATCH_COUNT_LIMIT_FIELD_KEYS
        if unsupported:
            raise SpecError(
                f"{entry_context}.{sorted(unsupported)} not supported (only "
                f"{sorted(_MATCH_COUNT_LIMIT_FIELD_KEYS)} are)"
            )

        # 'date_ranges' and its sugar 'dates' permit matches.max: 0 (a full
        # blackout of the listed periods); every other form needs matches.max
        # >= 1 or null. Both set has_date_ranges: from here on they behave
        # identically (a 'dates' list is just single-day ranges).
        has_dates = "dates" in entry
        has_date_ranges = "date_ranges" in entry or has_dates
        # The key actually in use, for messages (they read the same either way).
        dr_key = "dates" if has_dates else "date_ranges"
        if "date_ranges" in entry and has_dates:
            raise SpecError(
                f"{entry_context}: 'date_ranges' and 'dates' can't be combined "
                "('dates' is sugar for a list of single-day 'date_ranges')"
            )

        override_key: str | None = None
        if "override_key" in entry:
            override_key = _require_str(
                entry["override_key"], f"{entry_context}.override_key"
            )
        if is_defaults:
            if override_key is None:
                raise SpecError(
                    f"{entry_context} missing required field 'override_key' "
                    "(every club_constraints.defaults.match_count_limits entry "
                    "needs one, so a club can address it)"
                )
            if override_key in seen_default_keys:
                raise SpecError(
                    f"{entry_context}.override_key {override_key!r} is used by an "
                    "earlier defaults entry"
                )
            seen_default_keys.add(override_key)

        team_ids: tuple[str, ...] | None = None
        if "teams" in entry:
            if is_defaults:
                raise SpecError(
                    f"{entry_context}.teams is not allowed under "
                    "club_constraints.defaults (defaults apply to every club); "
                    "give an explicit 'teams' list in the club's own "
                    "match_count_limits instead"
                )
            if override_key is not None:
                raise SpecError(
                    f"{entry_context}: 'teams' and 'override_key' can't be combined "
                    "(an override replaces a spec-wide, all-teams default)"
                )
            raw_ids = entry["teams"]
            if not isinstance(raw_ids, list) or not raw_ids:
                raise SpecError(f"{entry_context}.teams must be a non-empty list")
            seen_team_ids: set[str] = set()
            for team_id in raw_ids:
                if team_id in seen_team_ids:
                    raise SpecError(
                        f"{entry_context}.teams: duplicate team {team_id!r}"
                    )
                seen_team_ids.add(team_id)
                if team_id not in teams:
                    raise SpecError(
                        f"{entry_context}.teams references unknown team {team_id!r}"
                    )
                if teams[team_id].club != club_id:
                    raise SpecError(
                        f"{entry_context}.teams: team {team_id!r} belongs to club "
                        f"{teams[team_id].club!r}, not {club_id!r}"
                    )
            team_ids = tuple(raw_ids)
        elif not is_defaults and not club_team_ids:
            raise SpecError(
                f"{entry_context}: club {club_id!r} has no teams (specify "
                "'teams' explicitly)"
            )

        apply_per = fmodel.ApplyPer.ACROSS_TEAMS
        if "apply_per" in entry:
            try:
                apply_per = fmodel.ApplyPer(entry["apply_per"])
            except ValueError:
                allowed = ", ".join(repr(a.value) for a in fmodel.ApplyPer)
                raise SpecError(
                    f"{entry_context}.apply_per must be one of {allowed}, got "
                    f"{entry['apply_per']!r}"
                ) from None

        time_window_days = 1
        if "time_window_days" in entry:
            time_window_days = _require_int(
                entry["time_window_days"], f"{entry_context}.time_window_days"
            )
            if time_window_days < 1:
                raise SpecError(f"{entry_context}.time_window_days must be >= 1")

        venue_scope = fmodel.VenueScope.ALL
        if "venue_scope" in entry:
            try:
                venue_scope = fmodel.VenueScope(entry["venue_scope"])
            except ValueError:
                allowed = ", ".join(repr(s.value) for s in fmodel.VenueScope)
                raise SpecError(
                    f"{entry_context}.venue_scope must be one of {allowed}, got "
                    f"{entry['venue_scope']!r}"
                ) from None

        match_max: int | None = None
        match_max_overrides: dict[date, int | None] = {}
        match_min: int | None = None
        match_min_overrides: dict[date, int | None] = {}
        if "matches" in entry:
            match_max, match_max_overrides, match_min, match_min_overrides = (
                _parse_match_count_measure(
                    entry["matches"],
                    f"{entry_context}.matches",
                    time_window_days=time_window_days,
                    has_date_ranges=has_date_ranges,
                    max_zero_allowed=has_date_ranges,
                )
            )
            if (
                match_min is not None
                and match_max is not None
                and match_min > match_max
            ):
                raise SpecError(
                    f"{entry_context}.matches.min {match_min} exceeds "
                    f"{entry_context}.matches.max {match_max}"
                )

        playing_teams_max: int | None = None
        playing_teams_max_overrides: dict[date, int | None] = {}
        playing_teams_min: int | None = None
        playing_teams_min_overrides: dict[date, int | None] = {}
        if "playing_teams" in entry:
            (
                playing_teams_max,
                playing_teams_max_overrides,
                playing_teams_min,
                playing_teams_min_overrides,
            ) = _parse_match_count_measure(
                entry["playing_teams"],
                f"{entry_context}.playing_teams",
                time_window_days=time_window_days,
                has_date_ranges=has_date_ranges,
                max_zero_allowed=True,
            )
            if (
                playing_teams_min is not None
                and playing_teams_max is not None
                and playing_teams_min > playing_teams_max
            ):
                raise SpecError(
                    f"{entry_context}.playing_teams.min {playing_teams_min} "
                    f"exceeds {entry_context}.playing_teams.max {playing_teams_max}"
                )

        date_ranges: tuple[fmodel.DateRange, ...] = ()
        if has_date_ranges:
            if not is_defaults and override_key is not None:
                raise SpecError(
                    f"{entry_context}: a club entry can't combine {dr_key!r} "
                    "with 'override_key' (an override_key names a rolling default "
                    "to replace wholesale; on a defaults entry it is the entry's "
                    f"own key and {dr_key!r} is fine)"
                )
            if "time_window_days" in entry:
                raise SpecError(
                    f"{entry_context}: {dr_key!r} and 'time_window_days' can't "
                    f"be combined ({dr_key!r} replaces the rolling window)"
                )
            if not _has_measure_bound(
                match_max, match_max_overrides, match_min, match_min_overrides
            ) and not _has_measure_bound(
                playing_teams_max,
                playing_teams_max_overrides,
                playing_teams_min,
                playing_teams_min_overrides,
            ):
                raise SpecError(
                    f"{entry_context}.{dr_key} needs a 'matches' and/or "
                    "'playing_teams' cap"
                )
            if has_dates:
                date_ranges = _parse_match_count_dates(
                    entry["dates"], f"{entry_context}.dates"
                )
            else:
                date_ranges = _parse_match_count_date_ranges(
                    entry["date_ranges"], f"{entry_context}.date_ranges"
                )

        exclude_dates: frozenset[date] = frozenset()
        if "exclude_dates" in entry:
            if has_date_ranges:
                raise SpecError(
                    f"{entry_context}: 'exclude_dates' and {dr_key!r} can't be combined"
                )
            exclude_dates = frozenset(
                _parse_date_list(
                    entry["exclude_dates"], f"{entry_context}.exclude_dates"
                )
            )

        if (
            not _has_measure_bound(
                match_max, match_max_overrides, match_min, match_min_overrides
            )
            and not _has_measure_bound(
                playing_teams_max,
                playing_teams_max_overrides,
                playing_teams_min,
                playing_teams_min_overrides,
            )
            and override_key is None
        ):
            raise SpecError(
                f"{entry_context} needs a 'matches' and/or 'playing_teams' mapping "
                "to carry an actual bound (or an 'override_key' to cancel a "
                "default)"
            )

        limits.append(
            _ParsedMatchCountLimit(
                team_ids=team_ids,
                match_max=match_max,
                match_max_overrides=match_max_overrides,
                match_min=match_min,
                match_min_overrides=match_min_overrides,
                playing_teams_max=playing_teams_max,
                playing_teams_max_overrides=playing_teams_max_overrides,
                playing_teams_min=playing_teams_min,
                playing_teams_min_overrides=playing_teams_min_overrides,
                time_window_days=time_window_days,
                venue_scope=venue_scope,
                apply_per=apply_per,
                date_ranges=date_ranges,
                override_key=override_key,
                exclude_dates=exclude_dates,
            )
        )

    return limits


def _resolved_cap(
    base: int | None, overrides: Mapping[date, int | None]
) -> fmodel.Cap | None:
    """A fmodel.Cap for a parsed (base, overrides) pair -- one of 'matches' or
    'playing_teams' 'max'/'max_overrides' or 'min'/'min_overrides' -- or None
    when neither carries an actual bound, so that bound is left unconstrained on
    the resolved MatchLimit."""
    if base is None and not overrides:
        return None
    return fmodel.Cap(base=base, overrides=dict(overrides))


def _resolve_match_count_limits(
    default_limits: list[_ParsedMatchCountLimit],
    club_limits: Mapping[str, list[_ParsedMatchCountLimit]],
    clubs: Mapping[str, fmodel.Club],
    teams: Mapping[str, fmodel.Team],
    path: Path,
) -> list[fmodel.MatchLimit]:
    """Combine the spec-wide default match_count_limits with each club's own, and
    resolve every entry to a concrete fmodel.MatchLimit (a RangeLimit when the
    entry carries 'date_ranges', otherwise a RollingLimit).

    A club entry whose 'override_key' names a default entry replaces that default
    for the club (an unknown key, or two club entries sharing one, is an error);
    every other club entry is additive. Entries that carry none of 'matches'/
    'playing_teams' max or min bound (a pure cancel-the-default marker) and
    entries that resolve to no teams are dropped.
    """
    default_keys = {pl.override_key for pl in default_limits}
    resolved: list[fmodel.MatchLimit] = []
    for club_id in clubs:
        own = list(club_limits.get(club_id, []))
        overridden: dict[str, _ParsedMatchCountLimit] = {}
        for pl in own:
            if pl.override_key is None:
                continue
            if pl.override_key not in default_keys:
                raise SpecError(
                    f"{path}: club_constraints[{club_id!r}].match_count_limits "
                    f"override_key {pl.override_key!r} does not match any "
                    "club_constraints.defaults.match_count_limits entry"
                )
            if pl.override_key in overridden:
                raise SpecError(
                    f"{path}: club_constraints[{club_id!r}].match_count_limits "
                    f"has two entries with override_key {pl.override_key!r}"
                )
            overridden[pl.override_key] = pl

        effective = [
            pl for pl in default_limits if pl.override_key not in overridden
        ] + own

        club_team_objs = [teams[tid] for tid in _club_team_ids(club_id, teams)]
        for pl in effective:
            match_max = _resolved_cap(pl.match_max, pl.match_max_overrides)
            match_min = _resolved_cap(pl.match_min, pl.match_min_overrides)
            playing_teams_max = _resolved_cap(
                pl.playing_teams_max, pl.playing_teams_max_overrides
            )
            playing_teams_min = _resolved_cap(
                pl.playing_teams_min, pl.playing_teams_min_overrides
            )
            if (
                match_max is None
                and match_min is None
                and playing_teams_max is None
                and playing_teams_min is None
            ):
                continue
            team_objs = (
                club_team_objs
                if pl.team_ids is None
                else [teams[tid] for tid in pl.team_ids]
            )
            if not team_objs:
                continue
            if pl.date_ranges:
                resolved.append(
                    fmodel.RangeLimit(
                        teams=team_objs,
                        match_max=match_max,
                        match_min=match_min,
                        playing_teams_max=playing_teams_max,
                        playing_teams_min=playing_teams_min,
                        venue_scope=pl.venue_scope,
                        apply_per=pl.apply_per,
                        ranges=pl.date_ranges,
                    )
                )
            else:
                resolved.append(
                    fmodel.RollingLimit(
                        teams=team_objs,
                        match_max=match_max,
                        match_min=match_min,
                        playing_teams_max=playing_teams_max,
                        playing_teams_min=playing_teams_min,
                        venue_scope=pl.venue_scope,
                        apply_per=pl.apply_per,
                        window_days=pl.time_window_days,
                        exclude_dates=pl.exclude_dates,
                    )
                )
    return resolved


def _parse_fixed_fixtures(
    data: Mapping[str, Any],
    teams: Mapping[str, fmodel.Team],
    home_dates: Mapping[str, list[date]],
    path: Path,
) -> list[fmodel.ScheduledFixture]:
    """Parse the optional 'fixed_fixtures' section: fixtures pinned to a specific date.

    Each entry references a home and away team (by team ID) and a date. The two
    teams must be in the same division, and the date must be one of the home
    team's club's home_dates. A pin that a match_count_limits blackout (the home
    team barred from hosting, or the away team from travelling, that day) would
    forbid is caught later, when the model is built.
    """
    section_name = "fixed_fixtures"
    section_spec = data.get(section_name)
    if section_spec is None:
        return []
    if not isinstance(section_spec, list):
        raise SpecError(f"{path}: {section_name!r} must be a list")

    required = {"home", "away", "date"}
    fixed_fixtures = []
    for i, entry in enumerate(section_spec):
        context = f"{path}: {section_name}[{i}]"
        if not isinstance(entry, dict):
            raise SpecError(f"{context} must be a mapping")
        missing = required - entry.keys()
        if missing:
            raise SpecError(f"{context} missing required field(s) {sorted(missing)}")
        unsupported = entry.keys() - required
        if unsupported:
            raise SpecError(f"{context}: unsupported field(s) {sorted(unsupported)}")

        home_id = entry["home"]
        away_id = entry["away"]
        if home_id not in teams:
            raise SpecError(f"{context}: references unknown team {home_id!r}")
        if away_id not in teams:
            raise SpecError(f"{context}: references unknown team {away_id!r}")
        if home_id == away_id:
            raise SpecError(f"{context}: home and away team are both {home_id!r}")

        home_team = teams[home_id]
        away_team = teams[away_id]
        if home_team.division != away_team.division:
            raise SpecError(
                f"{context}: {home_id!r} (division {home_team.division}) and "
                f"{away_id!r} (division {away_team.division}) are not in the same "
                "division"
            )

        fixture_date = _parse_date(entry["date"], f"{context}.date")
        if fixture_date not in home_dates.get(home_team.club, []):
            raise SpecError(
                f"{context}: {fixture_date.isoformat()} is not one of "
                f"{home_team.club!r}'s home dates"
            )

        fixed_fixtures.append(
            fmodel.ScheduledFixture(
                fixture=fmodel.Fixture(home_team=home_team, away_team=away_team),
                date=fixture_date,
            )
        )
    return fixed_fixtures


_EXCLUDE_FIXTURES_SECTION_KEYS = {"clubs", "teams", "fixtures"}


def _parse_exclude_fixtures(
    data: Mapping[str, Any], teams: Mapping[str, fmodel.Team], path: Path
) -> list[fmodel.Fixture]:
    """Parse the optional 'exclude_fixtures' section: fixtures withheld from
    scheduling entirely, to be arranged in a later run.

    'clubs' and 'teams' exclude every fixture (in both directions) that any of the
    given clubs' or teams' teams would otherwise play within their division;
    'fixtures' excludes individual home/away pairs. Returns the fully expanded set
    of excluded (home, away) Fixture pairs. Both directions are expanded even for a
    single_round division (where only one is ever scheduled), so the exclusion
    holds whichever way the Berger draw sends that pairing.
    """
    section_name = "exclude_fixtures"
    section_spec = data.get(section_name)
    if section_spec is None:
        return []
    if not isinstance(section_spec, dict):
        raise SpecError(f"{path}: {section_name!r} must be a mapping")

    unsupported = section_spec.keys() - _EXCLUDE_FIXTURES_SECTION_KEYS
    if unsupported:
        raise SpecError(
            f"{path}: {section_name}.{sorted(unsupported)} not supported "
            "(only 'clubs', 'teams' and 'fixtures' are)"
        )

    teams_by_division: dict[int, list[fmodel.Team]] = {}
    for team in teams.values():
        teams_by_division.setdefault(team.division, []).append(team)

    excluded: set[fmodel.Fixture] = set()

    clubs_spec = section_spec.get("clubs", [])
    if not isinstance(clubs_spec, list):
        raise SpecError(f"{path}: {section_name}.clubs must be a list")
    known_club_ids = {t.club for t in teams.values()}
    for club_id in clubs_spec:
        if club_id not in known_club_ids:
            raise SpecError(
                f"{path}: {section_name}.clubs references unknown club {club_id!r}"
            )
        for division_teams in teams_by_division.values():
            for home_team, away_team in itertools.permutations(division_teams, 2):
                if home_team.club == club_id or away_team.club == club_id:
                    excluded.add(
                        fmodel.Fixture(home_team=home_team, away_team=away_team)
                    )

    teams_spec = section_spec.get("teams", [])
    if not isinstance(teams_spec, list):
        raise SpecError(f"{path}: {section_name}.teams must be a list")
    for team_id in teams_spec:
        if team_id not in teams:
            raise SpecError(
                f"{path}: {section_name}.teams references unknown team {team_id!r}"
            )
        team = teams[team_id]
        for other in teams_by_division[team.division]:
            if other == team:
                continue
            excluded.add(fmodel.Fixture(home_team=team, away_team=other))
            excluded.add(fmodel.Fixture(home_team=other, away_team=team))

    fixtures_spec = section_spec.get("fixtures", [])
    if not isinstance(fixtures_spec, list):
        raise SpecError(f"{path}: {section_name}.fixtures must be a list")
    required = {"home", "away"}
    for i, entry in enumerate(fixtures_spec):
        context = f"{path}: {section_name}.fixtures[{i}]"
        if not isinstance(entry, dict):
            raise SpecError(f"{context} must be a mapping")
        missing = required - entry.keys()
        if missing:
            raise SpecError(f"{context} missing required field(s) {sorted(missing)}")
        unsupported_fields = entry.keys() - required
        if unsupported_fields:
            raise SpecError(
                f"{context}: unsupported field(s) {sorted(unsupported_fields)}"
            )

        home_id = entry["home"]
        away_id = entry["away"]
        if home_id not in teams:
            raise SpecError(f"{context}: references unknown team {home_id!r}")
        if away_id not in teams:
            raise SpecError(f"{context}: references unknown team {away_id!r}")
        if home_id == away_id:
            raise SpecError(f"{context}: home and away team are both {home_id!r}")

        home_team = teams[home_id]
        away_team = teams[away_id]
        if home_team.division != away_team.division:
            raise SpecError(
                f"{context}: {home_id!r} (division {home_team.division}) and "
                f"{away_id!r} (division {away_team.division}) are not in the same "
                "division"
            )

        excluded.add(fmodel.Fixture(home_team=home_team, away_team=away_team))

    return list(excluded)


def _load_yaml_data(path: Path) -> dict[str, Any]:
    """Read and parse a spec file's raw YAML content (shared by load_spec() and the
    lighter-weight load_team_ids())."""
    with path.open() as f:
        try:
            # _NoDuplicateKeysSafeLoader subclasses SafeLoader, so this is as safe as
            # yaml.safe_load(); ruff's S506 can't see that from the loader's name alone.
            data = yaml.load(f, Loader=_NoDuplicateKeysSafeLoader)  # noqa: S506
        except yaml.constructor.ConstructorError as e:
            raise SpecError(f"{path}: {e}") from e

    if not isinstance(data, dict):
        raise SpecError(f"{path}: top-level YAML content must be a mapping")
    return data


_CHECKSUM_ALGORITHM = "sha256"


def spec_checksum(spec_path: str | Path) -> str:
    """Return a self-describing checksum ("sha256:<hex>") of the raw bytes of the
    spec file at spec_path.

    load_spec() records this on the Parameters it returns, so it travels through
    fmodel.solve() onto the SolveResult and into solution.yaml: a solution then
    carries a tamper-evident fingerprint of the exact spec it was solved from,
    and report.py recomputes it from the spec it's handed and warns on a
    mismatch. Being a hash of the file's bytes, it changes if the spec is merely
    reformatted -- so a mismatch means "re-check this pairing", not necessarily
    "the schedule is wrong".
    """
    digest = hashlib.new(_CHECKSUM_ALGORITHM, Path(spec_path).read_bytes()).hexdigest()
    return f"{_CHECKSUM_ALGORITHM}:{digest}"


def load_team_ids(spec_path: str | Path) -> dict[str, tuple[str, int]]:
    """Map each team ID in a spec to its (club, index) pair.

    Only needs 'clubs' and 'teams' to be valid -- unlike load_spec(), it doesn't
    require 'divisions', 'club_constraints', etc. fmodel.Team carries a (club,
    index) pair but not the spec's own team ID, so this is for code (namely
    fixturesolution.py) that needs to translate between the two -- e.g. to store
    a solved fixture list by team ID rather than by (club, index).
    """
    path = Path(spec_path)
    data = _load_yaml_data(path)
    clubs = _parse_clubs(data, path)
    team_shells = _parse_teams(data, clubs, path)
    return {
        team_id: (shell.club, shell.index) for team_id, shell in team_shells.items()
    }


def load_spec(spec_path: str | Path) -> Spec:
    """Load a fixture Spec (solver Parameters plus club reporting metadata) from a YAML file."""
    path = Path(spec_path)
    data = _load_yaml_data(path)

    unsupported = data.keys() - _TOP_LEVEL_KEYS
    if unsupported:
        raise SpecError(
            f"{path}: top-level key(s) {sorted(unsupported)} not supported "
            f"(only {sorted(_TOP_LEVEL_KEYS)} are)"
        )

    clubs = _parse_clubs(data, path)
    team_shells = _parse_teams(data, clubs, path)
    divisions = _parse_divisions(data, team_shells, path)
    # Build teams in 'divisions' order: a single_round division's team order is its
    # Berger draw, and fmodel derives its grouped Division view (that order
    # included) from Parameters.teams.
    teams = {
        team_id: fmodel.Team(
            division=divisions.team_divisions[team_id],
            club=team_shells[team_id].club,
            index=team_shells[team_id].index,
            name_override=team_shells[team_id].name_override,
        )
        for team_id in divisions.ordered_team_ids
    }

    club_constraints = _parse_club_constraints(data, clubs, teams, path)
    home_dates = club_constraints.home_dates

    kwargs: dict[str, Any] = {}
    if club_constraints.club_latest_match_date:
        kwargs["club_latest_match_date"] = club_constraints.club_latest_match_date
    if club_constraints.home_dates_used:
        kwargs["home_dates_used"] = club_constraints.home_dates_used
    if club_constraints.match_count_limits:
        kwargs["match_count_limits"] = club_constraints.match_count_limits

    fixed_fixtures = _parse_fixed_fixtures(data, teams, home_dates, path)
    if fixed_fixtures:
        kwargs["fixed_fixtures"] = fixed_fixtures

    excluded_fixtures = _parse_exclude_fixtures(data, teams, path)
    if excluded_fixtures:
        excluded_fixture_set = set(excluded_fixtures)
        for sf in fixed_fixtures:
            if sf.fixture in excluded_fixture_set:
                raise SpecError(
                    f"{path}: fixed_fixtures entry {sf.fixture.home_team.name} vs "
                    f"{sf.fixture.away_team.name} is also excluded by exclude_fixtures"
                )
        kwargs["excluded_fixtures"] = excluded_fixtures

    if "latest_internal_match_date" in data:
        latest_internal_match_date = _parse_date(
            data["latest_internal_match_date"],
            f"{path}: 'latest_internal_match_date'",
        )
        for sf in fixed_fixtures:
            home_team = sf.fixture.home_team
            away_team = sf.fixture.away_team
            if (
                home_team.club == away_team.club
                and sf.date > latest_internal_match_date
            ):
                raise SpecError(
                    f"{path}: fixed_fixtures entry {home_team.name} vs "
                    f"{away_team.name} on {sf.date.isoformat()} is after "
                    "latest_internal_match_date"
                )
        kwargs["latest_internal_match_date"] = latest_internal_match_date

    if "earliest_match_date" in data:
        earliest_match_date = _parse_date(
            data["earliest_match_date"], f"{path}: 'earliest_match_date'"
        )
    else:
        earliest_match_date = date.today()
    if earliest_match_date < date.today():
        logger.warning(
            "%s: earliest_match_date %s is in the past",
            path,
            earliest_match_date.isoformat(),
        )
    kwargs["earliest_match_date"] = earliest_match_date

    for sf in fixed_fixtures:
        for team in (sf.fixture.home_team, sf.fixture.away_team):
            cutoff = club_constraints.club_latest_match_date.get(team.club)
            if cutoff is not None and sf.date > cutoff:
                raise SpecError(
                    f"{path}: fixed_fixtures entry {sf.fixture.home_team.name} vs "
                    f"{sf.fixture.away_team.name} on {sf.date.isoformat()} is after "
                    f"club_constraints[{team.club!r}].latest_match_date "
                    f"({cutoff.isoformat()})"
                )

    parameters = fmodel.Parameters(
        teams=list(teams.values()),
        home_dates=home_dates,
        division_schemes=divisions.schemes,
        spec_checksum=spec_checksum(path),
        **kwargs,
    )
    name = _require_str(data.get("name", ""), f"{path}: 'name'")
    is_final = _require_bool(data.get("is_final", False), f"{path}: 'is_final'")
    description = _require_str(data.get("description", ""), f"{path}: 'description'")
    return Spec(
        parameters=parameters,
        clubs=clubs,
        name=name,
        is_final=is_final,
        description=description,
    )
