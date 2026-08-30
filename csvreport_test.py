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

"""Test cases for CSV report generation."""

import csv
import tempfile
import unittest
from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path

import csvreport
import fixturespec
import fmodel


def _sf(home: fmodel.Team, away: fmodel.Team, d: date) -> fmodel.ScheduledFixture:
    return fmodel.ScheduledFixture(
        fixture=fmodel.Fixture(home_team=home, away_team=away), date=d
    )


def _generate_csv(
    fixtures: Collection[fmodel.ScheduledFixture],
    teams: Collection[fmodel.Team],
    clubs: Mapping[str, fmodel.Club],
    output_dir: Path,
    *,
    excluded_fixtures: Collection[fmodel.Fixture] = (),
) -> list[Path]:
    """Assemble a minimal fixturespec.Spec from the loose pieces these tests work
    with and render its CSV exports, so the tests need not build a full
    fmodel.Parameters just to exercise csvreport.generate_csv()."""
    parameters = fmodel.Parameters(
        teams=list(teams),
        home_dates={},
        unavailable_away_dates={},
        max_concurrent_home_matches={},
        excluded_fixtures=excluded_fixtures,
    )
    spec = fixturespec.Spec(parameters=parameters, clubs=clubs)
    return csvreport.generate_csv(spec, fixtures, output_dir)


def _club(name: str, venue: str, address: str, start: str, limit: str) -> fmodel.Club:
    return fmodel.Club(
        name=name,
        home_venue_name=venue,
        home_venue_address=address,
        home_start_time=start,
        home_time_limit=limit,
    )


class CsvReportTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.out = Path(self._tmpdir.name) / "out"

        self.clubs = {
            "harrow": _club(
                "Harrow",
                "Harrow Leisure Centre",
                "1 Harrow Road, London",
                "19:30",
                "75+15",
            ),
            "ealing": _club(
                "Ealing",
                "Ealing Sports Hall",
                "2 Ealing Road, London",
                "19:00",
                "60+15",
            ),
            "hendon": _club(
                "Hendon", "Hendon Club", "3 Hendon Road, London", "20:00", "90+30"
            ),
        }
        self.harrow1 = fmodel.Team(division=1, club="harrow", index=1)
        self.harrow2 = fmodel.Team(division=1, club="harrow", index=2)
        self.ealing1 = fmodel.Team(division=1, club="ealing", index=1)
        self.hendon1 = fmodel.Team(division=2, club="hendon", index=1)
        self.warriors = fmodel.Team(
            division=2, club="hendon", index=2, name_override="Hendon Warriors"
        )
        self.teams = [
            self.harrow1,
            self.harrow2,
            self.ealing1,
            self.hendon1,
            self.warriors,
        ]
        self.fixtures = [
            _sf(self.ealing1, self.harrow1, date(2025, 9, 8)),
            _sf(self.harrow1, self.ealing1, date(2025, 9, 1)),
            _sf(self.hendon1, self.warriors, date(2025, 9, 3)),
        ]

    def _rows(self, name: str) -> list[dict[str, str]]:
        with (self.out / name).open(newline="") as f:
            return list(csv.DictReader(f))

    def test_writes_both_files_and_returns_their_paths(self) -> None:
        paths = _generate_csv(self.fixtures, self.teams, self.clubs, self.out)
        self.assertEqual(
            paths,
            [
                self.out / "all-matches.csv",
                self.out / "all-matches-by-team.csv",
            ],
        )
        for p in paths:
            self.assertTrue(p.exists())

    def test_header_columns(self) -> None:
        _generate_csv(self.fixtures, self.teams, self.clubs, self.out)
        with (self.out / "all-matches.csv").open(newline="") as f:
            self.assertEqual(
                next(csv.reader(f)),
                [
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
                ],
            )
        with (self.out / "all-matches-by-team.csv").open(newline="") as f:
            self.assertEqual(
                next(csv.reader(f)),
                [
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
                ],
            )

    def test_all_matches_one_row_per_match_sorted_by_date(self) -> None:
        _generate_csv(self.fixtures, self.teams, self.clubs, self.out)
        rows = self._rows("all-matches.csv")

        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [r["date"] for r in rows], ["2025-09-01", "2025-09-03", "2025-09-08"]
        )
        first = rows[0]
        self.assertEqual(first["division"], "1")
        self.assertEqual(first["home_team"], "Harrow 1")
        self.assertEqual(first["home_team_club"], "Harrow")
        self.assertEqual(first["home_team_index"], "1")
        self.assertEqual(first["away_team"], "Ealing 1")
        self.assertEqual(first["away_team_club"], "Ealing")
        self.assertEqual(first["away_team_index"], "1")
        # Venue/start/limit are always the home team's club's.
        self.assertEqual(first["venue"], "Harrow Leisure Centre")
        self.assertEqual(first["venue_address"], "1 Harrow Road, London")
        self.assertEqual(first["start_time"], "19:30")
        self.assertEqual(first["time_limit"], "75+15")

    def test_all_matches_gives_name_override_plus_club_and_index(self) -> None:
        _generate_csv(self.fixtures, self.teams, self.clubs, self.out)
        hendon_row = next(
            r for r in self._rows("all-matches.csv") if r["date"] == "2025-09-03"
        )
        self.assertEqual(hendon_row["away_team"], "Hendon Warriors")
        # club name / index still identify the team behind the override name.
        self.assertEqual(hendon_row["away_team_club"], "Hendon")
        self.assertEqual(hendon_row["away_team_index"], "2")

    def test_by_team_has_two_rows_per_match_from_each_side(self) -> None:
        _generate_csv(self.fixtures, self.teams, self.clubs, self.out)
        rows = self._rows("all-matches-by-team.csv")

        # 3 matches -> 6 rows.
        self.assertEqual(len(rows), 6)

        # The 2025-09-01 Harrow 1 v Ealing 1 match shows up once for each team.
        sep1 = [r for r in rows if r["date"] == "2025-09-01"]
        self.assertEqual(len(sep1), 2)
        by_team = {r["team"]: r for r in sep1}
        self.assertEqual(by_team["Harrow 1"]["opponent"], "Ealing 1")
        self.assertEqual(by_team["Harrow 1"]["team_club"], "Harrow")
        self.assertEqual(by_team["Harrow 1"]["team_index"], "1")
        self.assertEqual(by_team["Harrow 1"]["opponent_club"], "Ealing")
        self.assertEqual(by_team["Harrow 1"]["opponent_index"], "1")
        self.assertEqual(by_team["Harrow 1"]["home_or_away"], "home")
        self.assertEqual(by_team["Ealing 1"]["opponent"], "Harrow 1")
        self.assertEqual(by_team["Ealing 1"]["home_or_away"], "away")
        # Both carry the home team's (Harrow's) venue details.
        for r in sep1:
            self.assertEqual(r["venue"], "Harrow Leisure Centre")
            self.assertEqual(r["start_time"], "19:30")

    def test_by_team_grouped_by_team_then_ordered_by_date(self) -> None:
        _generate_csv(self.fixtures, self.teams, self.clubs, self.out)
        rows = self._rows("all-matches-by-team.csv")

        # Each team's rows are contiguous, and teams come in (club, index) order.
        teams_in_order: list[str] = []
        for r in rows:
            if not teams_in_order or teams_in_order[-1] != r["team"]:
                teams_in_order.append(r["team"])
        self.assertEqual(
            teams_in_order,
            ["Ealing 1", "Harrow 1", "Hendon 1", "Hendon Warriors"],
        )

        ealing_dates = [r["date"] for r in rows if r["team"] == "Ealing 1"]
        self.assertEqual(ealing_dates, ["2025-09-01", "2025-09-08"])

    def test_excluded_fixtures_listed_with_empty_date_at_the_end(self) -> None:
        excluded = [fmodel.Fixture(home_team=self.harrow1, away_team=self.harrow2)]
        _generate_csv(
            self.fixtures,
            self.teams,
            self.clubs,
            self.out,
            excluded_fixtures=excluded,
        )

        all_rows = self._rows("all-matches.csv")
        self.assertEqual(all_rows[-1]["date"], "")
        self.assertEqual(all_rows[-1]["home_team"], "Harrow 1")
        self.assertEqual(all_rows[-1]["away_team"], "Harrow 2")
        # The scheduled rows still come first, all with real dates.
        self.assertTrue(all(r["date"] for r in all_rows[:-1]))

        team_rows = self._rows("all-matches-by-team.csv")
        harrow1_rows = [r for r in team_rows if r["team"] == "Harrow 1"]
        self.assertEqual(harrow1_rows[-1]["date"], "")
        self.assertEqual(harrow1_rows[-1]["opponent"], "Harrow 2")

    def test_empty_fixture_list_writes_header_only(self) -> None:
        _generate_csv([], [], self.clubs, self.out)

        for name in ("all-matches.csv", "all-matches-by-team.csv"):
            text = (self.out / name).read_text()
            self.assertEqual(text.splitlines()[1:], [], f"{name} has data rows")
            self.assertTrue(text.endswith("\n"))

    def test_uses_unix_line_endings(self) -> None:
        _generate_csv(self.fixtures, self.teams, self.clubs, self.out)
        raw = (self.out / "all-matches.csv").read_bytes()
        self.assertNotIn(b"\r", raw)


if __name__ == "__main__":
    unittest.main()
