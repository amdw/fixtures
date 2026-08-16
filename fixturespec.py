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

See README.md for a description of the expected YAML structure.
"""

from __future__ import annotations

import collections
import dataclasses
import itertools
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

import fmodel


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


def _parse_divisions(
    data: Mapping[str, Any], teams: Mapping[str, _TeamShell], path: Path
) -> dict[str, int]:
    """Parse the 'divisions' section (team IDs per division), returning each team's
    division. This is the only place a team's division is given - 'teams' entries
    don't repeat it."""
    divisions_spec = _require_mapping(data.get("divisions"), f"{path}: 'divisions'")

    team_divisions: dict[str, int] = {}
    for division_key, team_ids in divisions_spec.items():
        context = f"{path}: divisions[{division_key!r}]"
        division = _require_int(division_key, f"{context} key")
        if not isinstance(team_ids, list) or not team_ids:
            raise SpecError(f"{context} must be a non-empty list of team IDs")
        for team_id in team_ids:
            if team_id not in teams:
                raise SpecError(f"{context} references unknown team {team_id!r}")
            if team_id in team_divisions:
                raise SpecError(
                    f"{path}: team {team_id!r} listed in more than one division"
                )
            team_divisions[team_id] = division

    missing = teams.keys() - team_divisions.keys()
    if missing:
        raise SpecError(
            f"{path}: team(s) {sorted(missing)} not listed under 'divisions'"
        )
    return team_divisions


def _parse_max_concurrent_home_matches_value(
    value: Any, context: str
) -> fmodel.MaxConcurrentHomeMatches:
    if isinstance(value, int) and not isinstance(value, bool):
        return fmodel.MaxConcurrentHomeMatches(default=value)
    if isinstance(value, dict):
        unsupported = value.keys() - {"default", "overrides"}
        if unsupported:
            raise SpecError(f"{context}: unsupported field(s) {sorted(unsupported)}")
        if "default" not in value:
            raise SpecError(f"{context} missing required field 'default'")
        default = _require_int(value["default"], f"{context}.default")

        overrides_spec = value.get("overrides")
        overrides: dict[date, int] = {}
        if overrides_spec is not None:
            if not isinstance(overrides_spec, dict):
                raise SpecError(f"{context}.overrides must be a mapping")
            for date_key, count in overrides_spec.items():
                override_date = _parse_date(date_key, f"{context}.overrides")
                overrides[override_date] = _require_int(
                    count, f"{context}.overrides[{date_key!r}]"
                )
        return fmodel.MaxConcurrentHomeMatches(default=default, overrides=overrides)
    raise SpecError(
        f"{context}: expected an integer, or a mapping with 'default' and optionally "
        f"'overrides', got {value!r}"
    )


_CLUB_CONSTRAINT_FIELD_KEYS = {
    "home_dates",
    "unavailable_away_dates",
    "max_concurrent_home_matches",
    "max_home_dates_used",
}

# Constraint types with a notion of a default, overridable per club. Other constraint
# types (home_dates, unavailable_away_dates, max_home_dates_used) have no meaningful
# spec-wide default, so aren't accepted under 'defaults'.
_CLUB_CONSTRAINT_DEFAULTS_KEYS = {"max_concurrent_home_matches"}


@dataclasses.dataclass(frozen=True)
class _ClubConstraints:
    home_dates: dict[str, list[date]]
    unavailable_away_dates: dict[str, list[date]]
    max_concurrent_home_matches: dict[str, fmodel.MaxConcurrentHomeMatches]
    max_home_dates_used: dict[str, int]


def _parse_club_constraints(
    data: Mapping[str, Any], clubs: Mapping[str, fmodel.Club], path: Path
) -> _ClubConstraints:
    """Parse the 'club_constraints' section: per-club home_dates, unavailable_away_dates,
    max_concurrent_home_matches and max_home_dates_used, keyed directly by club ID.

    An optional 'defaults' entry (a sibling of the club entries) supplies a spec-wide
    default for constraint types that support one (currently just
    max_concurrent_home_matches); a club's own entry, if present, always takes
    precedence over 'defaults'. If 'defaults.max_concurrent_home_matches' is omitted,
    every club must have its own max_concurrent_home_matches entry.
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
    )


def _parse_fixed_fixtures(
    data: Mapping[str, Any],
    teams: Mapping[str, fmodel.Team],
    home_dates: Mapping[str, list[date]],
    path: Path,
) -> list[fmodel.ScheduledFixture]:
    """Parse the optional 'fixed_fixtures' section: fixtures pinned to a specific date.

    Each entry references a home and away team (by team ID) and a date. The two
    teams must be in the same division, and the date must be one of the home
    team's club's allowed home dates.
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
                f"{context}: {fixture_date.isoformat()} is not one of {home_id!r}'s "
                "club's home dates"
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
    of excluded (home, away) Fixture pairs.
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

    teams_by_division: dict[int, list[fmodel.Team]] = collections.defaultdict(list)
    for team in teams.values():
        teams_by_division[team.division].append(team)

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


def load_spec(spec_path: str | Path) -> Spec:
    """Load a fixture Spec (solver Parameters plus club reporting metadata) from a YAML file."""
    path = Path(spec_path)
    with path.open() as f:
        try:
            # _NoDuplicateKeysSafeLoader subclasses SafeLoader, so this is as safe as
            # yaml.safe_load(); ruff's S506 can't see that from the loader's name alone.
            data = yaml.load(f, Loader=_NoDuplicateKeysSafeLoader)  # noqa: S506
        except yaml.constructor.ConstructorError as e:
            raise SpecError(f"{path}: {e}") from e

    if not isinstance(data, dict):
        raise SpecError(f"{path}: top-level YAML content must be a mapping")

    clubs = _parse_clubs(data, path)
    team_shells = _parse_teams(data, clubs, path)
    team_divisions = _parse_divisions(data, team_shells, path)
    teams = {
        team_id: fmodel.Team(
            division=team_divisions[team_id],
            club=shell.club,
            index=shell.index,
            name_override=shell.name_override,
        )
        for team_id, shell in team_shells.items()
    }

    avoid_dates = _parse_date_list(data.get("avoid_dates"), f"{path}: 'avoid_dates'")

    club_constraints = _parse_club_constraints(data, clubs, path)
    home_dates = club_constraints.home_dates
    unavailable_away_dates = {
        club_id: sorted(set(dates) | set(avoid_dates))
        for club_id, dates in club_constraints.unavailable_away_dates.items()
    }
    max_concurrent_home_matches = club_constraints.max_concurrent_home_matches

    kwargs: dict[str, Any] = {}
    if "min_gap_days" in data:
        kwargs["min_gap_days"] = data["min_gap_days"]
    if club_constraints.max_home_dates_used:
        kwargs["max_home_dates_used"] = club_constraints.max_home_dates_used

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

    parameters = fmodel.Parameters(
        teams=list(teams.values()),
        home_dates=home_dates,
        unavailable_away_dates=unavailable_away_dates,
        max_concurrent_home_matches=max_concurrent_home_matches,
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
