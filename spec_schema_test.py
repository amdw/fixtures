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

"""Checks that spec-schema.json stays in sync with reality: that it's a valid JSON
Schema, and that it accepts real and representative spec files. It can't catch every
possible way the schema might drift from fixturespec.py's own (independent)
validation, but it does catch the schema going stale as spec files evolve.
"""

import datetime
import json
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import jsonschema
import yaml

import fixturespec
import fixturespec_test

_SCHEMA_PATH = Path(__file__).parent / "spec-schema.json"

# Exercises every top-level and nested section of the schema, so it doubles as a
# drift check that new fields added to one aren't forgotten in the other.
_FULL_EXAMPLE = """
name: "2025-26 Season"
draft: false
description: "Final schedule; refer to ECF LMS for authoritative dates."
latest_internal_match_date: 2025-12-31

clubs:
  albany:
    name: Albany
    home_venue_name: Albany Sports Hall
    home_venue_address: 1 Sports Hall Road, London N1 1AA
    home_start_time: "19:30"
    home_time_limit: "75+15"
  hackney:
    name: Hackney
    home_venue_name: Hackney Community Centre
    home_venue_address: 2 Community Lane, London E8 2BB
    home_start_time: "19:00"
    home_time_limit: "60+15"

teams:
  albany-1:
    club: albany
    index: 1
  hackney-1:
    club: hackney
    index: 1
  hackney-5:
    club: hackney
    index: 5
    name_override: "Hackney Herons"

divisions:
  1:
    scheme: double_round
    teams: [albany-1, hackney-1]
  3:
    scheme: single_round
    teams: [hackney-5]

club_constraints:
  defaults:
    match_count_limits:
      - override_key: weekly-gap
        apply_per: each_team
        time_window_days: 7
        max: 1
      - override_key: venue-capacity
        venue_scope: home
        max: 2
      - override_key: no-play-dates
        max: 0
        date_ranges:
          - start_date: 2025-12-22
            end_date: 2026-01-04

  albany:
    home_dates: [2025-09-01, 2025-09-15, 2025-09-29]
    unavailable_away_dates: [2025-12-25]
    latest_match_date: 2026-04-30
    match_count_limits:
      - override_key: weekly-gap
        apply_per: each_team
        time_window_days: 14
        max: 1
      - override_key: venue-capacity
        venue_scope: home
        max: 3

  hackney:
    home_dates: [2025-09-08, 2025-09-22]
    match_count_limits:
      - override_key: venue-capacity
        venue_scope: home
        max: 2
        date_max_overrides:
          2025-09-08: 3
      - max: null
        venue_scope: all
        date_max_overrides:
          2025-09-22: 1
      - teams: [hackney-1, hackney-5]
        time_window_days: 7
        max: 1
        venue_scope: away
      - teams: [hackney-1, hackney-5]
        max: 1
        date_ranges:
          - start_date: 2025-10-20
            end_date: 2025-10-26
          - start_date: 2026-02-16
            end_date: 2026-02-22
    home_dates_used:
      min: 1
      max: 2
    teams:
      hackney-5:
        unavailable_home_dates: [2025-09-08]
        unavailable_away_dates: [2025-09-22]

fixed_fixtures:
  - home: albany-1
    away: hackney-1
    date: 2025-09-01

exclude_fixtures:
  clubs: []
  teams: []
  fixtures:
    - home: hackney-1
      away: albany-1
"""


def _stringify_dates(value: Any) -> Any:
    """Recursively replace datetime.date values with ISO8601 strings, and int-like
    mapping keys with strings, so that PyYAML's parsed output (which turns unquoted
    yyyy-mm-dd scalars into date objects, and unquoted integer keys into ints) is
    JSON-compatible for jsonschema validation."""
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _stringify_dates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify_dates(v) for v in value]
    return value


class SpecSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        with _SCHEMA_PATH.open() as f:
            self.schema = json.load(f)

    def test_schema_itself_is_valid(self) -> None:
        jsonschema.Draft202012Validator.check_schema(self.schema)

    def _assert_valid(self, yaml_text: str) -> None:
        data = _stringify_dates(yaml.safe_load(yaml_text))
        validator = jsonschema.Draft202012Validator(
            self.schema, format_checker=jsonschema.FormatChecker()
        )
        errors = sorted(validator.iter_errors(data), key=str)
        self.assertEqual([], errors)

    def test_full_example_is_valid(self) -> None:
        self._assert_valid(_FULL_EXAMPLE)

    def test_committed_example_run_is_valid(self) -> None:
        spec_path = Path(__file__).parent / "runs" / "example" / "spec.yaml"
        self._assert_valid(spec_path.read_text())


class SchemaAcceptsEveryValidFixtureSpecTest(unittest.TestCase):
    """Runs fixturespec_test.py's own suite, capturing every spec file its
    'this should parse successfully' tests feed to fixturespec.load_spec(), and
    checks the schema accepts every one of them too.

    This is deliberately not wired into fixturespec.load_spec() itself: a bug in
    the (independently maintained) schema would then start rejecting genuine spec
    files at runtime, rather than just leaving the docs stale. Catching drift here,
    against the much wider variety of valid specs fixturespec_test.py already
    covers, gets the same protection without that risk.
    """

    def test_schema_accepts_every_spec_fixturespec_test_considers_valid(self) -> None:
        collected: list[tuple[str, Any]] = []
        real_load_spec = fixturespec.load_spec

        def recording_load_spec(spec_path: str | Path) -> fixturespec.Spec:
            spec = real_load_spec(
                spec_path
            )  # raises if fixturespec_test.py expects failure
            with Path(spec_path).open() as f:
                collected.append((str(spec_path), yaml.safe_load(f)))
            return spec

        with mock.patch.object(
            fixturespec, "load_spec", side_effect=recording_load_spec
        ):
            suite = unittest.TestLoader().loadTestsFromModule(fixturespec_test)
            result = unittest.TestResult()
            suite.run(result)

        self.assertTrue(
            result.wasSuccessful(),
            "fixturespec_test.py failed while collecting specs for the schema "
            f"drift check: {result.errors + result.failures}",
        )
        self.assertGreater(
            len(collected), 0, "expected fixturespec_test.py to exercise load_spec()"
        )

        with _SCHEMA_PATH.open() as f:
            schema = json.load(f)
        validator = jsonschema.Draft202012Validator(
            schema, format_checker=jsonschema.FormatChecker()
        )
        failures = {}
        for spec_path, data in collected:
            errors = sorted(validator.iter_errors(_stringify_dates(data)), key=str)
            if errors:
                failures[spec_path] = errors
        self.assertEqual({}, failures)


if __name__ == "__main__":
    unittest.main()
