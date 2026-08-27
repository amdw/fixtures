#!/usr/bin/env python3
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

"""Render spec-schema.json to a static HTML reference page.

This needs a third-party dependency (json-schema-for-humans), so
.github/workflows/pages.yml installs the dev dependencies before running it. Run
it locally with `uv run python3 build_schema_docs.py` any time you want to
preview the reference page without going through a full Pages deploy.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from json_schema_for_humans.generate import generate_from_filename
from json_schema_for_humans.generation_configuration import GenerationConfiguration


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path("spec-schema.json"),
        help="Path of the JSON Schema to render (default: spec-schema.json)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("schema-docs/spec-schema.html"),
        help="Path of the HTML page to (re)generate (default: schema-docs/spec-schema.html)",
    )
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    config = GenerationConfiguration(show_breadcrumbs=False, with_footer=False)
    generate_from_filename(args.schema, str(args.out), config=config)

    print(f"Rendered {args.schema} to {args.out}")


if __name__ == "__main__":
    main()
