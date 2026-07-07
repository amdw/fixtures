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
    table { border-collapse: collapse; margin-bottom: 2rem; width: 100%; max-width: 60rem; }
    th, td { border: 1px solid #ccc; padding: 0.4rem 0.8rem; text-align: left; }
    th { background: #f0f0f0; }
    tr:nth-child(even) { background: #fafafa; }
    a { color: #0645ad; }
    nav ul { list-style: none; padding: 0; }
    nav li { margin-bottom: 0.3rem; }
"""

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


def slugify(value: str) -> str:
    """Turn a name into a filesystem/URL-safe slug, e.g. 'Willesden & Brent' -> 'willesden-brent'."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unnamed"


def _fmt_date(d: date) -> str:
    return f"{d.strftime('%a')} {d.isoformat()}"


def _page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        f"<h1>{html.escape(title)}</h1>\n"
        f"{body}"
        "</body>\n"
        "</html>\n"
    )


def _page_title(path: Path) -> str:
    """Recover the display title of a page previously written by _page(), for nav links."""
    match = _TITLE_RE.search(path.read_text())
    return html.unescape(match.group(1)) if match else path.stem


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head_html = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    if rows:
        body_html = "".join(
            "<tr>"
            + "".join(f"<td>{html.escape(cell)}</td>" for cell in row)
            + "</tr>\n"
            for row in rows
        )
    else:
        body_html = f'<tr><td colspan="{len(headers)}"><em>No matches</em></td></tr>\n'
    return (
        "<table>\n"
        f"<thead><tr>{head_html}</tr></thead>\n"
        f"<tbody>\n{body_html}</tbody>\n"
        "</table>\n"
    )


def _team_name(team: fmodel.Team, clubs: Mapping[str, fmodel.Club]) -> str:
    if team.name_override:
        return team.name_override
    return f"{clubs[team.club].name} {team.index}"


def _by_date(
    fixtures: Collection[fmodel.ScheduledFixture], clubs: Mapping[str, fmodel.Club]
) -> list[fmodel.ScheduledFixture]:
    return sorted(
        fixtures, key=lambda sf: (sf.date, _team_name(sf.fixture.home_team, clubs))
    )


def _rows_with_division(
    fixtures: Collection[fmodel.ScheduledFixture], clubs: Mapping[str, fmodel.Club]
) -> list[list[str]]:
    rows = []
    for sf in _by_date(fixtures, clubs):
        home_club = clubs[sf.fixture.home_team.club]
        rows.append(
            [
                _fmt_date(sf.date),
                str(sf.fixture.home_team.division),
                _team_name(sf.fixture.home_team, clubs),
                _team_name(sf.fixture.away_team, clubs),
                home_club.home_venue,
                home_club.home_start_time,
                home_club.home_time_limit,
            ]
        )
    return rows


def _rows(
    fixtures: Collection[fmodel.ScheduledFixture], clubs: Mapping[str, fmodel.Club]
) -> list[list[str]]:
    rows = []
    for sf in _by_date(fixtures, clubs):
        home_club = clubs[sf.fixture.home_team.club]
        rows.append(
            [
                _fmt_date(sf.date),
                _team_name(sf.fixture.home_team, clubs),
                _team_name(sf.fixture.away_team, clubs),
                home_club.home_venue,
                home_club.home_start_time,
                home_club.home_time_limit,
            ]
        )
    return rows


def _nav(links: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<li><a href="{href}">{html.escape(text)}</a></li>\n' for href, text in links
    )
    return f"<nav><ul>\n{items}</ul></nav>\n"


def _division_number(path: Path) -> int:
    return int(path.stem.removeprefix("division-"))


_MATCH_HEADERS = ["Date", "Home", "Away", "Venue", "Start", "Time Limit"]
_MATCH_HEADERS_WITH_DIVISION = [
    "Date",
    "Division",
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
) -> Path:
    """Write all HTML report pages for a solved fixture list into output_dir.

    Returns the path to the run's index.html.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    fixtures_by_division: dict[int, list[fmodel.ScheduledFixture]] = defaultdict(list)
    for sf in fixtures:
        fixtures_by_division[sf.fixture.home_team.division].append(sf)

    teams_by_club: dict[str, list[fmodel.Team]] = defaultdict(list)
    for team in teams:
        teams_by_club[team.club].append(team)

    fixtures_by_club: dict[str, list[fmodel.ScheduledFixture]] = defaultdict(list)
    for sf in fixtures:
        fixtures_by_club[sf.fixture.home_team.club].append(sf)
        if sf.fixture.away_team.club != sf.fixture.home_team.club:
            fixtures_by_club[sf.fixture.away_team.club].append(sf)

    # All matches
    (output_dir / "all-matches.html").write_text(
        _page(
            "All matches",
            _table(_MATCH_HEADERS_WITH_DIVISION, _rows_with_division(fixtures, clubs)),
        )
    )

    # One page per division
    for division in sorted(fixtures_by_division):
        (output_dir / f"division-{division}.html").write_text(
            _page(
                f"Division {division}",
                _table(_MATCH_HEADERS, _rows(fixtures_by_division[division], clubs)),
            )
        )

    # One page per club: consolidated table, then one table per team
    for club_id in sorted(teams_by_club):
        club_name = clubs[club_id].name
        body = _table(
            _MATCH_HEADERS_WITH_DIVISION,
            _rows_with_division(fixtures_by_club.get(club_id, []), clubs),
        )
        for team in sorted(teams_by_club[club_id], key=lambda t: t.index):
            team_fixtures = [
                sf
                for sf in fixtures
                if sf.fixture.home_team == team or sf.fixture.away_team == team
            ]
            body += f"<h2>{html.escape(_team_name(team, clubs))}</h2>\n"
            body += _table(_MATCH_HEADERS, _rows(team_fixtures, clubs))
        (output_dir / f"club-{slugify(club_id)}.html").write_text(
            _page(club_name, body)
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

    index_path = run_dir / "index.html"
    index_path.write_text(_page("Fixtures", body))
    return index_path


def write_runs_index(runs_dir: Path, index_path: Path) -> Path:
    """(Re)write the top-level index page listing every run under runs_dir that has a report."""
    run_names = []
    if runs_dir.is_dir():
        run_names = sorted(
            (
                p.name
                for p in runs_dir.iterdir()
                if p.is_dir() and (p / "all-matches.html").exists()
            ),
            reverse=True,
        )

    if run_names:
        try:
            rel_runs_dir = runs_dir.relative_to(index_path.parent)
        except ValueError:
            rel_runs_dir = runs_dir
        body = _nav([(f"{rel_runs_dir}/{name}/index.html", name) for name in run_names])
    else:
        body = "<p>No runs yet.</p>\n"

    index_path.write_text(_page("Fixture runs", body))
    return index_path
