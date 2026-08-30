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

"""Format-agnostic shaping of a solved fixture list.

Team display-name resolution and the row orderings the reports use, factored
out of htmlreport.py so the CSV export (csvreport.py) can order and name things
identically without going through the HTML renderer. Nothing here does any I/O
or produces any markup.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection, Mapping
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import fmodel


def team_name(team: fmodel.Team, clubs: Mapping[str, fmodel.Club]) -> str:
    if team.name_override:
        return team.name_override
    return f"{clubs[team.club].name} {team.index}"


def team_sort_key(
    team: fmodel.Team, clubs: Mapping[str, fmodel.Club]
) -> tuple[str, int]:
    """(club name, team index) of a team, used as a sort tie-break throughout."""
    return (clubs[team.club].name, team.index)


def by_date_home_away(
    fixtures: Collection[fmodel.ScheduledFixture],
    clubs: Mapping[str, fmodel.Club],
    with_division: bool,
) -> list[fmodel.ScheduledFixture]:
    def key(sf: fmodel.ScheduledFixture) -> tuple:
        home_team = sf.fixture.home_team
        division_part = (home_team.division,) if with_division else ()
        return (
            sf.date,
            *division_part,
            *team_sort_key(home_team, clubs),
            *team_sort_key(sf.fixture.away_team, clubs),
        )

    return sorted(fixtures, key=key)


def by_date_opponent(
    team: fmodel.Team,
    fixtures: Collection[fmodel.ScheduledFixture],
    clubs: Mapping[str, fmodel.Club],
) -> list[fmodel.ScheduledFixture]:
    def key(sf: fmodel.ScheduledFixture) -> tuple:
        opponent = (
            sf.fixture.away_team
            if sf.fixture.home_team == team
            else sf.fixture.home_team
        )
        return (sf.date, *team_sort_key(opponent, clubs))

    return sorted(fixtures, key=key)


def by_home_away(
    fixtures: Collection[fmodel.Fixture],
    clubs: Mapping[str, fmodel.Club],
    with_division: bool,
) -> list[fmodel.Fixture]:
    def key(f: fmodel.Fixture) -> tuple:
        division_part = (f.home_team.division,) if with_division else ()
        return (
            *division_part,
            *team_sort_key(f.home_team, clubs),
            *team_sort_key(f.away_team, clubs),
        )

    return sorted(fixtures, key=key)


@dataclasses.dataclass(frozen=True)
class ClubDateCounts:
    """How many distinct calendar dates a club is in action on: `total` across all
    its matches, `home` across the ones it hosts, `away` across the ones it visits.
    A date carrying both a home and an away match for the club (two of its teams
    out the same night, or an internal derby) counts once in `total` but in both
    `home` and `away`, so `total` can be less than `home + away`."""

    total: int
    home: int
    away: int


def club_date_counts(
    club_id: str, fixtures: Collection[fmodel.ScheduledFixture]
) -> ClubDateCounts:
    """Count club_id's distinct match dates over the given scheduled fixtures
    (which may be the whole season or any subset; fixtures not involving club_id
    are ignored)."""
    home_dates: set[date] = set()
    away_dates: set[date] = set()
    for sf in fixtures:
        if sf.fixture.home_team.club == club_id:
            home_dates.add(sf.date)
        if sf.fixture.away_team.club == club_id:
            away_dates.add(sf.date)
    return ClubDateCounts(
        total=len(home_dates | away_dates),
        home=len(home_dates),
        away=len(away_dates),
    )
