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

"""Render a solved fixture list as a set of linked HTML pages.

generate_report writes each run's report pages plus that run's own index.html,
all from the spec + solved fixtures it is handed. write_runs_index then derives
the single top-level index.html from the run directories present on disk (their
folder layout is the only input it needs). build_site.py drives both during a
GitHub Pages deploy.
"""

from __future__ import annotations

import dataclasses
import html
from collections import defaultdict
from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import fmodel
import reportdata

if TYPE_CHECKING:
    import fixturespec

_STYLE = """
    body { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
           margin: 2rem; color: #1a1a1a; }
    h1 { margin-top: 0; }
    .banner { background: #eee; color: #333; font-weight: 600; text-align: center;
              padding: 0.6rem 1rem; margin-bottom: 1.5rem; font-size: 1.25rem;
              border-radius: 4px; }
    .banner.draft { background: #b00020; color: #fff; }
    .draft-label { font-weight: 800; text-transform: uppercase; letter-spacing: 0.05em;
                    margin-right: 0.5rem; }
    .table-scroll { overflow-x: auto; margin-bottom: 2rem; }
    table { border-collapse: collapse; width: 100%; max-width: 80rem; }
    th, td { border: 1px solid #ccc; padding: 0.4rem 0.8rem; text-align: left; }
    th { background: #f0f0f0; }
    tr:nth-child(even) { background: #fafafa; }
    a { color: #0645ad; }
    nav ul { list-style: none; padding: 0; }
    nav li { margin-bottom: 0.3rem; }
    nav ul ul { padding-left: 1.2rem; margin-top: 0.3rem; }
    .venue { margin-top: -0.5rem; margin-bottom: 1.5rem; color: #333; }
    .date-summary { margin-top: -1.5rem; margin-bottom: 2rem; color: #333; }
    .export-link { margin-top: -1rem; margin-bottom: 2rem; }
    ul.venues { padding-left: 1.2rem; }
    ul.venues li { margin-bottom: 0.3rem; }
    .anchor-link { margin-left: 0.4rem; font-size: 0.8em; text-decoration: none;
                    color: #888; }
    .anchor-link:hover { color: #0645ad; }

    @media (max-width: 40rem) {
        body { margin: 1rem; }
        th, td { padding: 0.4rem 0.5rem; white-space: nowrap; }
    }
"""


def _fmt_date(d: date) -> str:
    return d.strftime("%a %d %b %Y")


def _head_title(title: str, run_name: str, draft: bool, is_run_home: bool) -> str:
    """The text for the page's <title> element.

    A run's home page gets just the run name; every other page in the run gets
    the run name followed by that page's own title (a club or division name, or
    "All matches"). A trailing "(DRAFT)" is added whenever the run is a draft.
    Pages with no run name (e.g. the top-level list of runs) fall back to their
    own title alone.
    """
    if run_name and is_run_home:
        text = run_name
    elif run_name:
        text = f"{run_name} – {title}"
    else:
        text = title
    return f"{text} (DRAFT)" if draft else text


def _page(
    title: str,
    body: str,
    run_name: str = "",
    draft: bool = False,
    description: str = "",
    *,
    is_run_home: bool = False,
) -> str:
    banner_html = ""
    if run_name or draft:
        parts = []
        if draft:
            parts.append('<span class="draft-label">DRAFT</span>')
        if run_name:
            parts.append(f'<span class="run-name">{html.escape(run_name)}</span>')
        classes = "banner draft" if draft else "banner"
        banner_html = f'<div class="{classes}">{" ".join(parts)}</div>\n'
    description_html = ""
    if description:
        description_html = f'<p class="description">{html.escape(description)}</p>\n'
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(_head_title(title, run_name, draft, is_run_home))}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{banner_html}"
        f"{description_html}"
        f"<h1>{html.escape(title)}</h1>\n"
        f"{body}"
        "</body>\n"
        "</html>\n"
    )


