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
    python solve.py <spec.yaml> [output_dir]

<output_dir> defaults to the spec file's own directory, so the usual layout keeps
spec.yaml and solution.yaml side by side in the same run folder (e.g.
runs/2025-26-season/). Re-running this overwrites the previous solution.yaml in
place.

report.py can then (re)generate the HTML report from solution.yaml -- without
needing to re-solve -- any time the report format changes.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
from datetime import date
from pathlib import Path

import fixturesolution
import fixturespec
import fmodel

logger = logging.getLogger(__name__)


def solve(
    spec_path: Path, output_dir: Path, earliest_match_date: date | None = None
) -> Path:
    """Solve the given spec and write its solution.yaml into output_dir. Returns the
    path to the solution file.

    earliest_match_date, if given, overrides the spec's (always-None)
    Parameters.earliest_match_date -- see fmodel.Parameters for what it does. It's a
    solve-time argument rather than a spec field since its usual value (today) is
    tied to when the solver is run, not to the season being modelled.
    """
    spec = fixturespec.load_spec(spec_path)
    team_ids = fixturespec.load_team_ids(spec_path)
    parameters = spec.parameters
    if earliest_match_date is not None:
        logger.info("Excluding new fixtures dated before %s", earliest_match_date)
        parameters = dataclasses.replace(
            parameters, earliest_match_date=earliest_match_date
        )
    result = fmodel.solve(parameters)
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
        "--earliest-match-date",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="Don't schedule any new fixture before this date (default: today, so "
        "re-running the solver doesn't place a fixture in the past). Pass a date on "
        "or before the spec's earliest home date to disable this cutoff, e.g. for an "
        "old spec being re-solved for reference.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    output_dir = args.output_dir if args.output_dir is not None else args.spec.parent
    earliest_match_date = args.earliest_match_date or date.today()
    solution_path = solve(
        args.spec, output_dir, earliest_match_date=earliest_match_date
    )

    print(f"Wrote solution to {solution_path}")


if __name__ == "__main__":
    main()
