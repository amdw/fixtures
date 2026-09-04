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

"""Golden-master coverage for fmodel's constraint semantics.

Every spec.yaml + solution.yaml pair under runs/ (the real, published runs) and
validation_fixtures/ (synthetic cases kept around purely to pin a specific
solver bug or edge case -- never published, see build_site.py) is checked with
validate.py, and must match its solution's own expected_invalid_reason:
unset, it must validate; set, it must fail validation, for that reason.

This is deliberately not "every committed spec must validate" -- a solution can
be kept around, and marked expected_invalid_reason, specifically because it's
*not* expected to validate (e.g. a schedule kept for reference after tightening
a constraint, or one that reproduces a known solver bug). What this test does
enforce is that the *recorded* expectation and the *actual* outcome agree. A
change to fmodel.py that flips either direction fails here, forcing an
explicit, reviewable decision: fix a regression, or update the affected
solution's expected_invalid_reason -- itself a change a reviewer will see.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import build_site
import validate

_ROOT = Path(__file__).resolve().parent
_CORPUS_DIRS = ("runs", "validation_fixtures")


def _cases() -> list[Path]:
    cases = []
    for name in _CORPUS_DIRS:
        cases.extend(build_site.find_run_specs(_ROOT / name))
    return cases


class TestValidationRegression(unittest.TestCase):
    def test_every_committed_solution_matches_its_expectation(self) -> None:
        cases = _cases()
        self.assertTrue(cases, "no spec.yaml/solution.yaml pairs found to check")

        failures = []
        for run_dir in cases:
            report = validate.validate(run_dir / "spec.yaml", run_dir / "solution.yaml")
            if report.matches_expectation:
                continue
            if report.expected_invalid_reason:
                failures.append(
                    f"{run_dir}: marked expected_invalid_reason="
                    f"{report.expected_invalid_reason!r}, but it currently "
                    "validates -- if the code change that did this is correct, "
                    "clear expected_invalid_reason in its solution.yaml"
                )
            else:
                failures.append(
                    f"{run_dir}: no longer validates against its own spec "
                    f"({'; '.join(report.problems)}) -- if this is a deliberate, "
                    "known exception, set expected_invalid_reason in its "
                    "solution.yaml to say why; otherwise this is a regression"
                )
        self.assertEqual(failures, [], "\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
