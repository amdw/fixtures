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

"""Render a solved fixture list as CSV, for importing into other tools.

Two files are written, covering every division (there's a ``division`` column
to filter on):

* ``all-matches.csv`` -- one row per match, ``home_team`` vs ``away_team``.
* ``all-matches-by-team.csv`` -- two rows per match, one from each team's point
  of view (``team``, ``opponent``, ``home_or_away``), so a club can pull just
  its own team's fixtures straight into a calendar.

Each team is given as its display name, its club's name (``*_club``, e.g.
``Albany``) and its index within that club (``*_index``, e.g. ``1``), so the
club and team number are available directly even when the display name is a
``name_override``.

Both files list withheld matches (a spec's ``exclude_fixtures``) too, with an
empty ``date``, mirroring the HTML report's "TBC" rows. Dates are ISO
``yyyy-mm-dd``; venue, start time and time limit are always the home team's
club's, as in the HTML report.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import reportdata

if TYPE_CHECKING:
    import fmodel

ALL_MATCHES_FILENAME = "all-matches.csv"
BY_TEAM_FILENAME = "all-matches-by-team.csv"

_ALL_MATCHES_HEADER = [
    "date",
    "division",
    "home_team",
    "home_team_club",
    "home_team_index",
    "away_team",
    "away_team_club",
    "away_team_index",
    "venue",
    "venue_address",
    "start_time",
    "time_limit",
]
_BY_TEAM_HEADER = [
    "date",
    "division",
    "team",
    "team_club",
    "team_index",
    "opponent",
    "opponent_club",
    "opponent_index",
    "home_or_away",
    "venue",
    "venue_address",
    "start_time",
    "time_limit",
]


def _iso(d: date | None) -> str:
    return d.isoformat() if d is not None else ""


def _team_columns(team: fmodel.Team, clubs: Mapping[str, fmodel.Club]) -> list[str]:
    """(display name, club name, team index) -- the three columns describing a team."""
    return [
        reportdata.team_name(team, clubs),
        clubs[team.club].name,
        str(team.index),
    ]


def _venue_columns(
    fixture: fmodel.Fixture, clubs: Mapping[str, fmodel.Club]
) -> list[str]:
    home_club = clubs[fixture.home_team.club]
    return [
        home_club.home_venue_name,
        home_club.home_venue_address,
        home_club.home_start_time,
        home_club.home_time_limit,
    ]


def _match_row(
    fixture: fmodel.Fixture,
    fixture_date: date | None,
    clubs: Mapping[str, fmodel.Club],
) -> list[str]:
    return [
        _iso(fixture_date),
        str(fixture.home_team.division),
        *_team_columns(fixture.home_team, clubs),
        *_team_columns(fixture.away_team, clubs),
        *_venue_columns(fixture, clubs),
    ]


def _team_row(
    team: fmodel.Team,
    fixture: fmodel.Fixture,
    fixture_date: date | None,
    clubs: Mapping[str, fmodel.Club],
) -> list[str]:
    is_home = fixture.home_team == team
    opponent = fixture.away_team if is_home else fixture.home_team
    return [
        _iso(fixture_date),
        str(team.division),
        *_team_columns(team, clubs),
        *_team_columns(opponent, clubs),
        "home" if is_home else "away",
        *_venue_columns(fixture, clubs),
    ]


def _all_matches_rows(
    fixtures: Collection[fmodel.ScheduledFixture],
    excluded_fixtures: Collection[fmodel.Fixture],
    clubs: Mapping[str, fmodel.Club],
) -> list[list[str]]:
    rows = [
        _match_row(sf.fixture, sf.date, clubs)
        for sf in reportdata.by_date_home_away(fixtures, clubs, with_division=True)
    ]
    rows += [
        _match_row(f, None, clubs)
        for f in reportdata.by_home_away(excluded_fixtures, clubs, with_division=True)
    ]
    return rows


def _by_team_rows(
    teams: Collection[fmodel.Team],
    fixtures: Collection[fmodel.ScheduledFixture],
    excluded_fixtures: Collection[fmodel.Fixture],
    clubs: Mapping[str, fmodel.Club],
) -> list[list[str]]:
    rows: list[list[str]] = []
    for team in sorted(teams, key=lambda t: reportdata.team_sort_key(t, clubs)):
        team_fixtures = [
            sf
            for sf in fixtures
            if team in (sf.fixture.home_team, sf.fixture.away_team)
        ]
        for sf in reportdata.by_date_opponent(team, team_fixtures, clubs):
            rows.append(_team_row(team, sf.fixture, sf.date, clubs))

        team_excluded = [
            f for f in excluded_fixtures if team in (f.home_team, f.away_team)
        ]
        for f in sorted(
            team_excluded,
            key=lambda f: reportdata.team_sort_key(
                f.away_team if f.home_team == team else f.home_team, clubs
            ),
        ):
            rows.append(_team_row(team, f, None, clubs))
    return rows


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    path.write_text(buffer.getvalue())


def generate_csv(
    fixtures: Collection[fmodel.ScheduledFixture],
    teams: Collection[fmodel.Team],
    clubs: Mapping[str, fmodel.Club],
    output_dir: Path,
    excluded_fixtures: Collection[fmodel.Fixture] = (),
) -> list[Path]:
    """Write the CSV exports of a solved fixture list into output_dir.

    See the module docstring for the two files and their columns. Returns the
    paths written, in the order (all-matches, all-matches-by-team).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    all_matches_path = output_dir / ALL_MATCHES_FILENAME
    _write_csv(
        all_matches_path,
        _ALL_MATCHES_HEADER,
        _all_matches_rows(fixtures, excluded_fixtures, clubs),
    )

    by_team_path = output_dir / BY_TEAM_FILENAME
    _write_csv(
        by_team_path,
        _BY_TEAM_HEADER,
        _by_team_rows(teams, fixtures, excluded_fixtures, clubs),
    )

    return [all_matches_path, by_team_path]
