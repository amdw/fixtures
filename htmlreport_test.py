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
from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path

import fixturespec
import fmodel
import htmlreport


def _sf(home: fmodel.Team, away: fmodel.Team, d: date) -> fmodel.ScheduledFixture:
    return fmodel.ScheduledFixture(
        fixture=fmodel.Fixture(home_team=home, away_team=away), date=d
    )


def _generate(
    result: fmodel.SolveResult,
    teams: Collection[fmodel.Team],
    clubs: Mapping[str, fmodel.Club],
    output_dir: Path,
    *,
    excluded_fixtures: Collection[fmodel.Fixture] = (),
    name: str = "",
    draft: bool = False,
    description: str = "",
    division_schemes: Mapping[int, fmodel.FixtureScheme] | None = None,
) -> Path:
    """Assemble a minimal fixturespec.Spec from the loose pieces these tests work
    with and render `result` as its HTML report, so the tests need not build a
    full fmodel.Parameters just to exercise htmlreport.generate_report()."""
    parameters = fmodel.Parameters(
        teams=list(teams),
        home_dates={},
        unavailable_away_dates={},
        excluded_fixtures=excluded_fixtures,
        division_schemes=division_schemes or {},
    )
    spec = fixturespec.Spec(
        parameters=parameters,
        clubs=clubs,
        name=name,
        draft=draft,
        description=description,
    )
    return htmlreport.generate_report(spec, result, output_dir)


def _club(
    name: str,
    venue: str = "Venue",
    address: str = "1 Venue Street, London",
    start: str = "19:30",
    limit: str = "75+15",
) -> fmodel.Club:
    return fmodel.Club(
        name=name,
        home_venue_name=venue,
        home_venue_address=address,
        home_start_time=start,
        home_time_limit=limit,
    )


