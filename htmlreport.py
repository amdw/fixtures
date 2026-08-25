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

The index pages (both the per-run index.html and the top-level runs index)
are derived purely from the report files present on disk, so they can be
(re)built at any time -- e.g. by build_indexes.py during a GitHub Pages
deploy -- without needing the original fixtures/teams data or any
third-party dependency.
"""

from __future__ import annotations

import dataclasses
import html
import re
from collections import defaultdict
from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import fmodel

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

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_RUN_NAME_RE = re.compile(r'<span class="run-name">(.*?)</span>', re.DOTALL)
_DESCRIPTION_RE = re.compile(r'<p class="description">(.*?)</p>', re.DOTALL)
_DRAFT_MARKER = 'class="draft-label"'


def slugify(value: str) -> str:
    """Turn a name into a filesystem/URL-safe slug, e.g. 'Willesden & Brent' -> 'willesden-brent'."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unnamed"


def _fmt_date(d: date) -> str:
    return d.strftime("%a %d %b %Y")


def _page(
    title: str,
    body: str,
    run_name: str = "",
    draft: bool = False,
    description: str = "",
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
        f"<title>{html.escape(title)}</title>\n"
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


def _page_title(path: Path) -> str:
    """Recover the display title of a page previously written by _page(), for nav links."""
    match = _TITLE_RE.search(path.read_text())
    return html.unescape(match.group(1)) if match else path.stem


def _page_run_name(path: Path) -> str:
    """Recover the run name banner text of a page previously written by _page(), if any."""
    match = _RUN_NAME_RE.search(path.read_text())
    return html.unescape(match.group(1)) if match else ""


def _page_is_draft(path: Path) -> bool:
    """Recover whether a page previously written by _page() carried a draft banner."""
    return _DRAFT_MARKER in path.read_text()


def _page_description(path: Path) -> str:
    """Recover the description of a page previously written by _page(), if any."""
    match = _DESCRIPTION_RE.search(path.read_text())
    return html.unescape(match.group(1)) if match else ""


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


def _team_name(team: fmodel.Team, clubs: Mapping[str, fmodel.Club]) -> str:
    if team.name_override:
        return team.name_override
    return f"{clubs[team.club].name} {team.index}"


def _home_away_sort_key(
    sf: fmodel.ScheduledFixture, clubs: Mapping[str, fmodel.Club]
) -> tuple:
    """Sort key for home/away tables: date, home club, home index, away club, away index."""
    return (
        sf.date,
        clubs[sf.fixture.home_team.club].name,
        sf.fixture.home_team.index,
        clubs[sf.fixture.away_team.club].name,
        sf.fixture.away_team.index,
    )


def _home_away_division_sort_key(
    sf: fmodel.ScheduledFixture, clubs: Mapping[str, fmodel.Club]
) -> tuple:
    """Sort key for home/away+division tables: date, division, home club, home index, away club, away index."""
    return (
        sf.date,
        sf.fixture.home_team.division,
        clubs[sf.fixture.home_team.club].name,
        sf.fixture.home_team.index,
        clubs[sf.fixture.away_team.club].name,
        sf.fixture.away_team.index,
    )


def _excluded_home_away_sort_key(
    f: fmodel.Fixture, clubs: Mapping[str, fmodel.Club]
) -> tuple:
    """Sort key for excluded home/away tables: home club, home index, away club, away index."""
    return (
        clubs[f.home_team.club].name,
        f.home_team.index,
        clubs[f.away_team.club].name,
        f.away_team.index,
    )


def _excluded_home_away_division_sort_key(
    f: fmodel.Fixture, clubs: Mapping[str, fmodel.Club]
) -> tuple:
    """Sort key for excluded home/away+division tables: division, home club, home index, away club, away index."""
    return (
        f.home_team.division,
        clubs[f.home_team.club].name,
        f.home_team.index,
        clubs[f.away_team.club].name,
        f.away_team.index,
    )


def _rows_with_division(
    fixtures: Collection[fmodel.ScheduledFixture], clubs: Mapping[str, fmodel.Club]
) -> list[list[str]]:
    rows = []
    for sf in sorted(fixtures, key=lambda sf: _home_away_division_sort_key(sf, clubs)):
        home_club = clubs[sf.fixture.home_team.club]
        rows.append(
            [
                _fmt_date(sf.date),
                str(sf.fixture.home_team.division),
                _team_name(sf.fixture.home_team, clubs),
                _team_name(sf.fixture.away_team, clubs),
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
    for sf in sorted(fixtures, key=lambda sf: _home_away_sort_key(sf, clubs)):
        home_club = clubs[sf.fixture.home_team.club]
        rows.append(
            [
                _fmt_date(sf.date),
                _team_name(sf.fixture.home_team, clubs),
                _team_name(sf.fixture.away_team, clubs),
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
    for sf in sorted(
        fixtures,
        key=lambda sf: (
            sf.date,
            team.index,
            clubs[
                sf.fixture.away_team.club
                if sf.fixture.home_team == team
                else sf.fixture.home_team.club
            ].name,
            (
                sf.fixture.away_team.index
                if sf.fixture.home_team == team
                else sf.fixture.home_team.index
            ),
        ),
    ):
        is_home = sf.fixture.home_team == team
        opponent = sf.fixture.away_team if is_home else sf.fixture.home_team
        home_club = clubs[sf.fixture.home_team.club]
        rows.append(
            [
                _fmt_date(sf.date),
                _team_name(opponent, clubs),
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
    for f in sorted(
        fixtures, key=lambda f: _excluded_home_away_division_sort_key(f, clubs)
    ):
        home_club = clubs[f.home_team.club]
        rows.append(
            [
                "TBC",
                str(f.home_team.division),
                _team_name(f.home_team, clubs),
                _team_name(f.away_team, clubs),
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
    for f in sorted(fixtures, key=lambda f: _excluded_home_away_sort_key(f, clubs)):
        home_club = clubs[f.home_team.club]
        rows.append(
            [
                "TBC",
                _team_name(f.home_team, clubs),
                _team_name(f.away_team, clubs),
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
        key=lambda f: (
            team.index,
            clubs[f.away_team.club if f.home_team == team else f.home_team.club].name,
            f.away_team.index if f.home_team == team else f.home_team.index,
        ),
    ):
        is_home = f.home_team == team
        opponent = f.away_team if is_home else f.home_team
        home_club = clubs[f.home_team.club]
        rows.append(
            [
                "TBC",
                _team_name(opponent, clubs),
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


def _venue_header(club: fmodel.Club) -> str:
    return (
        f'<p class="venue"><strong>{html.escape(club.home_venue_name)}</strong><br>\n'
        f"{html.escape(club.home_venue_address)}</p>\n"
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


def _division_number(path: Path) -> int:
    return int(path.stem.removeprefix("division-"))


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
    fixtures: Collection[fmodel.ScheduledFixture],
    teams: Collection[fmodel.Team],
    clubs: Mapping[str, fmodel.Club],
    output_dir: Path,
    excluded_fixtures: Collection[fmodel.Fixture] = (),
    name: str = "",
    draft: bool = False,
    description: str = "",
) -> Path:
    """Write all HTML report pages for a solved fixture list into output_dir.

    excluded_fixtures (fixtures withheld from scheduling entirely, to be arranged
    in a later run) are appended to the bottom of every relevant table with "TBC"
    in place of a date.

    Returns the path to the run's index.html.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

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

    # One page per division
    for division in sorted(set(fixtures_by_division) | set(excluded_by_division)):
        division_club_ids = _home_club_ids_scheduled(
            fixtures_by_division[division]
        ) | _home_club_ids_excluded(excluded_by_division[division])
        (output_dir / f"division-{division}.html").write_text(
            _page(
                f"Division {division}",
                _table(
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

    # One page per club: venue header, consolidated table, then one table per team
    for club_id in sorted(teams_by_club):
        club_name = clubs[club_id].name
        body = _venue_header(clubs[club_id]) + _table(
            _MATCH_HEADERS_WITH_DIVISION,
            _rows_with_division(fixtures_by_club.get(club_id, []), clubs)
            + _excluded_rows_with_division(excluded_by_club.get(club_id, []), clubs),
        )
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
            team_name = _team_name(team, clubs)
            body += _anchored_heading(2, team_name, slugify(team_name))
            body += f"<h3>Division {team.division}</h3>\n"
            body += _table(
                _TEAM_MATCH_HEADERS,
                _team_rows(team, team_fixtures, clubs)
                + _excluded_team_rows(team, team_excluded, clubs),
            )
        (output_dir / f"club-{slugify(club_id)}.html").write_text(
            _page(club_name, body, name, draft, description)
        )

    return build_run_index(output_dir)


def build_run_index(run_dir: Path) -> Path:
    """(Re)build a run's index.html purely from the report files present in run_dir."""
    all_matches_path = run_dir / "all-matches.html"
    division_paths = sorted(run_dir.glob("division-*.html"), key=_division_number)
    club_paths = sorted(run_dir.glob("club-*.html"))

    body = "<h2>All matches</h2>\n"
    if all_matches_path.exists():
        body += _nav([("all-matches.html", _page_title(all_matches_path))])
    body += "<h2>Divisions</h2>\n"
    body += _nav([(p.name, _page_title(p)) for p in division_paths])
    body += "<h2>Clubs</h2>\n"
    body += _nav([(p.name, _page_title(p)) for p in club_paths])

    # Run name/draft status aren't known here (this can run standalone, from
    # just the files on disk), so recover them from an already-written page.
    reference_page = next(
        (p for p in [all_matches_path, *division_paths, *club_paths] if p.exists()),
        None,
    )
    run_name = _page_run_name(reference_page) if reference_page else ""
    draft = _page_is_draft(reference_page) if reference_page else False
    description = _page_description(reference_page) if reference_page else ""

    index_path = run_dir / "index.html"
    index_path.write_text(_page("Fixtures", body, run_name, draft, description))
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
