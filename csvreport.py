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

Two whole-season files are written, covering every division (there's a
``division`` column to filter on):

* ``all-matches.csv`` -- one row per match, ``home_team`` vs ``away_team``.
* ``all-matches-by-team.csv`` -- two rows per match, one from each team's point
  of view (``team``, ``opponent``, ``home_or_away``), so a club can pull just
  its own team's fixtures straight into a calendar.

Then, for each team, ``team-<club-slug>-<index>.csv`` -- exactly the
``all-matches-by-team.csv`` rows for that one team; and for each club,
``club-<club-slug>-dates.csv`` -- one row per date the club is in action, holding
that ``date`` and a comma-separated, home-team-order list of the club's matches
(home and away) that day as ``home - away``, to join onto a calendar spreadsheet
by date.

In the whole-season and per-team files each team is given as its display name,
its club's name (``*_club``, e.g. ``Albany``) and its index within that club
(``*_index``, e.g. ``1``), so the club and team number are available directly
even when the display name is a ``name_override``.

Every file lists withheld matches (a spec's ``exclude_fixtures``) too with an
empty ``date`` -- one row each in the whole-season and per-team files, all
together on a single trailing row in the per-club file -- mirroring the HTML
report's "TBC" rows. Dates are ISO ``yyyy-mm-dd``; venue, start time and time
limit are always the home team's club's, as in the HTML report.
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
    import fixturespec
    import fmodel

ALL_MATCHES_FILENAME = "all-matches.csv"
BY_TEAM_FILENAME = "all-matches-by-team.csv"

_CLUB_BY_DATE_HEADER = ["date", "matches"]

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


def _one_team_rows(
    team: fmodel.Team,
    fixtures: Collection[fmodel.ScheduledFixture],
    excluded_fixtures: Collection[fmodel.Fixture],
    clubs: Mapping[str, fmodel.Club],
) -> list[list[str]]:
    """The by-team rows for a single team: its scheduled matches by date then
    opponent, then any withheld matches (empty date) by opponent."""
    rows: list[list[str]] = []
    team_fixtures = [
        sf for sf in fixtures if team in (sf.fixture.home_team, sf.fixture.away_team)
    ]
    for sf in reportdata.by_date_opponent(team, team_fixtures, clubs):
        rows.append(_team_row(team, sf.fixture, sf.date, clubs))

    team_excluded = [f for f in excluded_fixtures if team in (f.home_team, f.away_team)]
    for f in sorted(
        team_excluded,
        key=lambda f: reportdata.team_sort_key(
            f.away_team if f.home_team == team else f.home_team, clubs
        ),
    ):
        rows.append(_team_row(team, f, None, clubs))
    return rows


def _by_team_rows(
    teams: Collection[fmodel.Team],
    fixtures: Collection[fmodel.ScheduledFixture],
    excluded_fixtures: Collection[fmodel.Fixture],
    clubs: Mapping[str, fmodel.Club],
) -> list[list[str]]:
    rows: list[list[str]] = []
    for team in sorted(teams, key=lambda t: reportdata.team_sort_key(t, clubs)):
        rows += _one_team_rows(team, fixtures, excluded_fixtures, clubs)
    return rows


def _match_description(
    fixture: fmodel.Fixture, clubs: Mapping[str, fmodel.Club]
) -> str:
    """A match as one string for the per-club file's list, e.g. ``Harrow 1 - Ealing 1``
    (home team first)."""
    return (
        f"{reportdata.team_name(fixture.home_team, clubs)} - "
        f"{reportdata.team_name(fixture.away_team, clubs)}"
    )


def _club_by_date_rows(
    club_id: str,
    fixtures: Collection[fmodel.ScheduledFixture],
    excluded_fixtures: Collection[fmodel.Fixture],
    clubs: Mapping[str, fmodel.Club],
) -> list[list[str]]:
    """One row per date club_id is in action: the ISO date and a comma-separated,
    home-team-order list of that club's matches (home and away) that day. Any
    withheld matches are listed together on a single trailing empty-date row,
    matching the other exports."""
    club_fixtures = [
        sf
        for sf in fixtures
        if club_id in (sf.fixture.home_team.club, sf.fixture.away_team.club)
    ]
    by_date: dict[date, list[str]] = {}
    for sf in reportdata.by_date_home_away(club_fixtures, clubs, with_division=True):
        by_date.setdefault(sf.date, []).append(_match_description(sf.fixture, clubs))
    rows = [[_iso(d), ", ".join(matches)] for d, matches in sorted(by_date.items())]

    club_excluded = [
        f for f in excluded_fixtures if club_id in (f.home_team.club, f.away_team.club)
    ]
    if club_excluded:
        descriptions = [
            _match_description(f, clubs)
            for f in reportdata.by_home_away(club_excluded, clubs, with_division=True)
        ]
        rows.append(["", ", ".join(descriptions)])
    return rows


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    path.write_text(buffer.getvalue())


def generate_csv(
    spec: fixturespec.Spec,
    fixtures: Collection[fmodel.ScheduledFixture],
    output_dir: Path,
) -> list[Path]:
    """Write the CSV exports of a solved fixture list into output_dir.

    `spec` supplies the teams, clubs and any excluded_fixtures (withheld matches,
    written with an empty date); `fixtures` is the solved schedule for it. See the
    module docstring for the files and their columns. Returns the paths written,
    in the order: all-matches, all-matches-by-team, then one club-<slug>-dates.csv
    per club and one team-<slug>.csv per team (each group in (club, index) order).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    teams = spec.parameters.teams
    clubs = spec.clubs
    excluded_fixtures = spec.parameters.excluded_fixtures

    paths: list[Path] = []

    all_matches_path = output_dir / ALL_MATCHES_FILENAME
    _write_csv(
        all_matches_path,
        _ALL_MATCHES_HEADER,
        _all_matches_rows(fixtures, excluded_fixtures, clubs),
    )
    paths.append(all_matches_path)

    by_team_path = output_dir / BY_TEAM_FILENAME
    _write_csv(
        by_team_path,
        _BY_TEAM_HEADER,
        _by_team_rows(teams, fixtures, excluded_fixtures, clubs),
    )
    paths.append(by_team_path)

    club_ids = sorted({t.club for t in teams}, key=lambda cid: clubs[cid].name)
    for club_id in club_ids:
        path = output_dir / reportdata.club_dates_csv_filename(club_id)
        _write_csv(
            path,
            _CLUB_BY_DATE_HEADER,
            _club_by_date_rows(club_id, fixtures, excluded_fixtures, clubs),
        )
        paths.append(path)

    for team in sorted(teams, key=lambda t: reportdata.team_sort_key(t, clubs)):
        path = output_dir / reportdata.team_csv_filename(team)
        _write_csv(
            path,
            _BY_TEAM_HEADER,
            _one_team_rows(team, fixtures, excluded_fixtures, clubs),
        )
        paths.append(path)

    return paths
