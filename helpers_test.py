# Copyright 2025 Andrew Medworth
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

"""Test cases for helper functions (e.g. date generation)."""

import calendar
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from hypothesis import assume, given
from hypothesis import strategies as st

import fmodel
import genfixtures


class TestGenDates(unittest.TestCase):
    """Test cases for gen_dates function."""

    def test_basic_weekday_generation(self):
        """Test generating all Mondays in a range."""
        start = date(2025, 1, 6)  # Monday
        end = date(2025, 1, 20)  # Monday

        result = genfixtures.gen_dates(start, end, calendar.MONDAY)
        expected = [date(2025, 1, 6), date(2025, 1, 13), date(2025, 1, 20)]

        self.assertEqual(result, expected)

    def test_different_weekday(self):
        """Test generating Thursdays."""
        start = date(2025, 1, 1)  # Wednesday
        end = date(2025, 1, 31)  # Friday

        result = genfixtures.gen_dates(start, end, calendar.THURSDAY)
        expected = [
            date(2025, 1, 2),
            date(2025, 1, 9),
            date(2025, 1, 16),
            date(2025, 1, 23),
            date(2025, 1, 30),
        ]

        self.assertEqual(result, expected)

    def test_exclude_first_occurrence(self):
        """Test excluding first occurrence of each month."""
        start = date(2025, 1, 1)
        end = date(2025, 3, 31)

        result = genfixtures.gen_dates(
            start, end, calendar.MONDAY, exclude_month_occurrences=[1]
        )

        # Should exclude first Monday of each month
        # Jan: 6 (excluded), 13, 20, 27
        # Feb: 3 (excluded), 10, 17, 24
        # Mar: 3 (excluded), 10, 17, 24, 31
        expected = [
            date(2025, 1, 13),
            date(2025, 1, 20),
            date(2025, 1, 27),
            date(2025, 2, 10),
            date(2025, 2, 17),
            date(2025, 2, 24),
            date(2025, 3, 10),
            date(2025, 3, 17),
            date(2025, 3, 24),
            date(2025, 3, 31),
        ]

        self.assertEqual(result, expected)

    def test_exclude_multiple_occurrences(self):
        """Test excluding first and third occurrence of each month."""
        start = date(2025, 1, 1)
        end = date(2025, 2, 28)

        result = genfixtures.gen_dates(
            start, end, calendar.TUESDAY, exclude_month_occurrences=[1, 3]
        )

        # Jan Tuesdays: 7 (excluded), 14, 21 (excluded), 28
        # Feb Tuesdays: 4 (excluded), 11, 18 (excluded), 25
        expected = [
            date(2025, 1, 14),
            date(2025, 1, 28),
            date(2025, 2, 11),
            date(2025, 2, 25),
        ]

        self.assertEqual(result, expected)

    def test_single_day_range(self):
        """Test with start and end on same weekday."""
        start = date(2025, 1, 6)  # Monday
        end = date(2025, 1, 6)  # Same Monday

        result = genfixtures.gen_dates(start, end, calendar.MONDAY)
        expected = [date(2025, 1, 6)]

        self.assertEqual(result, expected)

    def test_no_matching_weekdays(self):
        """Test range with no matching weekdays."""
        start = date(2025, 1, 6)  # Monday
        end = date(2025, 1, 7)  # Tuesday

        result = genfixtures.gen_dates(start, end, calendar.WEDNESDAY)
        expected = []

        self.assertEqual(result, expected)

    @given(
        start_date=st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 1, 1)),
        days_span=st.integers(min_value=0, max_value=365),
        day_of_week=st.integers(min_value=0, max_value=6),
        exclude_occurrences=st.lists(st.integers(min_value=1, max_value=5), max_size=3),
    )
    def test_gen_dates_properties(
        self, start_date, days_span, day_of_week, exclude_occurrences
    ):
        """Test key properties of gen_dates output."""
        end_date = start_date + timedelta(days=days_span)

        result = genfixtures.gen_dates(
            start_date, end_date, day_of_week, exclude_occurrences
        )

        # Property 1: All dates in result have the correct weekday
        for d in result:
            self.assertEqual(d.weekday(), day_of_week)

        # Property 2: All dates are within the specified range
        for d in result:
            self.assertGreaterEqual(d, start_date)
            self.assertLessEqual(d, end_date)

        # Property 3: Result is sorted
        self.assertEqual(result, sorted(result))

        # Property 4: No duplicate dates
        self.assertEqual(len(result), len(set(result)))


