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

"""Rebuild the top-level and per-run index.html pages from whatever report files
are present under runs/.

This has no third-party dependencies (unlike run.py, it doesn't need ortools or
pyyaml), so it's cheap to run in CI -- see .github/workflows/pages.yml, which
runs it before every Pages deploy so index pages never need to be committed or
hand-maintained.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import htmlreport


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

    rebuilt = []
    if args.runs_dir.is_dir():
        for run_dir in sorted(p for p in args.runs_dir.iterdir() if p.is_dir()):
            if (run_dir / "all-matches.html").exists():
                htmlreport.build_run_index(run_dir)
                rebuilt.append(run_dir)

    htmlreport.write_runs_index(args.runs_dir, args.root_index)

    print(f"Rebuilt {len(rebuilt)} run index page(s) under {args.runs_dir}")
    print(f"Rebuilt top-level index at {args.root_index}")


if __name__ == "__main__":
    main()
