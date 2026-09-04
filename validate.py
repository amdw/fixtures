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

"""Check whether a solution.yaml complies with a spec.yaml.

Usage:
    python validate.py <spec.yaml> <solution.yaml>

Loads the spec and the solution and reports whether the solution is a valid
solved schedule for that spec: exactly the required fixtures of every division,
each on a date the constraint model accepts, with every constraint satisfied
(one match per team per day, home_dates_used bounds, match_count_limits,
fixed_fixtures). Exits 0 if it complies, 1 if it doesn't -- or if either file
can't be read.

The two intended uses:

- After changing the solver/model implementation, re-run this over the committed
  runs to confirm their existing solution.yaml files still validate -- i.e. the
  change didn't alter the semantics.
- After changing a constraint spec, run this with the *new* spec against an
  *old* solution to see whether that schedule would still comply. Pointing the
  tool at a spec other than the one the solution was solved from is expected and
  supported; a spec_checksum recorded in the solution that doesn't match the
  given spec is reported for information only and is not itself non-compliance.

The check is built on the same model fmodel.solve() uses (see
fmodel.check_schedule): it pins each candidate fixture/date variable to the
value the solution implies and asks the solver whether the model is still
satisfied, so it tracks the solver's semantics rather than re-stating them. The
trade-off is that any breach other than "this fixture can't be scheduled there
at all" is reported just as an inconsistency, without saying which rule.

If the solution declares an expected_invalid_reason (see fixturesolution), it's
expected to fail validation -- ValidationReport.matches_expectation compares
that expectation against what actually happened, so a solution flagged as a
known exception that now validates fine (or vice versa) is reported too, even
though its raw compliance status alone wouldn't say anything unusual happened.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import fixturesolution
import fixturespec
import fmodel


@dataclasses.dataclass(frozen=True)
class ValidationReport:
    """The outcome of validating one solution against one spec."""

    problems: list[str]
    checksum_note: str | None = None
    expected_invalid_reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def matches_expectation(self) -> bool:
        """True unless the solution's own expected_invalid_reason disagrees with
        what actually happened: it's marked as a known exception that in fact
        validates fine, or it's not marked as one but fails validation anyway.
        Consulted by the regression test (validation_regression_test.py) and by
        report.py's compliance banner; validate.py's own exit code still reflects
        plain compliance (report.ok), since that's what running it ad hoc asks."""
        return self.ok == (not self.expected_invalid_reason)


def _checksum_note(spec_path: Path, result: fmodel.SolveResult) -> str | None:
    """A note if the solution records a spec checksum that isn't the given spec's
    -- meaning it's being validated against a different (or reformatted) spec than
    it was solved from. That's a supported thing to do on purpose, so it's a note,
    not a problem."""
    recorded = result.spec_checksum
    if not recorded:
        return None
    actual = fixturespec.spec_checksum(spec_path)
    if actual == recorded:
        return None
    return (
        f"solution records spec_checksum {recorded}, but {spec_path} hashes to "
        f"{actual} -- validating against a different spec than it was solved from"
    )


def validate(spec_path: Path, solution_path: Path) -> ValidationReport:
    """Validate the solution at solution_path against the spec at spec_path."""
    spec = fixturespec.load_spec(spec_path)
    team_ids = fixturespec.load_team_ids(spec_path)
    result = fixturesolution.load_solution(
        solution_path, spec.parameters.teams, team_ids
    )
    return ValidationReport(
        problems=fmodel.check_schedule(spec.parameters, result.fixtures),
        checksum_note=_checksum_note(spec_path, result),
        expected_invalid_reason=result.expected_invalid_reason,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "spec", type=Path, help="Path to the YAML fixture specification"
    )
    parser.add_argument(
        "solution", type=Path, help="Path to the solution.yaml to check"
    )
    args = parser.parse_args()

    try:
        report = validate(args.spec, args.solution)
    except (fixturespec.SpecError, fixturesolution.SolutionError) as e:
        print(f"Could not validate: {e}")
        sys.exit(1)

    if report.checksum_note:
        print(f"Note: {report.checksum_note}")

    if report.expected_invalid_reason and report.ok:
        # The unexpected direction: marked as a known exception, but it now
        # validates fine -- worth a nudge even though nothing below will say so.
        print(
            f"Note: this solution is marked expected_invalid_reason="
            f"{report.expected_invalid_reason!r}, but it currently validates -- "
            "the reason may no longer apply."
        )

    if report.ok:
        print(f"{args.solution} complies with {args.spec}")
        sys.exit(0)

    print(f"{args.solution} does NOT comply with {args.spec}:")
    for problem in report.problems:
        print(f"- {problem}")
    sys.exit(1)


if __name__ == "__main__":
    main()
