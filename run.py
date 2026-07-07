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

"""Solve a YAML fixture specification and write its HTML report into a run folder.

Usage:
    python run.py <spec.yaml> <output_dir>

<output_dir> should normally live under runs/ (e.g. runs/2025-26-season) so
that it is picked up by the top-level runs index used for GitHub Pages. The
usual layout is to keep the spec file inside that same folder (e.g.
runs/2025-26-season/spec.yaml) and pass it as both input and output location.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import fixturespec
import fmodel
import htmlreport


def run(spec_path: Path, output_dir: Path) -> Path:
    """Solve the given spec and write its report into output_dir. Returns the run's index.html path."""
    spec = fixturespec.load_spec(spec_path)
    fixtures = fmodel.solve(spec.parameters)
    return htmlreport.generate_report(
        fixtures, spec.parameters.teams, spec.clubs, output_dir
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "spec", type=Path, help="Path to the YAML fixture specification"
    )
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

    run_index_path = run(args.spec, args.output_dir)
    root_index_path = htmlreport.write_runs_index(args.runs_dir, args.root_index)

    print(f"Wrote run report to {run_index_path}")
    print(f"Updated top-level index at {root_index_path}")


if __name__ == "__main__":
    main()
