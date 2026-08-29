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

    def construct_mapping(self, node, deep=False):
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
    draft: bool = False
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


def _parse_max_concurrent_home_matches_value(
    value: Any, context: str
) -> fmodel.MaxConcurrentHomeMatches:
    """A club's max_concurrent_home_matches value: either a plain integer, null (no
    limit from this mechanism -- see fmodel.MaxConcurrentHomeMatches), or a mapping
    with 'default' (an integer or null) and optionally 'overrides' (a date-keyed
    mapping of integer-or-null overrides of that default).
    """
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return fmodel.MaxConcurrentHomeMatches(default=value)
    if isinstance(value, dict):
        unsupported = value.keys() - {"default", "overrides"}
        if unsupported:
            raise SpecError(f"{context}: unsupported field(s) {sorted(unsupported)}")
        if "default" not in value:
            raise SpecError(f"{context} missing required field 'default'")
        default = _require_int_or_none(value["default"], f"{context}.default")

        overrides_spec = value.get("overrides")
        overrides: dict[date, int | None] = {}
        if overrides_spec is not None:
            if not isinstance(overrides_spec, dict):
                raise SpecError(f"{context}.overrides must be a mapping")
            for date_key, count in overrides_spec.items():
                override_date = _parse_date(date_key, f"{context}.overrides")
                overrides[override_date] = _require_int_or_none(
                    count, f"{context}.overrides[{date_key!r}]"
                )
        return fmodel.MaxConcurrentHomeMatches(default=default, overrides=overrides)
    raise SpecError(
        f"{context}: expected an integer, null (unlimited), or a mapping with "
        f"'default' and optionally 'overrides', got {value!r}"
    )


_CLUB_CONSTRAINT_FIELD_KEYS = {
    "home_dates",
    "unavailable_away_dates",
    "max_concurrent_home_matches",
    "max_home_dates_used",
    "teams",
    "avoid_coscheduling_teams",
}

_TEAM_CONSTRAINT_FIELD_KEYS = {"unavailable_home_dates", "unavailable_away_dates"}

_AVOID_COSCHEDULING_FIELD_KEYS = {"teams", "within_days", "applies_to"}

# Constraint types with a notion of a default, overridable per club. Other constraint
# types (home_dates, unavailable_away_dates, max_home_dates_used, teams,
# avoid_coscheduling_teams) have no meaningful spec-wide default, so aren't accepted
# under 'defaults'.
_CLUB_CONSTRAINT_DEFAULTS_KEYS = {"max_concurrent_home_matches"}


@dataclasses.dataclass(frozen=True)
class _ClubConstraints:
    home_dates: dict[str, list[date]]
    unavailable_away_dates: dict[str, list[date]]
    max_concurrent_home_matches: dict[str, fmodel.MaxConcurrentHomeMatches]
    max_home_dates_used: dict[str, int]
    team_home_dates: dict[fmodel.Team, list[date]]
    team_unavailable_away_dates: dict[fmodel.Team, list[date]]
    avoid_coscheduling_teams: list[fmodel.AvoidCoschedulingConstraint]


