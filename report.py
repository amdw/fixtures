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

"""Render a solution.yaml (as written by solve.py), plus its original spec (for
team/club/exclusion metadata), as a set of HTML report pages.

Usage:
    python report.py <spec.yaml> <solution.yaml> <output_dir>

This can be re-run at any time to regenerate the HTML report -- e.g. after
changing report formatting -- without re-solving, as long as solution.yaml
still matches the teams described in spec.yaml. run.py runs solve.py and
report.py together in one step.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import fixturesolution
import fixturespec
import htmlreport


def report(spec_path: Path, solution_path: Path, output_dir: Path) -> Path:
    """Render solution_path (a solution.yaml solved from spec_path) into output_dir.
    Returns the path to the run's index.html."""
    spec = fixturespec.load_spec(spec_path)
    team_ids = fixturespec.load_team_ids(spec_path)
    fixtures = fixturesolution.load_solution(
        solution_path, spec.parameters.teams, team_ids
    )
    return htmlreport.generate_report(
        fixtures,
        spec.parameters.teams,
        spec.clubs,
        output_dir,
        excluded_fixtures=spec.parameters.excluded_fixtures,
        name=spec.name,
        draft=spec.draft,
        description=spec.description,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "spec", type=Path, help="Path to the YAML fixture specification"
    )
    parser.add_argument("solution", type=Path, help="Path to the solved solution.yaml")
    parser.add_argument(
        "output_dir", type=Path, help="Directory to write this run's HTML report into"
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("runs"),
        help="Directory scanned to rebuild the top-level runs index (default: runs)",
    )
    parser.add_argument(
        "--root-index",
        type=Path,
        default=Path("index.html"),
        help="Path of the top-level index.html to (re)generate (default: index.html)",
    )
    args = parser.parse_args()

    run_index_path = report(args.spec, args.solution, args.output_dir)
    root_index_path = htmlreport.write_runs_index(args.runs_dir, args.root_index)

    print(f"Wrote run report to {run_index_path}")
    print(f"Updated top-level index at {root_index_path}")


if __name__ == "__main__":
    main()
