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

"""Solve a YAML fixture specification and write its solution to a solution.yaml file.

Usage:
    python solve.py [--allow-past-matches] <spec.yaml> [output_dir]

<output_dir> defaults to the spec file's own directory, so the usual layout keeps
spec.yaml and solution.yaml side by side in the same run folder (e.g.
runs/2025-26-season/). Re-running this overwrites the previous solution.yaml in
place.

By default the solve aborts without writing anything if any match in the solved
schedule falls before today -- the spec no longer carries an implicit "no play in
the past" cutoff, so this guards against re-solving a spec whose home dates have
partly passed. Pass --allow-past-matches to write the solution anyway (e.g.
re-solving a past season for reference).

report.py can then (re)generate the HTML report from solution.yaml -- without
needing to re-solve -- any time the report format changes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Collection
from datetime import date
from pathlib import Path

import fixturesolution
import fixturespec
import fmodel


class PastMatchError(Exception):
    """Raised when a solved schedule contains a match before today and
    --allow-past-matches was not given."""


def _reject_past_matches(fixtures: Collection[fmodel.ScheduledFixture]) -> None:
    """Raise PastMatchError if any scheduled fixture falls before today. Whether a
    fixture was pinned via fixed_fixtures or newly scheduled makes no difference:
    a solution.yaml is only ever written for a season still to be played."""
    today = date.today()
    past = sorted(
        (sf for sf in fixtures if sf.date < today),
        key=lambda sf: (
            sf.date,
            sf.fixture.home_team.name,
            sf.fixture.away_team.name,
        ),
    )
    if not past:
        return
    shown = ", ".join(
        f"{sf.fixture.home_team.name} vs {sf.fixture.away_team.name} on "
        f"{sf.date.isoformat()}"
        for sf in past[:12]
    )
    if len(past) > 12:
        shown += f", ... (+{len(past) - 12} more)"
    raise PastMatchError(
        f"{len(past)} scheduled match(es) fall before today "
        f"({today.isoformat()}): {shown}. Re-run with --allow-past-matches to "
        "write the solution anyway (e.g. re-solving a past season for reference)."
    )


def solve(
    spec_path: Path, output_dir: Path, *, allow_past_matches: bool = False
) -> Path:
    """Solve the given spec and write its solution.yaml into output_dir. Returns the
    path to the solution file.

    Raises PastMatchError (before writing anything) if the solved schedule
    contains a match before today and allow_past_matches is False.
    """
    spec = fixturespec.load_spec(spec_path)
    team_ids = fixturespec.load_team_ids(spec_path)
    result = fmodel.solve(spec.parameters)
    if not allow_past_matches:
        _reject_past_matches(result.fixtures)
    output_dir.mkdir(parents=True, exist_ok=True)
    solution_path = output_dir / "solution.yaml"
    fixturesolution.save_solution(result, team_ids, solution_path)
    return solution_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "spec", type=Path, help="Path to the YAML fixture specification"
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        nargs="?",
        help="Directory to write solution.yaml into (default: the spec file's own directory)",
    )
    parser.add_argument(
        "--allow-past-matches",
        action="store_true",
        help="Write the solution even if it schedules a match before today",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    output_dir = args.output_dir if args.output_dir is not None else args.spec.parent
    try:
        solution_path = solve(
            args.spec, output_dir, allow_past_matches=args.allow_past_matches
        )
    except PastMatchError as e:
        print(f"Not writing a solution: {e}", file=sys.stderr)
        raise SystemExit(1) from e

    print(f"Wrote solution to {solution_path}")


if __name__ == "__main__":
    main()