def _table_cell(cell: str) -> str:
    # "TBC" marks an excluded fixture's not-yet-scheduled date; call it out in bold.
    escaped = html.escape(cell)
    return (
        f"<td><strong>{escaped}</strong></td>"
        if cell == "TBC"
        else f"<td>{escaped}</td>"
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head_html = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    if rows:
        body_html = "".join(
            "<tr>" + "".join(_table_cell(cell) for cell in row) + "</tr>\n"
            for row in rows
        )
    else:
        body_html = f'<tr><td colspan="{len(headers)}"><em>No matches</em></td></tr>\n'
    return (
        '<div class="table-scroll">\n'
        "<table>\n"
        f"<thead><tr>{head_html}</tr></thead>\n"
        f"<tbody>\n{body_html}</tbody>\n"
        "</table>\n"
        "</div>\n"
    )


def _rows_with_division(
    fixtures: Collection[fmodel.ScheduledFixture], clubs: Mapping[str, fmodel.Club]
) -> list[list[str]]:
    rows = []
    for sf in reportdata.by_date_home_away(fixtures, clubs, with_division=True):
        home_club = clubs[sf.fixture.home_team.club]
        rows.append(
            [
                _fmt_date(sf.date),
                str(sf.fixture.home_team.division),
                reportdata.team_name(sf.fixture.home_team, clubs),
                reportdata.team_name(sf.fixture.away_team, clubs),
                home_club.home_venue_name,
                home_club.home_start_time,
                home_club.home_time_limit,
            ]
        )
    return rows


def _rows(
    fixtures: Collection[fmodel.ScheduledFixture], clubs: Mapping[str, fmodel.Club]
) -> list[list[str]]:
    rows = []
    for sf in reportdata.by_date_home_away(fixtures, clubs, with_division=False):
        home_club = clubs[sf.fixture.home_team.club]
        rows.append(
            [
                _fmt_date(sf.date),
                reportdata.team_name(sf.fixture.home_team, clubs),
                reportdata.team_name(sf.fixture.away_team, clubs),
                home_club.home_venue_name,
                home_club.home_start_time,
                home_club.home_time_limit,
            ]
        )
    return rows


def _days_since_previous(prev_date: date | None, this_date: date) -> str:
    return "" if prev_date is None else str((this_date - prev_date).days)


def _team_rows(
    team: fmodel.Team,
    fixtures: Collection[fmodel.ScheduledFixture],
    clubs: Mapping[str, fmodel.Club],
) -> list[list[str]]:
    rows = []
    prev_date: date | None = None
    for sf in reportdata.by_date_opponent(team, fixtures, clubs):
        is_home = sf.fixture.home_team == team
        opponent = sf.fixture.away_team if is_home else sf.fixture.home_team
        home_club = clubs[sf.fixture.home_team.club]
        rows.append(
            [
                _fmt_date(sf.date),
                reportdata.team_name(opponent, clubs),
                "Home" if is_home else "Away",
                home_club.home_venue_name,
                home_club.home_start_time,
                home_club.home_time_limit,
                _days_since_previous(prev_date, sf.date),
            ]
        )
        prev_date = sf.date
    return rows


def _excluded_rows_with_division(
    fixtures: Collection[fmodel.Fixture], clubs: Mapping[str, fmodel.Club]
) -> list[list[str]]:
    rows = []
    for f in reportdata.by_home_away(fixtures, clubs, with_division=True):
        home_club = clubs[f.home_team.club]
        rows.append(
            [
                "TBC",
                str(f.home_team.division),
                reportdata.team_name(f.home_team, clubs),
                reportdata.team_name(f.away_team, clubs),
                home_club.home_venue_name,
                home_club.home_start_time,
                home_club.home_time_limit,
            ]
        )
    return rows


def _excluded_rows(
    fixtures: Collection[fmodel.Fixture], clubs: Mapping[str, fmodel.Club]
) -> list[list[str]]:
    rows = []
    for f in reportdata.by_home_away(fixtures, clubs, with_division=False):
        home_club = clubs[f.home_team.club]
        rows.append(
            [
                "TBC",
                reportdata.team_name(f.home_team, clubs),
                reportdata.team_name(f.away_team, clubs),
                home_club.home_venue_name,
                home_club.home_start_time,
                home_club.home_time_limit,
            ]
        )
    return rows


def _excluded_team_rows(
    team: fmodel.Team,
    fixtures: Collection[fmodel.Fixture],
    clubs: Mapping[str, fmodel.Club],
) -> list[list[str]]:
    rows = []
    for f in sorted(
        fixtures,
        key=lambda f: reportdata.team_sort_key(
            f.away_team if f.home_team == team else f.home_team, clubs
        ),
    ):
        is_home = f.home_team == team
        opponent = f.away_team if is_home else f.home_team
        home_club = clubs[f.home_team.club]
        rows.append(
            [
                "TBC",
                reportdata.team_name(opponent, clubs),
                "Home" if is_home else "Away",
                home_club.home_venue_name,
                home_club.home_start_time,
                home_club.home_time_limit,
                "",
            ]
        )
    return rows


def _anchored_heading(level: int, text: str, anchor_id: str) -> str:
    """An <hN> heading with a stable id, plus a small self-link icon next to it so
    the anchor is discoverable and its URL easy to grab (e.g. to copy or bookmark)."""
    escaped = html.escape(text)
    return (
        f'<h{level} id="{anchor_id}">{escaped} '
        f'<a class="anchor-link" href="#{anchor_id}" aria-label="Link to {escaped}">'
        f"\U0001f517</a></h{level}>\n"
    )


def _scheme_label(
    division_schemes: Mapping[int, fmodel.FixtureScheme], division: int
) -> str:
    """The name of a division's fixture-generation scheme, for the pages and
    sections that cover a single unambiguous division. A division not present in
    division_schemes uses the default (a double round)."""
    single = division_schemes.get(division) is fmodel.FixtureScheme.SINGLE_ROUND
    return "Single-round all-play-all" if single else "Double-round all-play-all"


def _csv_link(filename: str, label: str) -> str:
    """A short paragraph linking a CSV export sitting next to the page, shown only
    when csvreport.generate_csv has actually written that file."""
    return f'<p class="export-link"><a href="{filename}">{html.escape(label)}</a></p>\n'


def _venue_header(club: fmodel.Club) -> str:
    return (
        f'<p class="venue"><strong>{html.escape(club.home_venue_name)}</strong><br>\n'
        f"{html.escape(club.home_venue_address)}</p>\n"
    )


def _club_date_summary(
    club_id: str, fixtures: Collection[fmodel.ScheduledFixture]
) -> str:
    """A one-line count of the distinct dates a club is in action on, shown under
    its consolidated table. Excluded (TBC) fixtures have no date and don't count."""
    counts = reportdata.club_date_counts(club_id, fixtures)
    return (
        '<p class="date-summary">Distinct match dates: '
        f"<strong>{counts.total}</strong> total "
        f"(<strong>{counts.home}</strong> home, "
        f"<strong>{counts.away}</strong> away)</p>\n"
    )


def _venues_section(club_ids: Collection[str], clubs: Mapping[str, fmodel.Club]) -> str:
    if not club_ids:
        return ""
    items = "".join(
        f"<li><strong>{html.escape(clubs[cid].name)}</strong>: "
        f"{html.escape(clubs[cid].home_venue_name)}, "
        f"{html.escape(clubs[cid].home_venue_address)}</li>\n"
        for cid in sorted(club_ids, key=lambda cid: clubs[cid].name)
    )
    return f'<h2>Venues</h2>\n<ul class="venues">\n{items}</ul>\n'


def _home_club_ids_scheduled(
    fixtures: Collection[fmodel.ScheduledFixture],
) -> set[str]:
    return {sf.fixture.home_team.club for sf in fixtures}


def _home_club_ids_excluded(fixtures: Collection[fmodel.Fixture]) -> set[str]:
    return {f.home_team.club for f in fixtures}


def _nav(links: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<li><a href="{href}">{html.escape(text)}</a></li>\n' for href, text in links
    )
    return f"<nav><ul>\n{items}</ul></nav>\n"


def _run_index_body(
    all_matches_links: list[tuple[str, str]],
    division_links: list[tuple[str, str]],
    club_links: list[tuple[str, str]],
) -> str:
    """The body of a run's index.html: three lists of (href, label) links, one
    per section, in the order they'll appear on the page."""
    return (
        "<h2>All matches</h2>\n"
        + _nav(all_matches_links)
        + "<h2>Divisions</h2>\n"
        + _nav(division_links)
        + "<h2>Clubs</h2>\n"
        + _nav(club_links)
    )


_MATCH_HEADERS = ["Date", "Home", "Away", "Venue", "Start", "Time Limit"]
_TEAM_MATCH_HEADERS = [
    "Date",
    "Opponent",
    "Home/Away",
    "Venue",
    "Start",
    "Time Limit",
    "Days Since Last",
]
_MATCH_HEADERS_WITH_DIVISION = [
    "Date",
    "Div",
    "Home",
    "Away",
    "Venue",
    "Start",
    "Time Limit",
]


def generate_report(
    spec: fixturespec.Spec,
    fixtures: Collection[fmodel.ScheduledFixture],
    output_dir: Path,
) -> Path:
    """Write a solved fixture list's report pages, plus the run's index.html
    linking them, into output_dir.

    `spec` supplies everything about the season except the solved dates -- teams,
    clubs, the run name/draft/description banners, each division's fixture scheme,
    and any excluded_fixtures (withheld from scheduling entirely, to be arranged
    in a later run; these are appended to the bottom of every relevant table with
    "TBC" in place of a date). `fixtures` is the solved schedule for that spec.

    Wherever csvreport.generate_csv has already written its exports into
    output_dir, they get linked too: all-matches.csv / all-matches-by-team.csv
    from the run index, each club's club-<slug>-dates.csv from its club page, and
    each team's team-<slug>.csv from that team's section. Run generate_csv first
    if those links are wanted.

    Returns the path to the run's index.html.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    teams = spec.parameters.teams
    clubs = spec.clubs
    excluded_fixtures = spec.parameters.excluded_fixtures
    schemes = spec.parameters.division_schemes
    name, draft, description = spec.name, spec.draft, spec.description

    fixtures_by_division: dict[int, list[fmodel.ScheduledFixture]] = defaultdict(list)
    for sf in fixtures:
        fixtures_by_division[sf.fixture.home_team.division].append(sf)

    excluded_by_division: dict[int, list[fmodel.Fixture]] = defaultdict(list)
    for f in excluded_fixtures:
        excluded_by_division[f.home_team.division].append(f)

    teams_by_club: dict[str, list[fmodel.Team]] = defaultdict(list)
    for team in teams:
        teams_by_club[team.club].append(team)

    fixtures_by_club: dict[str, list[fmodel.ScheduledFixture]] = defaultdict(list)
    for sf in fixtures:
        fixtures_by_club[sf.fixture.home_team.club].append(sf)
        if sf.fixture.away_team.club != sf.fixture.home_team.club:
            fixtures_by_club[sf.fixture.away_team.club].append(sf)

    excluded_by_club: dict[str, list[fmodel.Fixture]] = defaultdict(list)
    for f in excluded_fixtures:
        excluded_by_club[f.home_team.club].append(f)
        if f.away_team.club != f.home_team.club:
            excluded_by_club[f.away_team.club].append(f)

    # All matches
    all_match_club_ids = _home_club_ids_scheduled(fixtures) | _home_club_ids_excluded(
        excluded_fixtures
    )
    (output_dir / "all-matches.html").write_text(
        _page(
            "All matches",
            _table(
                _MATCH_HEADERS_WITH_DIVISION,
                _rows_with_division(fixtures, clubs)
                + _excluded_rows_with_division(excluded_fixtures, clubs),
            )
            + _venues_section(all_match_club_ids, clubs),
            name,
            draft,
            description,
        )
    )
    all_matches_links: list[tuple[str, str]] = [("all-matches.html", "All matches")]
    for csv_name, csv_label in (
        ("all-matches.csv", "CSV: one row per match"),
        ("all-matches-by-team.csv", "CSV: two rows per match, one per team"),
    ):
        if (output_dir / csv_name).exists():
            all_matches_links.append((csv_name, csv_label))

    # One page per division
    division_links: list[tuple[str, str]] = []
    for division in sorted(set(fixtures_by_division) | set(excluded_by_division)):
        division_club_ids = _home_club_ids_scheduled(
            fixtures_by_division[division]
        ) | _home_club_ids_excluded(excluded_by_division[division])
        (output_dir / f"division-{division}.html").write_text(
            _page(
                f"Division {division}",
                f"<h2>{html.escape(_scheme_label(schemes, division))}</h2>\n"
                + _table(
                    _MATCH_HEADERS,
                    _rows(fixtures_by_division[division], clubs)
                    + _excluded_rows(excluded_by_division[division], clubs),
                )
                + _venues_section(division_club_ids, clubs),
                name,
                draft,
                description,
            )
        )
        division_links.append((f"division-{division}.html", f"Division {division}"))

    # One page per club: venue header, consolidated table, then one table per team
    club_links: list[tuple[str, str]] = []
    for club_id in sorted(teams_by_club):
        club_name = clubs[club_id].name
        body = (
            _venue_header(clubs[club_id])
            + _table(
                _MATCH_HEADERS_WITH_DIVISION,
                _rows_with_division(fixtures_by_club.get(club_id, []), clubs)
                + _excluded_rows_with_division(
                    excluded_by_club.get(club_id, []), clubs
                ),
            )
            + _club_date_summary(club_id, fixtures_by_club.get(club_id, []))
        )
        club_csv = reportdata.club_dates_csv_filename(club_id)
        if (output_dir / club_csv).exists():
            body += _csv_link(club_csv, "Download CSV (one row per match date)")
        for team in sorted(teams_by_club[club_id], key=lambda t: t.index):
            team_fixtures = [
                sf
                for sf in fixtures
                if sf.fixture.home_team == team or sf.fixture.away_team == team
            ]
            team_excluded = [
                f
                for f in excluded_fixtures
                if f.home_team == team or f.away_team == team
            ]
            team_name = reportdata.team_name(team, clubs)
            body += _anchored_heading(2, team_name, reportdata.slugify(team_name))
            scheme_label = _scheme_label(schemes, team.division)
            body += f"<h3>Division {team.division} ({html.escape(scheme_label)})</h3>\n"
            body += _table(
                _TEAM_MATCH_HEADERS,
                _team_rows(team, team_fixtures, clubs)
                + _excluded_team_rows(team, team_excluded, clubs),
            )
            team_csv = reportdata.team_csv_filename(team)
            if (output_dir / team_csv).exists():
                body += _csv_link(team_csv, "Download CSV")
        club_slug = reportdata.slugify(club_id)
        (output_dir / f"club-{club_slug}.html").write_text(
            _page(club_name, body, name, draft, description)
        )
        club_links.append((f"club-{club_slug}.html", club_name))
    club_links.sort()

    index_path = output_dir / "index.html"
    index_path.write_text(
        _page(
            "Fixtures",
            _run_index_body(all_matches_links, division_links, club_links),
            name,
            draft,
            description,
            is_run_home=True,
        )
    )
    return index_path


def find_run_dirs(runs_dir: Path) -> list[Path]:
    """Every directory anywhere under runs_dir that has its own report (i.e. has an
    all-matches.html), at any depth -- a run need not sit directly under runs_dir.
    """
    if not runs_dir.is_dir():
        return []
    return sorted(p.parent for p in runs_dir.rglob("all-matches.html"))


@dataclasses.dataclass
class _RunTreeNode:
    """One level of the folder hierarchy under runs_dir: is_run if this exact path
    is itself a run (has its own report), plus any child folders (further runs
    and/or grouping folders) keyed by name."""

    is_run: bool = False
    children: dict[str, _RunTreeNode] = dataclasses.field(default_factory=dict)


def _build_run_tree(run_paths: Collection[Path]) -> _RunTreeNode:
    root = _RunTreeNode()
    for run_path in run_paths:
        node = root
        for part in run_path.parts:
            node = node.children.setdefault(part, _RunTreeNode())
        node.is_run = True
    return root


def _render_run_tree(
    node: _RunTreeNode, path_parts: tuple[str, ...], rel_runs_dir: Path
) -> str:
    """Render node's children as a nested <ul>, most recent (reverse alphabetical)
    first at each level; a name with no report of its own (just a grouping folder
    for further runs, e.g. a season with several draft sub-folders) is rendered as
    a plain label rather than a link."""
    items = []
    for name in sorted(node.children, reverse=True):
        child = node.children[name]
        child_parts = (*path_parts, name)
        label = html.escape(name)
        if child.is_run:
            href = f"{rel_runs_dir}/{'/'.join(child_parts)}/index.html"
            label = f'<a href="{href}">{label}</a>'
        if child.children:
            label += _render_run_tree(child, child_parts, rel_runs_dir)
        items.append(f"<li>{label}</li>\n")
    return f"<ul>\n{''.join(items)}</ul>\n"


def write_runs_index(runs_dir: Path, index_path: Path) -> Path:
    """(Re)write the top-level index page listing every run under runs_dir that has
    a report, at any depth -- nested to reflect runs_dir's own folder structure
    (e.g. runs/2026-27/draft1 is listed under a "2026-27" heading)."""
    run_dirs = find_run_dirs(runs_dir)

    if run_dirs:
        try:
            rel_runs_dir = runs_dir.relative_to(index_path.parent)
        except ValueError:
            rel_runs_dir = runs_dir
        run_paths = [d.relative_to(runs_dir) for d in run_dirs]
        tree = _build_run_tree(run_paths)
        body = f"<nav>{_render_run_tree(tree, (), rel_runs_dir)}</nav>\n"
    else:
        body = "<p>No runs yet.</p>\n"

    index_path.write_text(_page("Fixture runs", body))
    return index_path
