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

"""Test cases for the format-agnostic fixture-shaping helpers."""

import unittest
from datetime import date

import fmodel
import reportdata


def _sf(home: fmodel.Team, away: fmodel.Team, d: date) -> fmodel.ScheduledFixture:
    return fmodel.ScheduledFixture(
        fixture=fmodel.Fixture(home_team=home, away_team=away), date=d
    )


def _club(name: str) -> fmodel.Club:
    return fmodel.Club(
        name=name,
        home_venue_name=f"{name} Hall",
        home_venue_address=f"1 {name} Road",
        home_start_time="19:30",
        home_time_limit="75+15",
    )


class ReportDataTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        # Club id "z-club" deliberately sorts after "a-club" by id but its
        # display name "Aardvark" sorts first, so tests can tell which key wins.
        self.clubs = {
            "a-club": _club("Barnet"),
            "z-club": _club("Aardvark"),
        }
        self.barnet1 = fmodel.Team(division=1, club="a-club", index=1)
        self.barnet2 = fmodel.Team(division=1, club="a-club", index=2)
        self.aardvark1 = fmodel.Team(division=1, club="z-club", index=1)
        self.nick = fmodel.Team(
            division=1, club="z-club", index=2, name_override="Aardvark Nomads"
        )

    def test_team_name_falls_back_to_club_name_and_index(self) -> None:
        self.assertEqual(reportdata.team_name(self.barnet1, self.clubs), "Barnet 1")

    def test_team_name_prefers_override(self) -> None:
        self.assertEqual(reportdata.team_name(self.nick, self.clubs), "Aardvark Nomads")

    def test_team_sort_key_is_club_display_name_then_index(self) -> None:
        self.assertEqual(
            reportdata.team_sort_key(self.aardvark1, self.clubs), ("Aardvark", 1)
        )
        self.assertLess(
            reportdata.team_sort_key(self.aardvark1, self.clubs),
            reportdata.team_sort_key(self.barnet1, self.clubs),
        )

    def test_by_date_home_away_orders_by_date_then_home_then_away(self) -> None:
        later = _sf(self.barnet1, self.aardvark1, date(2025, 10, 1))
        early_b = _sf(self.barnet1, self.aardvark1, date(2025, 9, 1))
        early_a = _sf(self.aardvark1, self.barnet1, date(2025, 9, 1))

        ordered = reportdata.by_date_home_away(
            [later, early_b, early_a], self.clubs, with_division=False
        )
        # Same date: home team "Aardvark 1" sorts before "Barnet 1"; later date last.
        self.assertEqual(ordered, [early_a, early_b, later])

    def test_by_date_opponent_orders_by_date_then_opponent(self) -> None:
        vs_nick = _sf(self.aardvark1, self.nick, date(2025, 9, 1))
        vs_barnet2 = _sf(self.barnet2, self.aardvark1, date(2025, 9, 1))
        later = _sf(self.aardvark1, self.barnet1, date(2025, 9, 20))

        ordered = reportdata.by_date_opponent(
            self.aardvark1, [later, vs_barnet2, vs_nick], self.clubs
        )
        # Same date: opponent "Aardvark Nomads" before "Barnet 2"; later date last.
        self.assertEqual(ordered, [vs_nick, vs_barnet2, later])

    def test_by_home_away_orders_without_dates(self) -> None:
        f1 = fmodel.Fixture(home_team=self.barnet1, away_team=self.aardvark1)
        f2 = fmodel.Fixture(home_team=self.aardvark1, away_team=self.barnet1)

        ordered = reportdata.by_home_away([f1, f2], self.clubs, with_division=False)
        self.assertEqual(ordered, [f2, f1])

    def test_club_date_counts_splits_home_away_and_total(self) -> None:
        fixtures = [
            _sf(self.barnet1, self.aardvark1, date(2025, 9, 1)),
            _sf(self.barnet2, self.aardvark1, date(2025, 9, 15)),
            _sf(self.aardvark1, self.barnet1, date(2025, 10, 6)),
        ]
        self.assertEqual(
            reportdata.club_date_counts("a-club", fixtures),
            reportdata.ClubDateCounts(total=3, home=2, away=1),
        )

    def test_club_date_counts_dedupes_dates_and_ignores_other_clubs(self) -> None:
        fixtures = [
            # Two Barnet teams both at home the same night: one home date, not two.
            _sf(self.barnet1, self.aardvark1, date(2025, 9, 1)),
            _sf(self.barnet2, self.aardvark1, date(2025, 9, 1)),
            # An internal derby: same date counts as both a home and an away date.
            _sf(self.barnet1, self.barnet2, date(2025, 9, 8)),
            # Not involving a-club at all.
            _sf(self.aardvark1, self.nick, date(2025, 9, 22)),
        ]
        self.assertEqual(
            reportdata.club_date_counts("a-club", fixtures),
            reportdata.ClubDateCounts(total=2, home=2, away=1),
        )

    def test_club_date_counts_zero_when_club_absent(self) -> None:
        fixtures = [_sf(self.aardvark1, self.nick, date(2025, 9, 1))]
        self.assertEqual(
            reportdata.club_date_counts("a-club", fixtures),
            reportdata.ClubDateCounts(total=0, home=0, away=0),
        )


if __name__ == "__main__":
    unittest.main()
