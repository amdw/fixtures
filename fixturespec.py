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

import dataclasses
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

import fmodel


class SpecError(ValueError):
    """Raised when a fixture specification file is invalid."""


@dataclasses.dataclass(frozen=True)
class Spec:
    """The result of loading a fixture specification: solver input plus reporting metadata."""

    parameters: fmodel.Parameters
    clubs: Mapping[str, fmodel.Club]


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
    return [_parse_date(v, context) for v in value]


def _parse_clubs(data: Mapping[str, Any], path: Path) -> dict[str, fmodel.Club]:
    clubs_spec = _require_mapping(data.get("clubs"), f"{path}: 'clubs'")

    clubs = {}
    for club_id, club_spec in clubs_spec.items():
        context = f"{path}: clubs[{club_id!r}]"
        if not isinstance(club_spec, dict):
            raise SpecError(f"{context} must be a mapping")
        required = {"name", "home_venue", "home_start_time", "home_time_limit"}
        missing = required - club_spec.keys()
        if missing:
            raise SpecError(f"{context} missing required field(s) {sorted(missing)}")
        clubs[club_id] = fmodel.Club(
            name=_require_str(club_spec["name"], f"{context}.name"),
            home_venue=_require_str(club_spec["home_venue"], f"{context}.home_venue"),
            home_start_time=_require_str(
                club_spec["home_start_time"], f"{context}.home_start_time"
            ),
            home_time_limit=_require_str(
                club_spec["home_time_limit"], f"{context}.home_time_limit"
            ),
        )
    return clubs


def _parse_teams(
    data: Mapping[str, Any], clubs: Mapping[str, fmodel.Club], path: Path
) -> dict[str, fmodel.Team]:
    teams_spec = _require_mapping(data.get("teams"), f"{path}: 'teams'")

    teams: dict[str, fmodel.Team] = {}
    seen_club_index: dict[tuple[str, int], str] = {}
    for team_id, team_spec in teams_spec.items():
        context = f"{path}: teams[{team_id!r}]"
        if not isinstance(team_spec, dict):
            raise SpecError(f"{context} must be a mapping")
        required = {"club", "index", "division"}
        missing = required - team_spec.keys()
        if missing:
            raise SpecError(f"{context} missing required field(s) {sorted(missing)}")

        club_id = team_spec["club"]
        if club_id not in clubs:
            raise SpecError(f"{context} references unknown club {club_id!r}")

        index = _require_int(team_spec["index"], f"{context}.index")
        division = _require_int(team_spec["division"], f"{context}.division")

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

        teams[team_id] = fmodel.Team(
            division=division, club=club_id, index=index, name_override=name_override
        )
    return teams


def _parse_divisions(
    data: Mapping[str, Any], teams: Mapping[str, fmodel.Team], path: Path
) -> None:
    """Validate the 'divisions' section (team IDs per division) against each team's own division."""
    divisions_spec = _require_mapping(data.get("divisions"), f"{path}: 'divisions'")

    seen_team_ids: set[str] = set()
    for division, team_ids in divisions_spec.items():
        context = f"{path}: divisions[{division!r}]"
        if not isinstance(team_ids, list) or not team_ids:
            raise SpecError(f"{context} must be a non-empty list of team IDs")
        for team_id in team_ids:
            if team_id not in teams:
                raise SpecError(f"{context} references unknown team {team_id!r}")
            if team_id in seen_team_ids:
                raise SpecError(
                    f"{path}: team {team_id!r} listed in more than one division"
                )
            seen_team_ids.add(team_id)
            actual_division = teams[team_id].division
            if actual_division != division:
                raise SpecError(
                    f"{context}: team {team_id!r} has division={actual_division!r} on its "
                    f"own entry, which doesn't match"
                )

    missing = teams.keys() - seen_team_ids
    if missing:
        raise SpecError(
            f"{path}: team(s) {sorted(missing)} not listed under 'divisions'"
        )


_DATES_SECTION_KEYS = {"clubs"}


def _parse_dates_section(
    data: Mapping[str, Any],
    clubs: Mapping[str, fmodel.Club],
    path: Path,
    section_name: str,
) -> dict[str, list[date]]:
    """Parse a home_dates/unavailable_away_dates section's 'clubs' sub-mapping.

    A 'teams' sub-mapping (for per-team date overrides) is planned but not yet supported.
    """
    result: dict[str, list[date]] = {club_id: [] for club_id in clubs}

    section_spec = data.get(section_name)
    if section_spec is None:
        return result
    if not isinstance(section_spec, dict):
        raise SpecError(f"{path}: {section_name!r} must be a mapping")

    unsupported = section_spec.keys() - _DATES_SECTION_KEYS
    if unsupported:
        raise SpecError(
            f"{path}: {section_name}.{sorted(unsupported)} not supported (only 'clubs' is, so far)"
        )

    clubs_section = section_spec.get("clubs")
    if clubs_section is None:
        return result
    if not isinstance(clubs_section, dict):
        raise SpecError(f"{path}: {section_name}.clubs must be a mapping")

    for club_id, dates in clubs_section.items():
        if club_id not in clubs:
            raise SpecError(
                f"{path}: {section_name}.clubs references unknown club {club_id!r}"
            )
        result[club_id] = _parse_date_list(
            dates, f"{path}: {section_name}.clubs[{club_id!r}]"
        )

    return result


def load_spec(spec_path: str | Path) -> Spec:
    """Load a fixture Spec (solver Parameters plus club reporting metadata) from a YAML file."""
    path = Path(spec_path)
    with path.open() as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise SpecError(f"{path}: top-level YAML content must be a mapping")

    clubs = _parse_clubs(data, path)
    teams = _parse_teams(data, clubs, path)
    _parse_divisions(data, teams, path)

    home_dates = _parse_dates_section(data, clubs, path, "home_dates")
    unavailable_away_dates = _parse_dates_section(
        data, clubs, path, "unavailable_away_dates"
    )

    kwargs: dict[str, Any] = {}
    if "min_gap_days" in data:
        kwargs["min_gap_days"] = data["min_gap_days"]
    if "max_concurrent_home_matches" in data:
        kwargs["max_concurrent_home_matches"] = data["max_concurrent_home_matches"]

    parameters = fmodel.Parameters(
        teams=list(teams.values()),
        home_dates=home_dates,
        unavailable_away_dates=unavailable_away_dates,
        **kwargs,
    )
    return Spec(parameters=parameters, clubs=clubs)
