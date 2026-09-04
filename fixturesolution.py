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

"""Read and write solved fixture lists in a canonical solution YAML format.

This is what lets solving (see solve.py) and reporting (see report.py) run as
separate steps: solve.py writes a solution.yaml, and report.py can turn that
back into a report -- without re-running the solver -- given the original spec.

Each fixture is identified by its home and away teams' stable spec team ID
(the same IDs used by fixed_fixtures/exclude_fixtures in the spec file, e.g.
"albany-1"), so a solution.yaml only makes sense alongside the spec it was
solved from. To make that pairing checkable, the file also records a
"spec_checksum" of the spec's bytes (see fixturespec.spec_checksum()).

A solution can also carry an "expected_invalid_reason" string: a hand-written
note that this particular solution is deliberately being kept even though it no
longer satisfies its spec's constraints (see fmodel.SolveResult). Absent (the
common case), the solution is expected to validate normally.

fmodel.Team itself doesn't carry that ID (it's a
club/index/division/name_override value type, solver-internal), so callers
provide a team_ids mapping -- see fixturespec.load_team_ids() -- to translate
between the two.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import yaml

import fmodel


class SolutionError(ValueError):
    """Raised when a solution file, or the fixtures/team_ids given to save one,
    don't resolve cleanly against the given team information."""


class _NoAliasDumper(yaml.SafeDumper):
    """A SafeDumper that never emits YAML anchors/aliases (&id001/*id001).

    PyYAML's default dumper aliases repeated *identical* date objects (by id(),
    not just equal value) to avoid writing them out twice; fmodel.solve() happens
    to reuse the same date object across every fixture scheduled on it (they all
    come from the same entry in Parameters.home_dates), so almost every date in a
    solution would otherwise come out as an opaque alias instead of a literal
    value. Aliases are valid YAML and round-trip fine, but they make the file
    needlessly hard to read and diff.
    """

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    """Write any multi-line string (i.e. the stored stats blocks) as a literal
    block scalar rather than PyYAML's default one-line double-quoted form with
    escaped newlines, so the solution file stays readable and diffable."""
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_NoAliasDumper.add_representer(str, _represent_str)


def _team_id(
    team: fmodel.Team, ids_by_club_index: Mapping[tuple[str, int], str]
) -> str:
    key = (team.club, team.index)
    if key not in ids_by_club_index:
        raise SolutionError(
            f"No team ID found for club {team.club!r} index {team.index!r} "
            "(team_ids doesn't match the given fixtures)"
        )
    return ids_by_club_index[key]


def save_solution(
    result: fmodel.SolveResult,
    team_ids: Mapping[str, tuple[str, int]],
    path: Path,
) -> None:
    """Write a solve result to path in the canonical solution YAML format.

    team_ids maps each team ID in the spec that was solved to its (club, index)
    pair (see fixturespec.load_team_ids()) -- used to translate fmodel.Team,
    which doesn't carry a spec team ID, back into one.

    result.spec_checksum, when non-empty, is written under a "spec_checksum" key
    after the fixtures so the solution records a fingerprint of the exact spec it
    was solved from (see fixturespec.spec_checksum()).

    result.expected_invalid_reason, when non-empty, is written under an
    "expected_invalid_reason" key -- see fmodel.SolveResult for what it means.
    solve()-produced results never set it, so it's only ever written back
    unchanged when re-saving an already-annotated solution.

    result.model_stats/result.solve_stats, when non-empty, are written under a
    trailing "stats" key so the OR-Tools summaries travel with the schedule to
    the report; empty strings leave the key out entirely. load_solution()
    reverses this exactly, back into an fmodel.SolveResult.
    """
    ids_by_club_index = {
        club_index: team_id for team_id, club_index in team_ids.items()
    }
    entries = [
        {
            "home": _team_id(sf.fixture.home_team, ids_by_club_index),
            "away": _team_id(sf.fixture.away_team, ids_by_club_index),
            "date": sf.date,
        }
        for sf in result.fixtures
    ]
    entries.sort(key=lambda e: (e["date"], e["home"], e["away"]))
    document: dict[str, Any] = {"fixtures": entries}
    if result.spec_checksum:
        document["spec_checksum"] = result.spec_checksum
    if result.expected_invalid_reason:
        document["expected_invalid_reason"] = result.expected_invalid_reason
    if result.model_stats or result.solve_stats:
        document["stats"] = {
            "model": result.model_stats,
            "solve": result.solve_stats,
        }
    path.write_text(yaml.dump(document, Dumper=_NoAliasDumper, sort_keys=False))


_REQUIRED_FIXTURE_FIELDS = {"home", "away", "date"}


