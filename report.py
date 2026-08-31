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
team/club/exclusion metadata), as the HTML report pages and CSV exports for one
run.

Usage:
    python report.py <spec.yaml> <solution.yaml> <output_dir>

This can be re-run at any time to regenerate the report -- e.g. after changing
report formatting -- without re-solving, as long as solution.yaml still matches
the teams described in spec.yaml.

It renders a single run's files only. To (re)build the whole published site --
every run's report plus the top-level index -- into _site/, run build_site.py.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import csvreport
import fixturesolution
import fixturespec
import fmodel
import htmlreport

logger = logging.getLogger(__name__)


def _check_spec_checksum(spec_path: Path, result: fmodel.SolveResult) -> None:
    """Warn if the solution records a spec checksum that doesn't match the spec
    we've been handed -- i.e. this report is being built from a different (or
    reformatted) spec than the one the schedule was solved from."""
    recorded = result.spec_checksum
    if not recorded:
        return
    actual = fixturespec.spec_checksum(spec_path)
    if actual != recorded:
        logger.warning(
            "Spec checksum mismatch: the solution was solved from a spec hashing "
            "to %s, but %s hashes to %s -- this report may not match the spec the "
            "schedule was solved from.",
            recorded,
            spec_path,
            actual,
        )


def report(spec_path: Path, solution_path: Path, output_dir: Path) -> Path:
    """Render solution_path (a solution.yaml solved from spec_path) into output_dir,
    as the HTML report pages and the CSV exports. Returns the path to the run's
    index.html."""
    spec = fixturespec.load_spec(spec_path)
    team_ids = fixturespec.load_team_ids(spec_path)
    result = fixturesolution.load_solution(
        solution_path, spec.parameters.teams, team_ids
    )
    _check_spec_checksum(spec_path, result)
    csvreport.generate_csv(spec, result.fixtures, output_dir)
    return htmlreport.generate_report(spec, result, output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "spec", type=Path, help="Path to the YAML fixture specification"
    )
    parser.add_argument("solution", type=Path, help="Path to the solved solution.yaml")
    parser.add_argument(
        "output_dir", type=Path, help="Directory to write this run's HTML report into"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    run_index_path = report(args.spec, args.solution, args.output_dir)

    print(f"Wrote run report (HTML and CSV) to {run_index_path.parent}")


if __name__ == "__main__":
    main()
