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

"""Tests for berger.py."""

import collections
import itertools
import unittest

import berger

# The canonical FIDE Berger tables (FIDE Handbook C.06, "Berger Tables"),
# round by round, each entry "home-away" (White-Black). The table for an odd
# number of players is the one for the next even number with the last player's
# games treated as byes.
_FIDE_8 = [
    [(1, 8), (2, 7), (3, 6), (4, 5)],
    [(8, 5), (6, 4), (7, 3), (1, 2)],
    [(2, 8), (3, 1), (4, 7), (5, 6)],
    [(8, 6), (7, 5), (1, 4), (2, 3)],
    [(3, 8), (4, 2), (5, 1), (6, 7)],
    [(8, 7), (1, 6), (2, 5), (3, 4)],
    [(4, 8), (5, 3), (6, 2), (7, 1)],
]

_FIDE_10 = [
    [(1, 10), (2, 9), (3, 8), (4, 7), (5, 6)],
    [(10, 6), (7, 5), (8, 4), (9, 3), (1, 2)],
    [(2, 10), (3, 1), (4, 9), (5, 8), (6, 7)],
    [(10, 7), (8, 6), (9, 5), (1, 4), (2, 3)],
    [(3, 10), (4, 2), (5, 1), (6, 9), (7, 8)],
    [(10, 8), (9, 7), (1, 6), (2, 5), (3, 4)],
    [(4, 10), (5, 3), (6, 2), (7, 1), (8, 9)],
    [(10, 9), (1, 8), (2, 7), (3, 6), (4, 5)],
    [(5, 10), (6, 4), (7, 3), (8, 2), (9, 1)],
]

_FIDE_12 = [
    [(1, 12), (2, 11), (3, 10), (4, 9), (5, 8), (6, 7)],
    [(12, 7), (8, 6), (9, 5), (10, 4), (11, 3), (1, 2)],
    [(2, 12), (3, 1), (4, 11), (5, 10), (6, 9), (7, 8)],
    [(12, 8), (9, 7), (10, 6), (11, 5), (1, 4), (2, 3)],
    [(3, 12), (4, 2), (5, 1), (6, 11), (7, 10), (8, 9)],
    [(12, 9), (10, 8), (11, 7), (1, 6), (2, 5), (3, 4)],
    [(4, 12), (5, 3), (6, 2), (7, 1), (8, 11), (9, 10)],
    [(12, 10), (11, 9), (1, 8), (2, 7), (3, 6), (4, 5)],
    [(5, 12), (6, 4), (7, 3), (8, 2), (9, 1), (10, 11)],
    [(12, 11), (1, 10), (2, 9), (3, 8), (4, 7), (5, 6)],
    [(6, 12), (7, 5), (8, 4), (9, 3), (10, 2), (11, 1)],
]


class BergerPairingsTest(unittest.TestCase):
    def test_matches_canonical_fide_tables(self):
        for n, table in ((8, _FIDE_8), (10, _FIDE_10), (12, _FIDE_12)):
            with self.subTest(n=n):
                expected = [pair for rnd in table for pair in rnd]
                self.assertEqual(expected, berger.berger_pairings(n))

    def test_two_entrants(self):
        self.assertEqual([(1, 2)], berger.berger_pairings(2))

    def test_degenerate_sizes(self):
        self.assertEqual([], berger.berger_pairings(0))
        self.assertEqual([], berger.berger_pairings(1))

    def _assert_valid_single_round(self, n: int) -> list[tuple[int, int]]:
        pairings = berger.berger_pairings(n)
        # Every unordered pair exactly once.
        self.assertEqual(n * (n - 1) // 2, len(pairings))
        unordered = collections.Counter(frozenset(p) for p in pairings)
        self.assertEqual(
            {frozenset(p): 1 for p in itertools.combinations(range(1, n + 1), 2)},
            dict(unordered),
        )
        # Every entrant plays n-1 games.
        appearances = collections.Counter(x for p in pairings for x in p)
        self.assertEqual({x: n - 1 for x in range(1, n + 1)}, dict(appearances))
        return pairings

    def test_valid_single_round_for_even_and_odd_sizes(self):
        for n in range(2, 15):
            with self.subTest(n=n):
                self._assert_valid_single_round(n)

    def test_home_away_counts_are_balanced(self):
        # Each entrant hosts either floor((n-1)/2) or ceil((n-1)/2) of its games.
        for n in range(2, 15):
            with self.subTest(n=n):
                pairings = berger.berger_pairings(n)
                homes = collections.Counter(home for home, _ in pairings)
                low, high = (n - 1) // 2, (n - 1 + 1) // 2
                for x in range(1, n + 1):
                    self.assertIn(homes[x], {low, high})


class SingleRoundPairingsTest(unittest.TestCase):
    def test_maps_numbers_onto_entrants_in_draw_order(self):
        entrants = ["a", "b", "c", "d"]
        pairings = berger.single_round_pairings(entrants)
        number_pairings = berger.berger_pairings(4)
        self.assertEqual(
            [(entrants[h - 1], entrants[a - 1]) for h, a in number_pairings],
            pairings,
        )

    def test_first_entrant_is_berger_position_one(self):
        # Round 1 of the Berger table pairs position 1 (home) with position n.
        entrants = ["p1", "p2", "p3", "p4", "p5", "p6"]
        first = berger.single_round_pairings(entrants)[0]
        self.assertEqual(("p1", "p6"), first)

    def test_each_pair_once_over_arbitrary_entrants(self):
        entrants = list("abcdefghij")  # 10
        pairings = berger.single_round_pairings(entrants)
        self.assertEqual(45, len(pairings))
        self.assertEqual(
            {frozenset(p) for p in itertools.combinations(entrants, 2)},
            {frozenset(p) for p in pairings},
        )

    def test_empty(self):
        self.assertEqual([], berger.single_round_pairings([]))


if __name__ == "__main__":
    unittest.main()