def _resolve_team(
    entry: dict[str, Any],
    field: str,
    teams_by_id: Mapping[str, fmodel.Team],
    context: str,
) -> fmodel.Team:
    team_id = entry[field]
    if team_id not in teams_by_id:
        raise SolutionError(f"{context}.{field}: unknown team {team_id!r}")
    return teams_by_id[team_id]


def _load_stats(data: dict[str, Any], path: Path) -> tuple[str, str]:
    """Pull the optional model/solve summary text out of a loaded solution
    document. A file with no 'stats' key (one written before it was recorded)
    yields two empty strings."""
    stats = data.get("stats")
    if stats is None:
        return "", ""
    if not isinstance(stats, dict):
        raise SolutionError(f"{path}: 'stats' must be a mapping")
    model_stats = stats.get("model", "")
    solve_stats = stats.get("solve", "")
    if not isinstance(model_stats, str) or not isinstance(solve_stats, str):
        raise SolutionError(f"{path}: 'stats.model' and 'stats.solve' must be strings")
    return model_stats, solve_stats


def _load_spec_checksum(data: dict[str, Any], path: Path) -> str:
    """Pull the optional spec-file checksum out of a loaded solution document. A
    file with no 'spec_checksum' key (one written before it was recorded) yields
    an empty string."""
    checksum = data.get("spec_checksum", "")
    if not isinstance(checksum, str):
        raise SolutionError(f"{path}: 'spec_checksum' must be a string")
    return checksum


def _load_expected_invalid_reason(data: dict[str, Any], path: Path) -> str:
    """Pull the optional expected-invalid annotation out of a loaded solution
    document. A file with no 'expected_invalid_reason' key (the common case)
    yields an empty string, meaning the solution is expected to validate
    normally."""
    reason = data.get("expected_invalid_reason", "")
    if not isinstance(reason, str):
        raise SolutionError(f"{path}: 'expected_invalid_reason' must be a string")
    return reason


def load_solution(
    path: Path,
    teams: Collection[fmodel.Team],
    team_ids: Mapping[str, tuple[str, int]],
) -> fmodel.SolveResult:
    """Load a solution YAML file back into an fmodel.SolveResult, resolving each
    entry's home/away team ID via team_ids and teams (normally
    fixturespec.load_team_ids() and spec.parameters.teams from the spec the
    solution was solved from) to recover full Team objects (division,
    name_override, etc.).

    Any model/solver summary text stored under the file's 'stats' key, the
    spec-file checksum under its 'spec_checksum' key, and the annotation under
    its 'expected_invalid_reason' key, come back on the result too (empty
    strings if the file predates any of them).
    """
    with path.open() as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "fixtures" not in data:
        raise SolutionError(f"{path}: expected a mapping with a 'fixtures' key")

    fixtures_spec = data["fixtures"]
    if not isinstance(fixtures_spec, list):
        raise SolutionError(f"{path}: 'fixtures' must be a list")

    model_stats, solve_stats = _load_stats(data, path)
    spec_file_checksum = _load_spec_checksum(data, path)
    expected_invalid_reason = _load_expected_invalid_reason(data, path)

    teams_by_club_index = {(t.club, t.index): t for t in teams}
    teams_by_id = {
        team_id: teams_by_club_index[club_index]
        for team_id, club_index in team_ids.items()
        if club_index in teams_by_club_index
    }

    result = []
    for i, entry in enumerate(fixtures_spec):
        context = f"{path}: fixtures[{i}]"
        if not isinstance(entry, dict):
            raise SolutionError(f"{context} must be a mapping")
        missing = _REQUIRED_FIXTURE_FIELDS - entry.keys()
        if missing:
            raise SolutionError(
                f"{context} missing required field(s) {sorted(missing)}"
            )

        home_team = _resolve_team(entry, "home", teams_by_id, context)
        away_team = _resolve_team(entry, "away", teams_by_id, context)

        fixture_date = entry["date"]
        if isinstance(fixture_date, str):
            try:
                fixture_date = date.fromisoformat(fixture_date)
            except ValueError as e:
                raise SolutionError(
                    f"{context}.date: {fixture_date!r} is not a valid ISO8601 "
                    "(yyyy-mm-dd) date"
                ) from e
        elif not isinstance(fixture_date, date):
            raise SolutionError(
                f"{context}.date: expected a date, got {fixture_date!r}"
            )

        result.append(
            fmodel.ScheduledFixture(
                fixture=fmodel.Fixture(home_team=home_team, away_team=away_team),
                date=fixture_date,
            )
        )
    return fmodel.SolveResult(
        fixtures=result,
        model_stats=model_stats,
        solve_stats=solve_stats,
        spec_checksum=spec_file_checksum,
        expected_invalid_reason=expected_invalid_reason,
    )
