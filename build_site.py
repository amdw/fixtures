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

"""Assemble the published site under _site/ from each run's committed spec + solution.

For each directory anywhere under runs/ that holds both a spec.yaml and a
solution.yaml, this renders that run's report files (all-matches.html,
division-<n>.html, club-<id>.html, the run's index.html, and the all-matches.csv
/ all-matches-by-team.csv exports) into the matching path under _site/runs/; it
then writes the top-level _site/index.html linking them all. Together with the
schema reference page (build_schema_docs.py writes it into the same _site/),
that's the entire site the Pages workflow deploys -- it just uploads _site/
as-is, with no copy step -- so nothing under _site/ needs to be committed. See
.github/workflows/pages.yml, which runs this before every deploy.

It renders each run from source via report.py, so it needs the same
dependencies (pyyaml, and ortools via fmodel). It does *not* re-run the solver:
it only turns each committed solution.yaml back into a report, so it stays fast
enough to run on every deploy.
"""

from __future__ import annotations

import argparse
import shutil
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


def build_site(runs_dir: Path, out_dir: Path) -> None:
    """Render every run under runs_dir into out_dir/runs/, mirroring runs_dir's own
    folder structure, then (re)write out_dir/index.html linking them all."""
    out_runs_dir = out_dir / "runs"
    # Rebuild the runs tree from scratch so a run removed from runs_dir doesn't
    # linger in a local out_dir (CI always builds from a fresh checkout).
    shutil.rmtree(out_runs_dir, ignore_errors=True)

    run_dirs = find_run_specs(runs_dir)
    for run_dir in run_dirs:
        dest = out_runs_dir / run_dir.relative_to(runs_dir)
        report.report(run_dir / SPEC_FILENAME, run_dir / SOLUTION_FILENAME, dest)
        print(f"Rebuilt report for {run_dir} -> {dest}")

    out_dir.mkdir(parents=True, exist_ok=True)
    root_index = out_dir / "index.html"
    htmlreport.write_runs_index(out_runs_dir, root_index)

    print(f"Rebuilt {len(run_dirs)} run report(s) into {out_runs_dir}")
    print(f"Rebuilt top-level index at {root_index}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("runs"),
        help="Source directory containing one sub-folder per run (default: runs)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("_site"),
        help="Directory to assemble the site into (default: _site)",
    )
    args = parser.parse_args()

    build_site(args.runs_dir, args.out_dir)


if __name__ == "__main__":
    main()