def _parse_club_constraints(
    data: Mapping[str, Any],
    clubs: Mapping[str, fmodel.Club],
    teams: Mapping[str, fmodel.Team],
    path: Path,
) -> _ClubConstraints:
    """Parse the 'club_constraints' section: per-club home_dates, unavailable_away_dates,
    max_concurrent_home_matches, max_home_dates_used, teams and
    avoid_coscheduling_teams, keyed directly by club ID.

    An optional 'defaults' entry (a sibling of the club entries) supplies a spec-wide
    default for constraint types that support one (currently just
    max_concurrent_home_matches); a club's own entry, if present, always takes
    precedence over 'defaults'. If 'defaults.max_concurrent_home_matches' is omitted,
    every club must have its own max_concurrent_home_matches entry.

    A club's optional 'teams' entry holds per-team exclusions, for clubs whose teams
    don't all share the same availability. Home dates are always specified at the club
    level; per-team variations are supported only via exclusions -- see
    _parse_club_team_constraints().

    A club's optional 'avoid_coscheduling_teams' entry lists groups of that club's own
    teams that shouldn't be scheduled too close together (e.g. adjacent-division teams
    drawing from the same pool of players) -- see _parse_avoid_coscheduling_teams().
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
    default_max_concurrent_home_matches = None
    if "max_concurrent_home_matches" in defaults_spec:
        default_max_concurrent_home_matches = _parse_max_concurrent_home_matches_value(
            defaults_spec["max_concurrent_home_matches"],
            f"{path}: {section_name}.defaults.max_concurrent_home_matches",
        )

    unknown_clubs = section_spec.keys() - clubs.keys() - {"defaults"}
    if unknown_clubs:
        raise SpecError(
            f"{path}: {section_name} references unknown club(s) {sorted(unknown_clubs)}"
        )

    home_dates: dict[str, list[date]] = {}
    unavailable_away_dates: dict[str, list[date]] = {}
    max_concurrent_home_matches: dict[str, fmodel.MaxConcurrentHomeMatches] = {}
    max_home_dates_used: dict[str, int] = {}
    team_home_dates: dict[fmodel.Team, list[date]] = {}
    team_unavailable_away_dates: dict[fmodel.Team, list[date]] = {}
    avoid_coscheduling_teams: list[fmodel.AvoidCoschedulingConstraint] = []
    missing_max_concurrent: list[str] = []

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
        unavailable_away_dates[club_id] = _parse_date_list(
            club_spec.get("unavailable_away_dates"),
            f"{path}: {section_name}[{club_id!r}].unavailable_away_dates",
        )

        if "max_concurrent_home_matches" in club_spec:
            max_concurrent_home_matches[club_id] = (
                _parse_max_concurrent_home_matches_value(
                    club_spec["max_concurrent_home_matches"],
                    f"{path}: {section_name}[{club_id!r}].max_concurrent_home_matches",
                )
            )
        elif default_max_concurrent_home_matches is not None:
            max_concurrent_home_matches[club_id] = default_max_concurrent_home_matches
        else:
            missing_max_concurrent.append(club_id)

        if "max_home_dates_used" in club_spec:
            max_home_dates_used[club_id] = _require_int(
                club_spec["max_home_dates_used"],
                f"{path}: {section_name}[{club_id!r}].max_home_dates_used",
            )

        club_team_home_dates, club_team_unavailable_away_dates = (
            _parse_club_team_constraints(
                club_spec.get("teams", {}),
                club_id,
                teams,
                home_dates[club_id],
                f"{path}: {section_name}[{club_id!r}].teams",
            )
        )
        team_home_dates.update(club_team_home_dates)
        team_unavailable_away_dates.update(club_team_unavailable_away_dates)

        avoid_coscheduling_teams.extend(
            _parse_avoid_coscheduling_teams(
                club_spec.get("avoid_coscheduling_teams"),
                club_id,
                teams,
                f"{path}: {section_name}[{club_id!r}].avoid_coscheduling_teams",
            )
        )

    if missing_max_concurrent:
        raise SpecError(
            f"{path}: {section_name} missing max_concurrent_home_matches for "
            f"club(s) {sorted(missing_max_concurrent)} (no "
            f"{section_name}.defaults.max_concurrent_home_matches set)"
        )

    return _ClubConstraints(
        home_dates=home_dates,
        unavailable_away_dates=unavailable_away_dates,
        max_concurrent_home_matches=max_concurrent_home_matches,
        max_home_dates_used=max_home_dates_used,
        team_home_dates=team_home_dates,
        team_unavailable_away_dates=team_unavailable_away_dates,
        avoid_coscheduling_teams=avoid_coscheduling_teams,
    )


def _parse_club_team_constraints(
    teams_spec: Any,
    club_id: str,
    teams: Mapping[str, fmodel.Team],
    club_home_dates: list[date],
    context: str,
) -> tuple[dict[fmodel.Team, list[date]], dict[fmodel.Team, list[date]]]:
    """Parse a club_constraints entry's optional 'teams' sub-section: per-team
    exclusions for clubs whose teams don't all share the same availability.
    Home dates are always specified at the club level; per-team variations are
    supported only via exclusions.

    A team's unavailable_home_dates entry, if given, lists dates on which that team
    specifically can't host (e.g. its venue slot is taken by another of the club's
    teams); the team's effective home dates are the club's home_dates minus these.
    An entry not currently in the club's home_dates (e.g. a date commented out and
    held in reserve) has no effect yet but isn't an error -- this lets a team's
    unavailability be recorded ahead of that date being added to (or uncommented
    from) home_dates later, without needing to remember to add it then; a warning is
    logged so the mismatch isn't silently missed. A team's unavailable_away_dates
    entry, if given, is additional to its club's unavailable_away_dates (not instead
    of it).
    """
    if not isinstance(teams_spec, dict):
        raise SpecError(f"{context} must be a mapping")

    team_home_dates: dict[fmodel.Team, list[date]] = {}
    team_unavailable_away_dates: dict[fmodel.Team, list[date]] = {}
    for team_id, team_spec in teams_spec.items():
        team_context = f"{context}[{team_id!r}]"
        if team_id not in teams:
            raise SpecError(f"{team_context} references unknown team {team_id!r}")
        team = teams[team_id]
        if team.club != club_id:
            raise SpecError(
                f"{team_context}: team {team_id!r} belongs to club {team.club!r}, "
                f"not {club_id!r}"
            )
        if not isinstance(team_spec, dict):
            raise SpecError(f"{team_context} must be a mapping")
        unsupported = team_spec.keys() - _TEAM_CONSTRAINT_FIELD_KEYS
        if unsupported:
            raise SpecError(
                f"{team_context}.{sorted(unsupported)} not supported (only "
                f"{sorted(_TEAM_CONSTRAINT_FIELD_KEYS)} are)"
            )

        if "unavailable_home_dates" in team_spec:
            excluded = _parse_date_list(
                team_spec["unavailable_home_dates"],
                f"{team_context}.unavailable_home_dates",
            )
            not_yet_active = [d for d in excluded if d not in club_home_dates]
            if not_yet_active:
                logger.warning(
                    "%s.unavailable_home_dates: %s not currently in %r's "
                    "home_dates (ok if held in reserve there; has no effect "
                    "until it is)",
                    team_context,
                    [d.isoformat() for d in not_yet_active],
                    club_id,
                )
            excluded_set = set(excluded)
            team_home_dates[team] = [
                d for d in club_home_dates if d not in excluded_set
            ]

        if "unavailable_away_dates" in team_spec:
            team_unavailable_away_dates[team] = _parse_date_list(
                team_spec["unavailable_away_dates"],
                f"{team_context}.unavailable_away_dates",
            )

    return team_home_dates, team_unavailable_away_dates


def _parse_avoid_coscheduling_teams(
    entries_spec: Any,
    club_id: str,
    teams: Mapping[str, fmodel.Team],
    context: str,
) -> list[fmodel.AvoidCoschedulingConstraint]:
    """Parse a club_constraints entry's optional 'avoid_coscheduling_teams' list.

    Each entry names a group of that club's own teams (all of which must belong to
    this club), an optional 'within_days' window (default 0, i.e. the same date), and
    an optional 'applies_to' scope ('home', 'away' or the default 'both'): the solver
    then allows at most one match involving any of those teams -- counting only the
    matches of the kind named by 'applies_to' -- within any window of that many days,
    e.g. two teams that share players shouldn't both be fielded on the same date.
    """
    if entries_spec is None:
        return []
    if not isinstance(entries_spec, list):
        raise SpecError(f"{context} must be a list")

    constraints: list[fmodel.AvoidCoschedulingConstraint] = []
    for i, entry in enumerate(entries_spec):
        entry_context = f"{context}[{i}]"
        if not isinstance(entry, dict):
            raise SpecError(f"{entry_context} must be a mapping")
        unsupported = entry.keys() - _AVOID_COSCHEDULING_FIELD_KEYS
        if unsupported:
            raise SpecError(
                f"{entry_context}.{sorted(unsupported)} not supported (only "
                f"{sorted(_AVOID_COSCHEDULING_FIELD_KEYS)} are)"
            )
        if "teams" not in entry:
            raise SpecError(f"{entry_context} missing required field 'teams'")

        team_ids = entry["teams"]
        if not isinstance(team_ids, list) or not team_ids:
            raise SpecError(f"{entry_context}.teams must be a non-empty list")

        entry_teams: list[fmodel.Team] = []
        seen_team_ids: set[str] = set()
        for team_id in team_ids:
            if team_id in seen_team_ids:
                raise SpecError(f"{entry_context}.teams: duplicate team {team_id!r}")
            seen_team_ids.add(team_id)
            if team_id not in teams:
                raise SpecError(
                    f"{entry_context}.teams references unknown team {team_id!r}"
                )
            team = teams[team_id]
            if team.club != club_id:
                raise SpecError(
                    f"{entry_context}.teams: team {team_id!r} belongs to club "
                    f"{team.club!r}, not {club_id!r}"
                )
            entry_teams.append(team)

        within_days = 0
        if "within_days" in entry:
            within_days = _require_int(
                entry["within_days"], f"{entry_context}.within_days"
            )
            if within_days < 0:
                raise SpecError(f"{entry_context}.within_days must be >= 0")

        applies_to = fmodel.CoschedulingScope.BOTH
        if "applies_to" in entry:
            try:
                applies_to = fmodel.CoschedulingScope(entry["applies_to"])
            except ValueError:
                allowed = ", ".join(repr(s.value) for s in fmodel.CoschedulingScope)
                raise SpecError(
                    f"{entry_context}.applies_to must be one of {allowed}, got "
                    f"{entry['applies_to']!r}"
                ) from None

        constraints.append(
            fmodel.AvoidCoschedulingConstraint(
                teams=entry_teams, within_days=within_days, applies_to=applies_to
            )
        )

    return constraints


def _parse_fixed_fixtures(
    data: Mapping[str, Any],
    teams: Mapping[str, fmodel.Team],
    home_dates: Mapping[str, list[date]],
    team_home_dates: Mapping[fmodel.Team, list[date]],
    path: Path,
) -> list[fmodel.ScheduledFixture]:
    """Parse the optional 'fixed_fixtures' section: fixtures pinned to a specific date.

    Each entry references a home and away team (by team ID) and a date. The two
    teams must be in the same division, and the date must be one of the home team's
    allowed home dates (its club's home_dates, minus any
    club_constraints[club].teams[team].unavailable_home_dates for that team).
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
        effective_home_dates = team_home_dates.get(
            home_team, home_dates.get(home_team.club, [])
        )
        if fixture_date not in effective_home_dates:
            raise SpecError(
                f"{context}: {fixture_date.isoformat()} is not one of {home_id!r}'s "
                "allowed home dates"
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

    avoid_dates = _parse_date_list(data.get("avoid_dates"), f"{path}: 'avoid_dates'")

    club_constraints = _parse_club_constraints(data, clubs, teams, path)
    home_dates = club_constraints.home_dates
    unavailable_away_dates = {
        club_id: sorted(set(dates) | set(avoid_dates))
        for club_id, dates in club_constraints.unavailable_away_dates.items()
    }
    max_concurrent_home_matches = club_constraints.max_concurrent_home_matches
    team_home_dates = club_constraints.team_home_dates
    team_unavailable_away_dates = club_constraints.team_unavailable_away_dates

    kwargs: dict[str, Any] = {}
    if "min_gap_days" in data:
        kwargs["min_gap_days"] = data["min_gap_days"]
    if club_constraints.max_home_dates_used:
        kwargs["max_home_dates_used"] = club_constraints.max_home_dates_used
    if team_home_dates:
        kwargs["team_home_dates"] = team_home_dates
    if team_unavailable_away_dates:
        kwargs["team_unavailable_away_dates"] = team_unavailable_away_dates
    if club_constraints.avoid_coscheduling_teams:
        kwargs["avoid_coscheduling_teams"] = club_constraints.avoid_coscheduling_teams

    fixed_fixtures = _parse_fixed_fixtures(
        data, teams, home_dates, team_home_dates, path
    )
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

    parameters = fmodel.Parameters(
        teams=list(teams.values()),
        home_dates=home_dates,
        unavailable_away_dates=unavailable_away_dates,
        max_concurrent_home_matches=max_concurrent_home_matches,
        division_schemes=divisions.schemes,
        **kwargs,
    )
    name = _require_str(data.get("name", ""), f"{path}: 'name'")
    draft = _require_bool(data.get("draft", False), f"{path}: 'draft'")
    description = _require_str(data.get("description", ""), f"{path}: 'description'")
    return Spec(
        parameters=parameters,
        clubs=clubs,
        name=name,
        draft=draft,
        description=description,
    )