class TestGenerateReport(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.output_dir = Path(self._tmpdir.name) / "out"

        self.clubs = {
            "harrow": _club(
                "Harrow",
                venue="Harrow Leisure Centre",
                address="1 Harrow Road, London",
                start="19:30",
                limit="75+15",
            ),
            "ealing": _club(
                "Ealing",
                venue="Ealing Sports Hall",
                address="2 Ealing Road, London",
                start="19:00",
                limit="60+15",
            ),
            "hendon": _club(
                "Hendon",
                venue="Hendon Club",
                address="3 Hendon Road, London",
                start="20:00",
                limit="90+30",
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

        self.index_path = _generate(
            fmodel.SolveResult(self.fixtures), self.teams, self.clubs, self.output_dir
        )

    def test_returns_index_path(self) -> None:
        self.assertEqual(self.index_path, self.output_dir / "index.html")
        self.assertTrue(self.index_path.exists())

    def test_all_matches_page(self) -> None:
        content = (self.output_dir / "all-matches.html").read_text()
        self.assertIn("Harrow 1", content)
        self.assertIn("Ealing 1", content)
        self.assertIn("Hendon 1", content)
        # 4 fixtures -> 4 data rows (plus header row)
        self.assertEqual(content.count("<tr>"), 5)

    def test_all_matches_page_shows_total_match_count(self) -> None:
        content = (self.output_dir / "all-matches.html").read_text()
        table_part = content.split("<h2>Venues</h2>")[0]
        self.assertIn(
            '<p class="match-count">Total matches: <strong>4</strong></p>', table_part
        )

    def test_division_page_shows_total_match_count(self) -> None:
        # Division 1 has the 3 Harrow/Ealing fixtures.
        content = (self.output_dir / "division-1.html").read_text()
        self.assertIn(
            '<p class="match-count">Total matches: <strong>3</strong></p>', content
        )

    def test_club_consolidated_table_shows_total_match_count(self) -> None:
        harrow_page = (self.output_dir / "club-harrow.html").read_text()
        consolidated = harrow_page.split("<h2")[0]
        self.assertIn(
            '<p class="match-count">Total matches: <strong>3</strong></p>', consolidated
        )

    def test_empty_table_has_no_match_count_line(self) -> None:
        lonely_clubs = {"lonely-fc": _club("Lonely FC")}
        lonely = fmodel.Team(division=3, club="lonely-fc", index=1)
        out2 = Path(self._tmpdir.name) / "out-empty"
        _generate(fmodel.SolveResult([]), [lonely], lonely_clubs, out2)
        content = (out2 / "club-lonely-fc.html").read_text()
        self.assertIn("No matches", content)
        self.assertNotIn('<p class="match-count">', content)

    def test_match_annotated_with_venue_start_and_time_limit(self) -> None:
        content = (self.output_dir / "all-matches.html").read_text()
        self.assertIn("<th>Venue</th>", content)
        self.assertIn("<th>Start</th>", content)
        self.assertIn("<th>Time Limit</th>", content)
        self.assertIn("Harrow Leisure Centre", content)
        self.assertIn("19:30", content)
        self.assertIn("75+15", content)

    def test_match_table_shows_venue_name_but_not_address(self) -> None:
        content = (self.output_dir / "all-matches.html").read_text()
        table_part = content.split("<h2>Venues</h2>")[0]
        self.assertIn("Harrow Leisure Centre", table_part)
        self.assertNotIn("1 Harrow Road, London", table_part)

    def test_all_matches_venues_section_lists_name_and_address(self) -> None:
        content = (self.output_dir / "all-matches.html").read_text()
        self.assertIn("<h2>Venues</h2>", content)
        venues_part = content.split("<h2>Venues</h2>")[1]
        self.assertIn("Harrow Leisure Centre", venues_part)
        self.assertIn("1 Harrow Road, London", venues_part)
        self.assertIn("Ealing Sports Hall", venues_part)
        self.assertIn("2 Ealing Road, London", venues_part)
        self.assertIn("Hendon Club", venues_part)

    def test_division_venues_section_only_lists_that_divisions_clubs(self) -> None:
        div1 = (self.output_dir / "division-1.html").read_text()
        venues_part = div1.split("<h2>Venues</h2>")[1]
        self.assertIn("Harrow Leisure Centre", venues_part)
        self.assertIn("Ealing Sports Hall", venues_part)
        self.assertNotIn("Hendon Club", venues_part)

    def test_name_override_used_throughout(self) -> None:
        content = (self.output_dir / "all-matches.html").read_text()
        self.assertIn("Willesden Warriors", content)
        self.assertNotIn("Willesden &amp; Brent 1", content)

    def test_division_pages(self) -> None:
        div1 = (self.output_dir / "division-1.html").read_text()
        self.assertIn("Harrow 1", div1)
        self.assertNotIn("Hendon 1", div1)
        # Division column should not appear in per-division tables
        self.assertNotIn("<th>Division</th>", div1)
        self.assertIn("<th>Venue</th>", div1)

        div2 = (self.output_dir / "division-2.html").read_text()
        self.assertIn("Hendon 1", div2)
        self.assertIn("Willesden Warriors", div2)

    def test_division_page_names_the_fixture_scheme_in_a_subheading(self) -> None:
        # No division_schemes passed -> every division defaults to a double round.
        div1 = (self.output_dir / "division-1.html").read_text()
        self.assertIn("<h2>Double-round all-play-all</h2>", div1)
        self.assertNotIn("Single-round", div1)

    def test_club_per_team_heading_names_the_fixture_scheme_in_brackets(self) -> None:
        harrow_page = (self.output_dir / "club-harrow.html").read_text()
        harrow1_section = harrow_page.split('<h2 id="harrow-1">')[1].split("<h2")[0]
        self.assertIn(
            "<h3>Division 1 (Double-round all-play-all)</h3>", harrow1_section
        )
        # The consolidated table above the per-team sections spans divisions, so it
        # names no scheme.
        consolidated = harrow_page.split("<h2")[0]
        self.assertNotIn("all-play-all", consolidated)

    def test_single_round_division_scheme_shown_on_its_pages_only(self) -> None:
        out2 = Path(self._tmpdir.name) / "out-scheme"
        _generate(
            fmodel.SolveResult(self.fixtures),
            self.teams,
            self.clubs,
            out2,
            division_schemes={2: fmodel.FixtureScheme.SINGLE_ROUND},
        )
        div2 = (out2 / "division-2.html").read_text()
        self.assertIn("<h2>Single-round all-play-all</h2>", div2)
        # Division 1 has no entry, so it stays a double round.
        div1 = (out2 / "division-1.html").read_text()
        self.assertIn("<h2>Double-round all-play-all</h2>", div1)
        # Hendon 1 is in division 2, so its per-team heading reflects single round.
        hendon_page = (out2 / "club-hendon.html").read_text()
        hendon1_section = hendon_page.split('<h2 id="hendon-1">')[1]
        self.assertIn(
            "<h3>Division 2 (Single-round all-play-all)</h3>", hendon1_section
        )

    def test_club_page_consolidated_and_per_team(self) -> None:
        harrow_page = (self.output_dir / "club-harrow.html").read_text()
        # 3 distinct fixtures touch Harrow (incl. the Harrow 1 v Harrow 2 derby); the
        # consolidated table must list each exactly once, not twice for the derby.
        consolidated_table = harrow_page.split("<h2")[0]
        self.assertEqual(consolidated_table.count("<tr>"), 4)  # header + 3 fixtures
        self.assertIn('<h2 id="harrow-1">Harrow 1', harrow_page)
        self.assertIn('<h2 id="harrow-2">Harrow 2', harrow_page)
        # Harrow 1's own table should list all 3 of its fixtures (2 external + the derby)
        harrow1_section = harrow_page.split('<h2 id="harrow-1">')[1].split("<h2")[0]
        self.assertEqual(harrow1_section.count("<tr>"), 4)  # header + 3 fixtures

    def test_team_heading_has_stable_anchor_id(self) -> None:
        harrow_page = (self.output_dir / "club-harrow.html").read_text()
        self.assertIn('<h2 id="harrow-1">', harrow_page)
        self.assertIn('<h2 id="harrow-2">', harrow_page)
        willesden_page = (self.output_dir / "club-willesden-brent.html").read_text()
        # A name_override should be slugified for the anchor, same as everywhere else.
        self.assertIn('<h2 id="willesden-warriors">', willesden_page)

    def test_team_heading_has_self_link_icon_pointing_at_its_own_anchor(self) -> None:
        harrow_page = (self.output_dir / "club-harrow.html").read_text()
        self.assertIn(
            '<a class="anchor-link" href="#harrow-1" aria-label="Link to Harrow 1">',
            harrow_page,
        )

    def test_club_page_header_shows_venue_name_and_address(self) -> None:
        harrow_page = (self.output_dir / "club-harrow.html").read_text()
        header_part = harrow_page.split("<table>")[0]
        self.assertIn("Harrow Leisure Centre", header_part)
        self.assertIn("1 Harrow Road, London", header_part)

    def test_club_page_shows_distinct_date_counts_under_consolidated_table(
        self,
    ) -> None:
        harrow_page = (self.output_dir / "club-harrow.html").read_text()
        # Harrow: home 1 Sep + 15 Sep (derby); away 8 Sep + 15 Sep (derby).
        # Distinct overall: 1, 8, 15 Sep -> 3 total, 2 home, 2 away.
        summary = harrow_page.split("</table>")[1].split("<h2")[0]
        self.assertIn('<p class="date-summary">', summary)
        self.assertIn("3</strong> total", summary)
        self.assertIn("2</strong> home", summary)
        self.assertIn("2</strong> away", summary)

    def test_ampersand_club_name_slugified_and_escaped(self) -> None:
        path = self.output_dir / "club-willesden-brent.html"
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("Willesden &amp; Brent", content)

    def test_run_index_links_to_all_pages(self) -> None:
        content = self.index_path.read_text()
        self.assertIn("all-matches.html", content)
        self.assertIn("division-1.html", content)
        self.assertIn("division-2.html", content)
        self.assertIn("club-harrow.html", content)
        self.assertIn("club-willesden-brent.html", content)
        # Link labels are the pages' own titles, not raw filenames
        self.assertIn(">Division 1<", content)
        self.assertIn(">Willesden &amp; Brent<", content)

    def test_run_index_links_to_csv_exports_when_present(self) -> None:
        # generate_report links whichever CSV exports it finds already written
        # into the output dir (csvreport.generate_csv writes them in production).
        for name in ("all-matches.csv", "all-matches-by-team.csv"):
            (self.output_dir / name).write_text("date\n")

        content = _generate(
            fmodel.SolveResult(self.fixtures), self.teams, self.clubs, self.output_dir
        ).read_text()

        self.assertIn('href="all-matches.csv"', content)
        self.assertIn('href="all-matches-by-team.csv"', content)
        # Grouped under the "All matches" heading, ahead of the divisions list.
        self.assertLess(
            content.index("all-matches.csv"), content.index("<h2>Divisions</h2>")
        )

    def test_run_index_has_no_csv_links_when_exports_absent(self) -> None:
        # setUp's _generate() writes HTML pages only, no CSV files.
        self.assertNotIn(".csv", self.index_path.read_text())

    def test_run_index_shows_solver_diagnostics_when_stats_given(self) -> None:
        content = _generate(
            fmodel.SolveResult(
                self.fixtures,
                model_stats="satisfaction model '':\n#Variables: 42\n",
                solve_stats="CpSolverResponse summary:\nstatus: OPTIMAL\n",
            ),
            self.teams,
            self.clubs,
            self.output_dir,
        ).read_text()

        self.assertIn(
            '<details class="solver-diagnostics">\n'
            "<summary>Solver diagnostics</summary>",
            content,
        )
        self.assertIn("<h3>Model</h3>\n<pre>satisfaction model", content)
        self.assertIn("<h3>Solve</h3>\n<pre>CpSolverResponse summary", content)
        # Diagnostics sit below the run's navigation lists.
        self.assertLess(
            content.index("<h2>Clubs</h2>"),
            content.index('<details class="solver-diagnostics">'),
        )

    def test_run_index_escapes_solver_diagnostics_text(self) -> None:
        content = _generate(
            fmodel.SolveResult(self.fixtures, model_stats="a <b> & c\n"),
            self.teams,
            self.clubs,
            self.output_dir,
        ).read_text()
        self.assertIn("a &lt;b&gt; &amp; c", content)

    def test_run_index_omits_a_missing_diagnostics_half(self) -> None:
        content = _generate(
            fmodel.SolveResult(self.fixtures, solve_stats="status: OPTIMAL\n"),
            self.teams,
            self.clubs,
            self.output_dir,
        ).read_text()
        self.assertIn("<summary>Solver diagnostics</summary>", content)
        self.assertIn("<h3>Solve</h3>", content)
        self.assertNotIn("<h3>Model</h3>", content)

    def test_run_index_has_no_solver_diagnostics_by_default(self) -> None:
        self.assertNotIn(
            '<details class="solver-diagnostics">', self.index_path.read_text()
        )

    def test_club_page_links_per_club_and_per_team_csv_when_present(self) -> None:
        # generate_report links the per-club / per-team exports it finds already
        # written into the output dir (csvreport.generate_csv writes them).
        for name in ("club-harrow-dates.csv", "team-harrow-1.csv", "team-harrow-2.csv"):
            (self.output_dir / name).write_text("date\n")

        _generate(
            fmodel.SolveResult(self.fixtures), self.teams, self.clubs, self.output_dir
        )
        harrow_page = (self.output_dir / "club-harrow.html").read_text()

        # Per-club link sits under the consolidated table, ahead of the first team.
        self.assertIn(
            '<p class="export-link"><a href="club-harrow-dates.csv">', harrow_page
        )
        self.assertLess(
            harrow_page.index("club-harrow-dates.csv"),
            harrow_page.index('<h2 id="harrow-1">'),
        )
        # Each team section links its own file, under that team's table.
        h1_section = harrow_page.split('<h2 id="harrow-1">')[1].split("<h2")[0]
        self.assertIn(
            '<p class="export-link"><a href="team-harrow-1.csv">Download CSV</a></p>',
            h1_section,
        )
        self.assertLess(
            h1_section.index("</table>"), h1_section.index("team-harrow-1.csv")
        )
        self.assertNotIn("team-harrow-2.csv", h1_section)

    def test_club_page_has_no_csv_links_when_exports_absent(self) -> None:
        # setUp's _generate() writes HTML pages only, no CSV files.
        self.assertNotIn(".csv", (self.output_dir / "club-harrow.html").read_text())

    def test_division_numbers_sort_numerically_not_lexically(self) -> None:
        clubs = {"c": _club("C"), "d": _club("D")}
        teams: list[fmodel.Team] = []
        fixtures: list[fmodel.ScheduledFixture] = []
        for n in (1, 2, 10):
            home = fmodel.Team(division=n, club="c", index=1)
            away = fmodel.Team(division=n, club="d", index=1)
            teams += [home, away]
            fixtures.append(_sf(home, away, date(2025, 9, 1)))

        out2 = Path(self._tmpdir.name) / "out-div-sort"
        content = _generate(
            fmodel.SolveResult(fixtures), teams, clubs, out2
        ).read_text()

        self.assertLess(
            content.index("division-1.html"), content.index("division-2.html")
        )
        self.assertLess(
            content.index("division-2.html"), content.index("division-10.html")
        )

    def test_team_with_no_fixtures_gets_empty_table(self) -> None:
        lonely_clubs = {"lonely-fc": _club("Lonely FC")}
        lonely = fmodel.Team(division=3, club="lonely-fc", index=1)
        out2 = Path(self._tmpdir.name) / "out2"
        _generate(fmodel.SolveResult([]), [lonely], lonely_clubs, out2)
        content = (out2 / "club-lonely-fc.html").read_text()
        self.assertIn("No matches", content)

    def test_no_name_or_draft_by_default(self) -> None:
        for filename in ["all-matches.html", "division-1.html", "club-harrow.html"]:
            content = (self.output_dir / filename).read_text()
            self.assertNotIn('class="banner"', content)
            self.assertNotIn('class="draft-label"', content)
        self.assertNotIn('class="banner"', self.index_path.read_text())

    def test_no_description_by_default(self) -> None:
        for filename in ["all-matches.html", "division-1.html", "club-harrow.html"]:
            content = (self.output_dir / filename).read_text()
            self.assertNotIn('class="description"', content)
        self.assertNotIn('class="description"', self.index_path.read_text())

    def test_run_name_and_draft_shown_on_every_page(self) -> None:
        out2 = Path(self._tmpdir.name) / "out-named"
        index_path = _generate(
            fmodel.SolveResult(self.fixtures),
            self.teams,
            self.clubs,
            out2,
            name="2025-26 Season",
            draft=True,
        )
        for filename in [
            "all-matches.html",
            "division-1.html",
            "club-harrow.html",
            index_path.name,
        ]:
            content = (out2 / filename).read_text()
            self.assertIn('<div class="banner draft">', content)
            self.assertIn('<span class="draft-label">DRAFT</span>', content)
            self.assertIn('<span class="run-name">2025-26 Season</span>', content)

    def test_description_shown_on_every_page(self) -> None:
        out2 = Path(self._tmpdir.name) / "out-described"
        index_path = _generate(
            fmodel.SolveResult(self.fixtures),
            self.teams,
            self.clubs,
            out2,
            description="Final schedule; refer to ECF LMS for authoritative dates.",
        )
        for filename in [
            "all-matches.html",
            "division-1.html",
            "club-harrow.html",
            index_path.name,
        ]:
            content = (out2 / filename).read_text()
            self.assertIn('<p class="description">', content)
            self.assertIn("Final schedule", content)

    def test_head_title_is_bare_page_title_without_a_run_name(self) -> None:
        # setUp generates with no run name and draft=False.
        self.assertIn(
            "<title>All matches</title>",
            (self.output_dir / "all-matches.html").read_text(),
        )
        self.assertIn(
            "<title>Division 1</title>",
            (self.output_dir / "division-1.html").read_text(),
        )
        self.assertIn(
            "<title>Harrow</title>",
            (self.output_dir / "club-harrow.html").read_text(),
        )
        self.assertIn("<title>Fixtures</title>", self.index_path.read_text())

    def test_head_title_carries_run_name_club_or_division_and_draft(self) -> None:
        out2 = Path(self._tmpdir.name) / "out-head-title"
        index_path = _generate(
            fmodel.SolveResult(self.fixtures),
            self.teams,
            self.clubs,
            out2,
            name="2025-26 Season",
            draft=True,
        )
        self.assertIn("<title>2025-26 Season (DRAFT)</title>", index_path.read_text())
        self.assertIn(
            "<title>2025-26 Season – All matches (DRAFT)</title>",
            (out2 / "all-matches.html").read_text(),
        )
        self.assertIn(
            "<title>2025-26 Season – Division 1 (DRAFT)</title>",
            (out2 / "division-1.html").read_text(),
        )
        self.assertIn(
            "<title>2025-26 Season – Harrow (DRAFT)</title>",
            (out2 / "club-harrow.html").read_text(),
        )

    def test_head_title_omits_draft_marker_for_non_draft_runs(self) -> None:
        out2 = Path(self._tmpdir.name) / "out-head-title-final"
        index_path = _generate(
            fmodel.SolveResult(self.fixtures),
            self.teams,
            self.clubs,
            out2,
            name="2025-26 Season",
        )
        self.assertIn("<title>2025-26 Season</title>", index_path.read_text())
        self.assertIn(
            "<title>2025-26 Season – Division 1</title>",
            (out2 / "division-1.html").read_text(),
        )

    def test_nav_link_labels_stay_bare_despite_richer_head_titles(self) -> None:
        out2 = Path(self._tmpdir.name) / "out-nav-labels"
        index_path = _generate(
            fmodel.SolveResult(self.fixtures),
            self.teams,
            self.clubs,
            out2,
            name="2025-26 Season",
            draft=True,
        )
        content = index_path.read_text()
        self.assertIn(">All matches</a>", content)
        self.assertIn(">Division 1</a>", content)
        self.assertNotIn("2025-26 Season – Division 1</a>", content)


class TestExcludedFixturesInReport(unittest.TestCase):
    def setUp(self) -> None:
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
        }
        self.harrow1 = fmodel.Team(division=1, club="harrow", index=1)
        self.harrow2 = fmodel.Team(division=1, club="harrow", index=2)
        self.ealing1 = fmodel.Team(division=1, club="ealing", index=1)
        self.teams = [self.harrow1, self.harrow2, self.ealing1]

        self.fixtures = [_sf(self.harrow1, self.ealing1, date(2025, 9, 1))]
        self.excluded = [fmodel.Fixture(home_team=self.harrow2, away_team=self.ealing1)]

        self.index_path = _generate(
            fmodel.SolveResult(self.fixtures),
            self.teams,
            self.clubs,
            self.output_dir,
            excluded_fixtures=self.excluded,
        )

    def test_all_matches_page_shows_tbc_row_after_dated_rows(self) -> None:
        content = (self.output_dir / "all-matches.html").read_text()
        self.assertIn("<td><strong>TBC</strong></td>", content)
        self.assertIn("Harrow 2", content)
        self.assertLess(content.index("2025"), content.index("TBC"))

    def test_division_page_shows_excluded_fixture(self) -> None:
        div1 = (self.output_dir / "division-1.html").read_text()
        self.assertIn("<td><strong>TBC</strong></td>", div1)
        self.assertIn("Harrow 2", div1)

    def test_club_page_consolidated_shows_excluded_fixture(self) -> None:
        harrow_page = (self.output_dir / "club-harrow.html").read_text()
        consolidated_table = harrow_page.split("<h2")[0]
        self.assertIn("TBC", consolidated_table)
        ealing_page = (self.output_dir / "club-ealing.html").read_text()
        consolidated_table = ealing_page.split("<h2")[0]
        self.assertIn("TBC", consolidated_table)

    def test_team_page_shows_excluded_fixture_with_blank_days_since(self) -> None:
        harrow_page = (self.output_dir / "club-harrow.html").read_text()
        harrow2_section = harrow_page.split('<h2 id="harrow-2">')[1].split("<h2")[0]
        expected_row = (
            "<tr><td><strong>TBC</strong></td><td>Ealing 1</td><td>Home</td>"
            "<td>Harrow Leisure Centre</td><td>19:30</td><td>75+15</td><td></td></tr>"
        )
        self.assertIn(expected_row, harrow2_section)

    def test_team_not_involved_in_excluded_fixture_unaffected(self) -> None:
        harrow_page = (self.output_dir / "club-harrow.html").read_text()
        harrow1_section = harrow_page.split('<h2 id="harrow-1">')[1].split("<h2")[0]
        self.assertNotIn("TBC", harrow1_section)

    def test_no_excluded_fixtures_by_default(self) -> None:
        out2 = Path(self._tmpdir.name) / "out-no-excluded"
        _generate(fmodel.SolveResult(self.fixtures), self.teams, self.clubs, out2)
        content = (out2 / "all-matches.html").read_text()
        self.assertNotIn("TBC", content)

    def test_division_with_only_excluded_fixtures_still_gets_a_page(self) -> None:
        clubs = dict(self.clubs)
        clubs["hendon"] = _club("Hendon")
        clubs["wembley"] = _club("Wembley")
        hendon1 = fmodel.Team(division=2, club="hendon", index=1)
        wembley1 = fmodel.Team(division=2, club="wembley", index=1)
        teams = [*self.teams, hendon1, wembley1]
        excluded = [
            *self.excluded,
            fmodel.Fixture(home_team=hendon1, away_team=wembley1),
        ]

        out2 = Path(self._tmpdir.name) / "out-div2"
        _generate(
            fmodel.SolveResult(self.fixtures),
            teams,
            clubs,
            out2,
            excluded_fixtures=excluded,
        )
        div2 = (out2 / "division-2.html").read_text()
        self.assertIn("TBC", div2)
        self.assertIn("Hendon 1", div2)
        self.assertIn("Wembley 1", div2)


class TestWriteRunsIndex(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.runs_dir = self.root / "runs"
        self.index_path = self.root / "index.html"

    def test_no_runs(self) -> None:
        htmlreport.write_runs_index(self.runs_dir, self.index_path)
        content = self.index_path.read_text()
        self.assertIn("No runs yet", content)

    def test_head_title_of_top_level_index(self) -> None:
        htmlreport.write_runs_index(self.runs_dir, self.index_path)
        self.assertIn("<title>Fixture runs</title>", self.index_path.read_text())

    def test_lists_runs_with_report_only(self) -> None:
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

    def test_nested_run(self) -> None:
        """A run need not sit directly under runs_dir -- e.g. several drafts of the
        same season grouped under a season folder -- and should still be found and
        linked correctly, nested under a heading for the grouping folder(s)."""
        run_dir = self.runs_dir / "2026-27" / "draft1"
        run_dir.mkdir(parents=True)
        (run_dir / "all-matches.html").write_text("<html></html>")

        htmlreport.write_runs_index(self.runs_dir, self.index_path)
        content = self.index_path.read_text()

        self.assertIn("runs/2026-27/draft1/index.html", content)
        # The grouping folder itself has no report, so isn't a link.
        self.assertNotIn('<a href="runs/2026-27/index.html"', content)
        self.assertIn("2026-27", content)

    def test_nested_and_flat_runs_combined(self) -> None:
        (self.runs_dir / "example").mkdir(parents=True)
        (self.runs_dir / "example" / "all-matches.html").write_text("<html></html>")
        nested_run_dir = self.runs_dir / "2026-27" / "draft1"
        nested_run_dir.mkdir(parents=True)
        (nested_run_dir / "all-matches.html").write_text("<html></html>")

        htmlreport.write_runs_index(self.runs_dir, self.index_path)
        content = self.index_path.read_text()

        self.assertIn("runs/example/index.html", content)
        self.assertIn("runs/2026-27/draft1/index.html", content)


class TestFindRunDirs(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.runs_dir = Path(self._tmpdir.name) / "runs"

    def test_missing_runs_dir(self) -> None:
        self.assertEqual(htmlreport.find_run_dirs(self.runs_dir), [])

    def test_finds_runs_at_any_depth(self) -> None:
        flat_run = self.runs_dir / "example"
        flat_run.mkdir(parents=True)
        (flat_run / "all-matches.html").write_text("<html></html>")

        nested_run = self.runs_dir / "2026-27" / "draft1"
        nested_run.mkdir(parents=True)
        (nested_run / "all-matches.html").write_text("<html></html>")

        (self.runs_dir / "incomplete-run").mkdir()

        self.assertCountEqual(
            htmlreport.find_run_dirs(self.runs_dir), [flat_run, nested_run]
        )


if __name__ == "__main__":
    unittest.main()