class TestRemoveRandom(unittest.TestCase):
    """Test cases for remove_random function."""

    def setUp(self):
        super().setUp()

        # Set up the patch
        shuffle_patcher = patch("genfixtures.random.shuffle")
        self.mock_shuffle = shuffle_patcher.start()
        self.addCleanup(shuffle_patcher.stop)

        # Default to identity shuffle (no actual shuffling) for most tests
        self.mock_shuffle.side_effect = lambda x: None

    def test_remove_half(self):
        """Test removing 50% of dates."""
        dates = [
            date(2025, 1, 1),
            date(2025, 1, 8),
            date(2025, 1, 15),
            date(2025, 1, 22),
            date(2025, 1, 29),
        ]

        result = genfixtures.remove_random(dates, 0.5)

        # Should keep 50% of 5 dates = 2.5 -> 2 dates (int conversion)
        # With identity shuffle (no actual shuffling), keeps first 2 dates
        expected = [date(2025, 1, 1), date(2025, 1, 8)]
        self.assertEqual(result, expected)

    def test_remove_none(self):
        """Test removing 0% of dates (keep all)."""
        dates = [
            date(2025, 1, 1),
            date(2025, 1, 8),
            date(2025, 1, 15),
            date(2025, 1, 22),
            date(2025, 1, 29),
        ]

        result = genfixtures.remove_random(dates, 0.0)

        # Should keep all dates (100% - 0% = 100%)
        expected = dates  # Same order since no shuffling
        self.assertEqual(result, expected)

    def test_remove_all(self):
        """Test removing 100% of dates (keep none)."""
        dates = [
            date(2025, 1, 1),
            date(2025, 1, 8),
            date(2025, 1, 15),
            date(2025, 1, 22),
            date(2025, 1, 29),
        ]

        result = genfixtures.remove_random(dates, 1.0)

        self.assertEqual(len(result), 0)
        self.assertEqual(result, [])

    def test_shuffle_called(self):
        """Test that shuffle is actually called."""
        dates = [
            date(2025, 1, 1),
            date(2025, 1, 8),
            date(2025, 1, 15),
            date(2025, 1, 22),
            date(2025, 1, 29),
        ]

        genfixtures.remove_random(dates, 0.2)
        self.mock_shuffle.assert_called_once()

    def test_empty_input(self):
        """Test with empty date list."""
        result = genfixtures.remove_random([], 0.5)
        self.assertEqual(result, [])

    @given(
        dates_list=st.lists(
            st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 1, 1)),
            min_size=0,
            max_size=50,
            unique=True,
        ),
        fraction=st.floats(min_value=0.0, max_value=1.0),
    )
    def test_remove_random_properties(self, dates_list, fraction):
        """Test key properties of remove_random output."""
        # Sort input to make test deterministic
        dates_list = sorted(dates_list)

        result = genfixtures.remove_random(dates_list, fraction)

        # Property 1: Result is a subset of input
        for d in result:
            self.assertIn(d, dates_list)

        # Property 2: Result is sorted
        self.assertEqual(result, sorted(result))

        # Property 3: Result has correct length
        expected_length = int(len(dates_list) * (1 - fraction))
        self.assertEqual(len(result), expected_length)

        # Property 4: No duplicate dates
        self.assertEqual(len(result), len(set(result)))

        # Property 5: If fraction is 0, should return all dates
        if fraction == 0.0:
            self.assertEqual(result, dates_list)

        # Property 6: If fraction is 1, should return empty list
        if fraction == 1.0:
            self.assertEqual(result, [])


