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

"""Test cases for fmodel.check_schedule and the validate CLI."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

import fixturesolution
import fixturespec
import fmodel
import solve
import validate

_A1 = fmodel.Team(division=1, club="a", index=1)
_B1 = fmodel.Team(division=1, club="b", index=1)
# A lone team in its own division: it generates no required fixtures, so a
# fixture naming it is never a required one.
_C1 = fmodel.Team(division=2, club="c", index=1)

_D1 = date(2025, 9, 1)
_D2 = date(2025, 9, 8)
_D3 = date(2025, 9, 15)
_D4 = date(2025, 9, 22)


def _params(**overrides: object) -> fmodel.Parameters:
    kwargs: dict[str, object] = {
        "teams": [_A1, _B1, _C1],
        "home_dates": {"a": [_D1, _D2], "b": [_D3, _D4], "c": [_D1]},
        "unavailable_away_dates": {},
    }
    kwargs.update(overrides)
    return fmodel.Parameters(**kwargs)  # type: ignore[arg-type]


def _sf(home: fmodel.Team, away: fmodel.Team, d: date) -> fmodel.ScheduledFixture:
    return fmodel.ScheduledFixture(
        fixture=fmodel.Fixture(home_team=home, away_team=away), date=d
    )


_VALID = [_sf(_A1, _B1, _D1), _sf(_B1, _A1, _D3)]


_INCONSISTENT = "inconsistent with the spec's constraints"
_NOT_A_SLOT = "is not a slot this spec can schedule"


class TestCheckSchedule(unittest.TestCase):
    def test_valid_schedule_has_no_problems(self) -> None:
        self.assertEqual(fmodel.check_schedule(_params(), _VALID), [])

    def test_infeasible_spec_is_reported(self) -> None:
        # a and b have no overlapping schedulable date for one direction: a's only
        # home date is also b's only unavailable away date.
        params = _params(
            home_dates={"a": [_D1], "b": [_D3, _D4], "c": [_D1]},
            unavailable_away_dates={"b": [_D1]},
        )
        problems = fmodel.check_schedule(params, _VALID)
        self.assertEqual(len(problems), 1)
        self.assertIn("no feasible schedule at all", problems[0])

    def test_missing_required_fixture_is_inconsistent(self) -> None:
        problems = fmodel.check_schedule(_params(), [_sf(_A1, _B1, _D1)])
        self.assertEqual(
            problems,
            [f"the schedule is {_INCONSISTENT} (solver status: INFEASIBLE)"],
        )

    def test_duplicate_fixture_is_inconsistent(self) -> None:
        problems = fmodel.check_schedule(
            _params(),
            [_sf(_A1, _B1, _D1), _sf(_A1, _B1, _D2), _sf(_B1, _A1, _D3)],
        )
        self.assertEqual(len(problems), 1)
        self.assertIn(_INCONSISTENT, problems[0])

    def test_exact_duplicate_entry_is_harmless(self) -> None:
        # The same (fixture, date) listed twice is a redundant entry, not a
        # double-booking -- it should still validate.
        self.assertEqual(
            fmodel.check_schedule(_params(), [*_VALID, _sf(_A1, _B1, _D1)]), []
        )

    def test_fixture_not_paired_in_a_division_is_not_a_slot(self) -> None:
        problems = fmodel.check_schedule(_params(), [*_VALID, _sf(_A1, _C1, _D1)])
        self.assertEqual(problems, [f"a 1 vs c 1 on 2025-09-01 {_NOT_A_SLOT}"])

    def test_excluded_fixture_is_not_a_slot(self) -> None:
        params = _params(
            excluded_fixtures=[fmodel.Fixture(home_team=_B1, away_team=_A1)]
        )
        problems = fmodel.check_schedule(params, _VALID)
        self.assertEqual(problems, [f"b 1 vs a 1 on 2025-09-15 {_NOT_A_SLOT}"])

    def test_fixture_on_non_home_date_is_not_a_slot(self) -> None:
        problems = fmodel.check_schedule(
            _params(), [_sf(_A1, _B1, date(2025, 9, 2)), _sf(_B1, _A1, _D3)]
        )
        self.assertEqual(problems, [f"a 1 vs b 1 on 2025-09-02 {_NOT_A_SLOT}"])

    def test_fixture_on_unavailable_away_date_is_not_a_slot(self) -> None:
        params = _params(unavailable_away_dates={"b": [_D1]})
        problems = fmodel.check_schedule(
            params, [_sf(_A1, _B1, _D1), _sf(_B1, _A1, _D3)]
        )
        self.assertEqual(problems, [f"a 1 vs b 1 on 2025-09-01 {_NOT_A_SLOT}"])

    def test_fixture_before_earliest_match_date_is_not_a_slot(self) -> None:
        params = _params(earliest_match_date=_D2)
        problems = fmodel.check_schedule(params, _VALID)
        self.assertEqual(problems, [f"a 1 vs b 1 on 2025-09-01 {_NOT_A_SLOT}"])

    def test_match_count_limit_violation_is_inconsistent(self) -> None:
        # At most one match involving a-1 or b-1 in any 7-day window. A valid
        # schedule exists (their two fixtures 14 days apart); this one bunches
        # them 3 days apart, which the model rejects.
        params = fmodel.Parameters(
            teams=[_A1, _B1],
            home_dates={
                "a": [_D1, date(2025, 9, 8)],
                "b": [date(2025, 9, 4), _D4],
            },
            unavailable_away_dates={},
            match_count_limits=[
                fmodel.MatchCountLimit(
                    teams=[_A1, _B1], max_matches=1, time_window_days=7
                )
            ],
        )
        bad = [_sf(_A1, _B1, _D1), _sf(_B1, _A1, date(2025, 9, 4))]
        problems = fmodel.check_schedule(params, bad)
        self.assertEqual(len(problems), 1)
        self.assertIn(_INCONSISTENT, problems[0])
        # And the same schedule with the fixtures spaced out is fine.
        self.assertEqual(
            fmodel.check_schedule(
                params, [_sf(_A1, _B1, date(2025, 9, 8)), _sf(_B1, _A1, _D4)]
            ),
            [],
        )

    def test_two_teams_on_one_date_is_inconsistent(self) -> None:
        params = _params(home_dates={"a": [_D1, _D2], "b": [_D1], "c": [_D1]})
        problems = fmodel.check_schedule(
            params, [_sf(_A1, _B1, _D1), _sf(_B1, _A1, _D1)]
        )
        self.assertEqual(len(problems), 1)
        self.assertIn(_INCONSISTENT, problems[0])

    def test_home_dates_used_bound_violation_is_inconsistent(self) -> None:
        # b hosts only one match, so it can never use two home dates: a
        # home_dates_used minimum of 2 for b makes the schedule non-compliant.
        params = _params(home_dates_used={"b": fmodel.HomeDatesUsedBounds(minimum=2)})
        problems = fmodel.check_schedule(params, _VALID)
        self.assertEqual(len(problems), 1)
        self.assertIn(_INCONSISTENT, problems[0])


class TestMatchesExpectation(unittest.TestCase):
    def test_valid_and_not_expected_invalid_matches(self) -> None:
        report = validate.ValidationReport(problems=[])
        self.assertTrue(report.matches_expectation)

    def test_invalid_and_expected_invalid_matches(self) -> None:
        report = validate.ValidationReport(
            problems=["boom"], expected_invalid_reason="known bug"
        )
        self.assertTrue(report.matches_expectation)

    def test_invalid_but_not_expected_invalid_is_a_mismatch(self) -> None:
        report = validate.ValidationReport(problems=["boom"])
        self.assertFalse(report.matches_expectation)

    def test_valid_but_expected_invalid_is_a_mismatch(self) -> None:
        report = validate.ValidationReport(
            problems=[], expected_invalid_reason="known bug"
        )
        self.assertFalse(report.matches_expectation)


_SPEC = """
name: "Validate Test Season"
earliest_match_date: 2025-01-01

