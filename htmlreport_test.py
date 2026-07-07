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

"""Test cases for HTML report generation."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

import fmodel
import htmlreport


def _sf(home: fmodel.Team, away: fmodel.Team, d: date) -> fmodel.ScheduledFixture:
    return fmodel.ScheduledFixture(
        fixture=fmodel.Fixture(home_team=home, away_team=away), date=d
    )


def _club(
    name: str, venue: str = "Venue", start: str = "19:30", limit: str = "75+15"
) -> fmodel.Club:
    return fmodel.Club(
        name=name, home_venue=venue, home_start_time=start, home_time_limit=limit
    )


class TestSlugify(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(htmlreport.slugify("Albany"), "albany")

    def test_spaces_and_punctuation(self):
        self.assertEqual(htmlreport.slugify("Willesden & Brent"), "willesden-brent")

    def test_leading_trailing_punctuation(self):
        self.assertEqual(htmlreport.slugify("  Kings Head!!"), "kings-head")

    def test_empty(self):
        self.assertEqual(htmlreport.slugify("---"), "unnamed")


class TestGenerateReport(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.output_dir = Path(self._tmpdir.name) / "out"

        self.clubs = {
            "harrow": _club(
                "Harrow", venue="Harrow Leisure Centre", start="19:30", limit="75+15"
            ),
            "ealing": _club(
                "Ealing", venue="Ealing Sports Hall", start="19:00", limit="60+15"
            ),
            "hendon": _club(
                "Hendon", venue="Hendon Club", start="20:00", limit="90+30"
            ),
            "willesden-brent": _club("Willesden & Brent"),
        }

        self.harrow1 = fmodel.Team(division=1, club="harrow", index=1)
        self.harrow2 = fmodel.Team(division=1, club="harrow", index=2)
        self.ealing1 = fmodel.Team(division=1, club="ealing", index=1)
        self.hendon1 = fmodel.Team(division=2, club="hendon", index=1)
        self.willesden1 = fmodel.Team(
            division=2,
            club="willesden-brent",
            index=1,
            name_override="Willesden Warriors",
        )

        self.teams = [
            self.harrow1,
            self.harrow2,
            self.ealing1,
            self.hendon1,
            self.willesden1,
        ]
        self.fixtures = [
            _sf(self.harrow1, self.ealing1, date(2025, 9, 1)),
            _sf(self.ealing1, self.harrow1, date(2025, 9, 8)),
            _sf(self.harrow1, self.harrow2, date(2025, 9, 15)),
            _sf(self.hendon1, self.willesden1, date(2025, 9, 3)),
        ]

        self.index_path = htmlreport.generate_report(
            self.fixtures, self.teams, self.clubs, self.output_dir
        )

    def test_returns_index_path(self):
        self.assertEqual(self.index_path, self.output_dir / "index.html")
        self.assertTrue(self.index_path.exists())

    def test_all_matches_page(self):
        content = (self.output_dir / "all-matches.html").read_text()
        self.assertIn("Harrow 1", content)
        self.assertIn("Ealing 1", content)
        self.assertIn("Hendon 1", content)
        # 4 fixtures -> 4 data rows (plus header row)
        self.assertEqual(content.count("<tr>"), 5)

    def test_match_annotated_with_venue_start_and_time_limit(self):
        content = (self.output_dir / "all-matches.html").read_text()
        self.assertIn("<th>Venue</th>", content)
        self.assertIn("<th>Start</th>", content)
        self.assertIn("<th>Time Limit</th>", content)
        self.assertIn("Harrow Leisure Centre", content)
        self.assertIn("19:30", content)
        self.assertIn("75+15", content)

    def test_name_override_used_throughout(self):
        content = (self.output_dir / "all-matches.html").read_text()
        self.assertIn("Willesden Warriors", content)
        self.assertNotIn("Willesden &amp; Brent 1", content)

    def test_division_pages(self):
        div1 = (self.output_dir / "division-1.html").read_text()
        self.assertIn("Harrow 1", div1)
        self.assertNotIn("Hendon 1", div1)
        # Division column should not appear in per-division tables
        self.assertNotIn("<th>Division</th>", div1)
        self.assertIn("<th>Venue</th>", div1)

        div2 = (self.output_dir / "division-2.html").read_text()
        self.assertIn("Hendon 1", div2)
        self.assertIn("Willesden Warriors", div2)

    def test_club_page_consolidated_and_per_team(self):
        harrow_page = (self.output_dir / "club-harrow.html").read_text()
        # 3 distinct fixtures touch Harrow (incl. the Harrow 1 v Harrow 2 derby); the
        # consolidated table must list each exactly once, not twice for the derby.
        consolidated_table = harrow_page.split("<h2>")[0]
        self.assertEqual(consolidated_table.count("<tr>"), 4)  # header + 3 fixtures
        self.assertIn("<h2>Harrow 1</h2>", harrow_page)
        self.assertIn("<h2>Harrow 2</h2>", harrow_page)
        # Harrow 1's own table should list all 3 of its fixtures (2 external + the derby)
        harrow1_section = harrow_page.split("<h2>Harrow 1</h2>")[1].split("<h2>")[0]
        self.assertEqual(harrow1_section.count("<tr>"), 4)  # header + 3 fixtures

    def test_ampersand_club_name_slugified_and_escaped(self):
        path = self.output_dir / "club-willesden-brent.html"
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("Willesden &amp; Brent", content)

    def test_run_index_links_to_all_pages(self):
        content = self.index_path.read_text()
        self.assertIn("all-matches.html", content)
        self.assertIn("division-1.html", content)
        self.assertIn("division-2.html", content)
        self.assertIn("club-harrow.html", content)
        self.assertIn("club-willesden-brent.html", content)
        # Titles recovered from the linked pages, not raw filenames
        self.assertIn(">Division 1<", content)
        self.assertIn(">Willesden &amp; Brent<", content)

    def test_build_run_index_is_rebuildable_from_disk_alone(self):
        """Deleting index.html and rebuilding from the other report files must reproduce it."""
        self.index_path.unlink()
        rebuilt_path = htmlreport.build_run_index(self.output_dir)
        self.assertEqual(rebuilt_path, self.index_path)
        content = self.index_path.read_text()
        self.assertIn("division-1.html", content)
        self.assertIn("club-harrow.html", content)

    def test_division_numbers_sort_numerically_not_lexically(self):
        out2 = Path(self._tmpdir.name) / "out3"
        out2.mkdir()
        for n in [1, 2, 10]:
            (out2 / f"division-{n}.html").write_text(
                htmlreport._page(f"Division {n}", "")
            )
        (out2 / "all-matches.html").write_text(htmlreport._page("All matches", ""))
        content = htmlreport.build_run_index(out2).read_text()
        self.assertLess(
            content.index("division-1.html"), content.index("division-2.html")
        )
        self.assertLess(
            content.index("division-2.html"), content.index("division-10.html")
        )

    def test_team_with_no_fixtures_gets_empty_table(self):
        lonely_clubs = {"lonely-fc": _club("Lonely FC")}
        lonely = fmodel.Team(division=3, club="lonely-fc", index=1)
        out2 = Path(self._tmpdir.name) / "out2"
        htmlreport.generate_report([], [lonely], lonely_clubs, out2)
        content = (out2 / "club-lonely-fc.html").read_text()
        self.assertIn("No matches", content)


class TestWriteRunsIndex(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.runs_dir = self.root / "runs"
        self.index_path = self.root / "index.html"

    def test_no_runs(self):
        htmlreport.write_runs_index(self.runs_dir, self.index_path)
        content = self.index_path.read_text()
        self.assertIn("No runs yet", content)

    def test_lists_runs_with_report_only(self):
        for name in ["2024-25-season", "2025-26-season"]:
            run_dir = self.runs_dir / name
            run_dir.mkdir(parents=True)
            (run_dir / "all-matches.html").write_text("<html></html>")
        # A directory without a report (e.g. an in-progress run) should be ignored.
        (self.runs_dir / "incomplete-run").mkdir()

        htmlreport.write_runs_index(self.runs_dir, self.index_path)
        content = self.index_path.read_text()

        self.assertIn("runs/2024-25-season/index.html", content)
        self.assertIn("runs/2025-26-season/index.html", content)
        self.assertNotIn("incomplete-run", content)

        # Most recent (reverse alphabetical) first
        self.assertLess(
            content.index("2025-26-season"), content.index("2024-25-season")
        )


if __name__ == "__main__":
    unittest.main()