class TestDateWindows(unittest.TestCase):
    """Test cases for date_windows function."""

    def test_basic_windows(self):
        """Test basic window generation."""
        dates = [date(2025, 1, 1), date(2025, 1, 3), date(2025, 1, 8)]

        result = fmodel.date_windows(dates, window_days=5)

        # Window from Jan 1: {1, 3} (8 is 7 days away, > 5)
        # Window from Jan 3: {3, 8} (8 is 5 days away, <= 5)
        # Window from Jan 8: {8}
        # After removing subsets: {1, 3} and {3, 8}
        expected = [
            frozenset([date(2025, 1, 3), date(2025, 1, 8)]),
            frozenset([date(2025, 1, 1), date(2025, 1, 3)]),
        ]

        self.assertCountEqual(result, expected)

    def test_single_date(self):
        """Test with single date."""
        dates = [date(2025, 1, 1)]

        result = fmodel.date_windows(dates, window_days=7)
        expected = [frozenset([date(2025, 1, 1)])]

        self.assertEqual(result, expected)

    def test_all_dates_in_window(self):
        """Test when all dates fit in one window."""
        dates = [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]

        result = fmodel.date_windows(dates, window_days=5)
        expected = [frozenset(dates)]

        self.assertEqual(result, expected)

    def test_no_overlapping_windows(self):
        """Test dates that don't create overlapping windows."""
        dates = [date(2025, 1, 1), date(2025, 1, 10), date(2025, 1, 20)]

        result = fmodel.date_windows(dates, window_days=3)

        # Each date should form its own window
        expected = [
            frozenset([date(2025, 1, 1)]),
            frozenset([date(2025, 1, 10)]),
            frozenset([date(2025, 1, 20)]),
        ]

        self.assertCountEqual(result, expected)

    def test_subset_removal(self):
        """Test that subset windows are properly removed."""
        dates = [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 4)]

        result = fmodel.date_windows(dates, window_days=10)

        # All dates should be in one maximal window
        expected = [frozenset(dates)]
        self.assertEqual(result, expected)

    def test_empty_input(self):
        """Test with empty date list."""
        result = fmodel.date_windows([], window_days=7)
        self.assertEqual(result, [])

    def test_window_size_zero(self):
        """Test with zero window size."""
        dates = [date(2025, 1, 1), date(2025, 1, 2)]

        result = fmodel.date_windows(dates, window_days=0)

        # Each date should form its own window
        expected = [frozenset([date(2025, 1, 1)]), frozenset([date(2025, 1, 2)])]

        self.assertCountEqual(result, expected)

    def test_unsorted_input(self):
        """Test that function handles unsorted input correctly."""
        dates = [date(2025, 1, 8), date(2025, 1, 1), date(2025, 1, 3)]

        result = fmodel.date_windows(dates, window_days=5)

        # Should work same as sorted input
        expected = [
            frozenset([date(2025, 1, 1), date(2025, 1, 3)]),
            frozenset([date(2025, 1, 3), date(2025, 1, 8)]),
        ]

        self.assertCountEqual(result, expected)

    @given(
        dates_list=st.lists(
            st.dates(min_value=date(2020, 1, 1), max_value=date(2025, 1, 1)),
            min_size=0,
            max_size=20,
            unique=True,
        ),
        window_days=st.integers(min_value=0, max_value=30),
    )
    def test_date_windows_properties(self, dates_list, window_days):
        """Test key properties of date_windows output."""
        assume(len(dates_list) > 0 or window_days >= 0)  # Avoid trivial cases

        result = fmodel.date_windows(dates_list, window_days)

        # Property 1: Each window contains only dates from input
        for window in result:
            for d in window:
                self.assertIn(d, dates_list)

        # Property 2: All windows span <= window_days
        for window in result:
            if len(window) > 1:
                dates_in_window = sorted(window)
                span = (dates_in_window[-1] - dates_in_window[0]).days
                self.assertLessEqual(span, window_days)

        # Property 3: No window is a subset of another (maximal property)
        for i, window1 in enumerate(result):
            for j, window2 in enumerate(result):
                if i != j:
                    self.assertFalse(
                        window1.issubset(window2),
                        f"Window {window1} is subset of {window2}",
                    )

        # Property 4: All windows are frozensets
        for window in result:
            self.assertIsInstance(window, frozenset)

        # Property 5: If input is empty, result should be empty
        if not dates_list:
            self.assertEqual(result, [])

        # Property 6: Each date appears in at least one maximal window
        # (This tests that we don't miss any valid windows)
        if dates_list:
            # Every input date should be in at least one window
            for d in dates_list:
                self.assertTrue(
                    any(d in window for window in result),
                    f"Date {d} not found in any window",
                )


if __name__ == "__main__":
    unittest.main()
