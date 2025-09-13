# Copyright 2025 Andrew Medworth
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

"""Generate Middlesex League fixtures based on synthetic dates."""

from __future__ import annotations

import calendar
import collections
import random
from collections.abc import Collection
from datetime import date, timedelta

import fmodel

# 2025/26 composition
_TEAMS = [
    fmodel.Team(division=d, club=c, index=i)
    for d, c, i in [
        (1, "Albany", 1),
        (1, "Hackney", 1),
        (1, "Hammersmith", 1),
        (1, "Hendon", 1),
        (1, "Kings Head", 1),
        (1, "Metropolitan", 1),
        (1, "Muswell Hill", 1),
        (1, "West London", 1),
        (2, "Ealing", 1),
        (2, "Hammersmith", 2),
        (2, "Harrow", 1),
        (2, "Harrow", 2),
        (2, "Hendon", 2),
        (2, "Hendon", 3),
        (2, "Kings Head", 2),
        (2, "Muswell Hill", 2),
        (2, "Willesden & Brent", 1),
        (3, "Ealing", 2),
        (3, "Hackney", 2),
        (3, "Hammersmith", 3),
        (3, "Hammersmith", 4),
        (3, "Hendon", 4),
        (3, "Hendon", 5),
        (3, "Kings Head", 3),
        (3, "Potters Bar", 1),
    ]
]

_SEASON_START = date(2025, 9, 1)
_SEASON_END = date(2026, 5, 30)


def gen_dates(
    from_date: date,
    to_date: date,
    day_of_week: int,
    exclude_month_occurrences: Collection[int] | None = None,
) -> list[date]:
    """Generate all occurrences of a specific weekday between two dates, excluding certain monthly occurrences.

    Args:
        from_date: Start date (inclusive)
        to_date: End date (inclusive)
        day_of_week: Day of week to generate (0=Monday, 1=Tuesday, ..., 6=Sunday)
        except_nth_occurrence: Collection of nth occurrences to exclude per month (e.g., [1] excludes first occurrence, [1, 3] excludes first and third)

    Returns:
        List of dates that match the specified weekday and are not excluded nth occurrences
    """
    if exclude_month_occurrences is None:
        exclude_month_occurrences = []

    result = []
    current_date = from_date
    current_month = (current_date.year, current_date.month)
    month_occurrences = 0

    while current_date.weekday() != day_of_week:
        current_date += timedelta(days=1)

    while current_date <= to_date:
        if current_month != (current_date.year, current_date.month):
            current_month = (current_date.year, current_date.month)
            month_occurrences = 0
        month_occurrences += 1

        if month_occurrences not in exclude_month_occurrences:
            result.append(current_date)

        current_date += timedelta(days=7)

    return result


def remove_random(dates: Collection[date], fraction: float) -> list[date]:
    result = list(dates)
    random.shuffle(result)
    return sorted(list(result[: int(len(result) * (1 - fraction))]))


_HOME_DATES = {
    "Albany": gen_dates(_SEASON_START, _SEASON_END, day_of_week=calendar.MONDAY),
    "Ealing": gen_dates(_SEASON_START, _SEASON_END, day_of_week=calendar.MONDAY),
    "Hackney": gen_dates(_SEASON_START, _SEASON_END, day_of_week=calendar.WEDNESDAY),
    "Hammersmith": remove_random(
        gen_dates(_SEASON_START, _SEASON_END, day_of_week=calendar.THURSDAY), 0.5
    ),
    "Harrow": gen_dates(
        _SEASON_START,
        _SEASON_END,
        day_of_week=calendar.THURSDAY,
        exclude_month_occurrences=[1],
    ),
    "Hendon": gen_dates(
        _SEASON_START,
        _SEASON_END,
        day_of_week=calendar.THURSDAY,
        exclude_month_occurrences=[1],
    ),
    "Kings Head": remove_random(
        gen_dates(_SEASON_START, _SEASON_END, day_of_week=calendar.THURSDAY), 0.2
    ),
    "Metropolitan": gen_dates(
        _SEASON_START, _SEASON_END, day_of_week=calendar.THURSDAY
    ),
    "Muswell Hill": [
        d
        for d in gen_dates(_SEASON_START, _SEASON_END, day_of_week=calendar.TUESDAY)
        if d.month != 12
    ],
    "Potters Bar": gen_dates(_SEASON_START, _SEASON_END, day_of_week=calendar.MONDAY),
    "West London": gen_dates(
        _SEASON_START, _SEASON_END, day_of_week=calendar.WEDNESDAY
    ),
    "Willesden & Brent": gen_dates(
        _SEASON_START, _SEASON_END, day_of_week=calendar.WEDNESDAY
    ),
}

_UNAVAILABLE_AWAY_DATES = {
    "Albany": remove_random(
        gen_dates(_SEASON_START, _SEASON_END, day_of_week=calendar.THURSDAY), 0.5
    ),
}

_MAX_CONCURRENT_HOME_MATCHES = 2
_MIN_MATCH_GAP_DAYS = 7


def print_fixtures(fixtures: Collection[fmodel.ScheduledFixture]) -> None:
    fixtures_by_club = collections.defaultdict(list)
    fixtures_by_team = collections.defaultdict(list)
    for sf in fixtures:
        fixtures_by_club[sf.fixture.home_team.club].append(sf)
        fixtures_by_club[sf.fixture.away_team.club].append(sf)
        fixtures_by_team[sf.fixture.home_team].append(sf)
        fixtures_by_team[sf.fixture.away_team].append(sf)

    print("Fixtures by club:")
    for club, club_fixtures in fixtures_by_club.items():
        print(club)
        last_date = None
        for sf in sorted(club_fixtures, key=lambda sf: sf.date):
            club_teams = [
                (t, v, o)
                for (t, v, o) in [
                    (sf.fixture.home_team, "Home", sf.fixture.away_team.name),
                    (sf.fixture.away_team, "Away", sf.fixture.home_team.name),
                ]
                if t.club == club
            ]
            if last_date:
                gap = (sf.date - last_date).days
                gap_str = f" (+{gap}d)"
            else:
                gap_str = ""
            for team, venue, opp in club_teams:
                print(
                    f"  {sf.date.strftime('%a')} {sf.date.isoformat()}: {team.name} {venue} vs {opp}{gap_str}"
                )
            last_date = sf.date

    print("\nFixtures by team:")
    for team in sorted(fixtures_by_team.keys(), key=lambda t: (t.club, t.index)):
        print(team.name)
        last_date = None
        for sf in sorted(fixtures_by_team[team], key=lambda sf: sf.date):
            if sf.fixture.home_team == team:
                venue = "Home"
                opp = sf.fixture.away_team.name
            else:
                venue = "Away"
                opp = sf.fixture.home_team.name
            if last_date:
                gap = (sf.date - last_date).days
                gap_str = f" (+{gap}d)"
            else:
                gap_str = ""
            print(
                f"  {sf.date.strftime('%a')} {sf.date.isoformat()}: {venue} vs {opp}{gap_str}"
            )
            last_date = sf.date


def build_params() -> fmodel.Parameters:
    return fmodel.Parameters(
        teams=_TEAMS,
        home_dates=_HOME_DATES,
        unavailable_away_dates=_UNAVAILABLE_AWAY_DATES,
        min_gap_days=_MIN_MATCH_GAP_DAYS,
        max_concurrent_home_matches=_MAX_CONCURRENT_HOME_MATCHES,
    )


def main() -> None:
    random.seed(0)
    fixtures = fmodel.solve(build_params())
    print_fixtures(fixtures)


if __name__ == "__main__":
    main()
