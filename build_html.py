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

"""Regenerate every HTML page under runs/ from the committed spec + solution.yaml.

For each directory anywhere under runs/ that holds both a spec.yaml and a
solution.yaml, this re-renders the report pages (all-matches.html,
division-<n>.html, club-<id>.html and the run's index.html); it then rebuilds the
top-level runs index.html. That's the complete set of HTML the Pages deploy
serves from runs/ (plus the root index.html), so none of it needs to be
committed. See .github/workflows/pages.yml, which runs this before every deploy.

It renders the report pages from source via report.py, so it needs the same
dependencies (pyyaml, and ortools via fmodel). It does *not* re-run the solver:
it only turns each committed solution.yaml back into HTML, so it stays fast
enough to run on every deploy.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import htmlreport
import report

SPEC_FILENAME = "spec.yaml"
SOLUTION_FILENAME = "solution.yaml"


def find_run_specs(runs_dir: Path) -> list[Path]:
    """Every directory anywhere under runs_dir (at any depth) that has both a
    spec.yaml and a solution.yaml -- i.e. a run whose report can be regenerated
    from source."""
    if not runs_dir.is_dir():
        return []
    return sorted(
        p.parent
        for p in runs_dir.rglob(SOLUTION_FILENAME)
        if (p.parent / SPEC_FILENAME).is_file()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("runs"),
        help="Directory containing one sub-folder per run (default: runs)",
    )
    parser.add_argument(
        "--root-index",
        type=Path,
        default=Path("index.html"),
        help="Path of the top-level index.html to (re)generate (default: index.html)",
    )
    args = parser.parse_args()

    run_dirs = find_run_specs(args.runs_dir)
    for run_dir in run_dirs:
        report.report(run_dir / SPEC_FILENAME, run_dir / SOLUTION_FILENAME, run_dir)
        print(f"Rebuilt report for {run_dir}")

    htmlreport.write_runs_index(args.runs_dir, args.root_index)

    print(f"Rebuilt {len(run_dirs)} run report(s) under {args.runs_dir}")
    print(f"Rebuilt top-level index at {args.root_index}")


if __name__ == "__main__":
    main()