clubs:
  albany:
    name: Albany
    home_venue_name: Albany Sports Hall
    home_venue_address: 1 Albany Road, London
    home_start_time: "19:30"
    home_time_limit: "75+15"
  hackney:
    name: Hackney
    home_venue_name: Hackney Community Centre
    home_venue_address: 2 Hackney Road, London
    home_start_time: "19:00"
    home_time_limit: "60+15"

teams:
  albany-1:
    club: albany
    index: 1
  hackney-1:
    club: hackney
    index: 1

divisions:
  1:
    scheme: double_round
    teams: [albany-1, hackney-1]

club_constraints:
  defaults:
    match_count_limits:
      - override_key: venue-capacity
        venue_scope: home
        max_matches: 1
  albany:
    home_dates: [2025-09-01, 2025-09-29]
  hackney:
    home_dates: [2025-09-15]
"""


class TestValidateCli(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.dir = Path(self._tmpdir.name)
        self.spec_path = self.dir / "spec.yaml"
        self.spec_path.write_text(_SPEC)
        self.solution_path = solve.solve(self.spec_path, self.dir)

    def test_freshly_solved_solution_validates(self) -> None:
        report = validate.validate(self.spec_path, self.solution_path)
        self.assertTrue(report.ok, report.problems)
        self.assertIsNone(report.checksum_note)

    def test_tampered_date_fails_validation(self) -> None:
        text = self.solution_path.read_text()
        tampered = text.replace("date: 2025-09-15", "date: 2025-09-16")
        self.assertNotEqual(text, tampered)
        self.solution_path.write_text(tampered)

        report = validate.validate(self.spec_path, self.solution_path)
        self.assertFalse(report.ok)
        self.assertTrue(
            any("is not a slot this spec can schedule" in p for p in report.problems),
            report.problems,
        )

    def test_missing_fixture_fails_validation(self) -> None:
        data = fixturesolution.load_solution(
            self.solution_path,
            fixturespec.load_spec(self.spec_path).parameters.teams,
            fixturespec.load_team_ids(self.spec_path),
        )
        kept = data.fixtures[:1]
        self.solution_path.write_text(
            "fixtures:\n"
            + "".join(
                f"- home: {sf.fixture.home_team.club}-{sf.fixture.home_team.index}\n"
                f"  away: {sf.fixture.away_team.club}-{sf.fixture.away_team.index}\n"
                f"  date: {sf.date.isoformat()}\n"
                for sf in kept
            )
        )
        report = validate.validate(self.spec_path, self.solution_path)
        self.assertFalse(report.ok)
        self.assertTrue(
            any("inconsistent with the spec" in p for p in report.problems),
            report.problems,
        )

    def test_checksum_note_when_spec_reformatted(self) -> None:
        self.spec_path.write_text(_SPEC + "\n# a trailing comment changes the bytes\n")
        report = validate.validate(self.spec_path, self.solution_path)
        self.assertIsNotNone(report.checksum_note)
        self.assertIn("different spec", report.checksum_note or "")
        # Reformatting doesn't change the schedule's validity.
        self.assertTrue(report.ok, report.problems)

    def test_expected_invalid_reason_matches_when_solution_actually_invalid(
        self,
    ) -> None:
        text = self.solution_path.read_text()
        tampered = (
            text.replace("date: 2025-09-15", "date: 2025-09-16")
            + "expected_invalid_reason: kept to pin a known issue\n"
        )
        self.solution_path.write_text(tampered)

        report = validate.validate(self.spec_path, self.solution_path)
        self.assertFalse(report.ok)
        self.assertTrue(report.matches_expectation)

    def test_expected_invalid_reason_mismatch_when_solution_still_valid(self) -> None:
        with self.solution_path.open("a") as f:
            f.write("expected_invalid_reason: no longer reproduces\n")

        report = validate.validate(self.spec_path, self.solution_path)
        self.assertTrue(report.ok)
        self.assertFalse(report.matches_expectation)
        self.assertEqual(report.expected_invalid_reason, "no longer reproduces")

    def test_unreadable_solution_raises_solution_error(self) -> None:
        self.solution_path.write_text("not: a valid solution\n")
        with self.assertRaises(fixturesolution.SolutionError):
            validate.validate(self.spec_path, self.solution_path)


if __name__ == "__main__":
    unittest.main()
