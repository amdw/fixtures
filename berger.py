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

"""Berger tables: the standard construction for single round-robin draws.

Middlesex League Rules 7c: a division of more than eight teams plays each pair
once only, "based on the draw for the appropriate Berger table". Given a draw
(an ordering of the entrants, position 1 first), the Berger table fixes which
side of every match is at home; the solver then only has to place each of those
fixtures on a date.

berger_pairings() returns the table for plain entrant numbers; single_round_pairings()
maps those numbers onto caller-supplied entrants.
"""

from __future__ import annotations

from collections.abc import Sequence


def _rotate(slots: list[int], step: int, modulus: int, fixed: int) -> list[int]:
    """Advance every slot value by `step` positions, wrapping within 1..modulus, and
    leaving `fixed` (the entrant that stays put across all rounds) untouched."""
    return [s if s == fixed else ((s - 1 + step) % modulus) + 1 for s in slots]


def berger_pairings(n: int) -> list[tuple[int, int]]:
    """The Berger table for `n` entrants numbered 1..n, as a flat list of
    (home, away) number pairs -- one per unordered pair of entrants, so
    n * (n - 1) // 2 pairs in all.

    Pairs are listed round by round (rounds 1..n-1 for even n), in the order the
    canonical FIDE/Schurig Berger tables print them. Entrant `n` alternates
    sides each round; the rest follow the standard rotation. For odd `n` a
    phantom entrant is added to even out the rounds and its games (the byes) are
    dropped, so every real entrant still plays n-1 games.
    """
    if n < 2:
        return []

    # For odd n, add a phantom entrant (number `total`) so there's an even field;
    # its games are byes and get filtered out below.
    total = n if n % 2 == 0 else n + 1
    half = total // 2

    # `top[i]` plays `bottom[i]` each round. The phantom / pivot entrant `total`
    # sits in bottom[0] and never rotates; everyone else advances by `half` each
    # round, wrapping within 1..total-1.
    top = list(range(1, half + 1))
    bottom = list(range(total, half, -1))

    pairings: list[tuple[int, int]] = []
    for rnd in range(1, total):
        for home, away in zip(top, bottom, strict=True):
            # Entrant `total` takes each side on alternate rounds (it is White /
            # at home on even rounds in the canonical tables).
            if total in (home, away) and rnd % 2 == 0:
                home, away = away, home
            if home <= n and away <= n:
                pairings.append((home, away))
        top = _rotate(top, half, total - 1, total)
        bottom = _rotate(bottom, half, total - 1, total)

    return pairings


def single_round_pairings[T](entrants: Sequence[T]) -> list[tuple[T, T]]:
    """Return the (home, away) pairs for a single round-robin among `entrants`,
    with the home/away side of each match taken from the Berger table for
    len(entrants) entrants drawn in the given order: entrants[0] is table
    position 1, entrants[1] position 2, and so on. Each unordered pair of
    entrants appears exactly once.
    """
    ordered = list(entrants)
    return [
        (ordered[home - 1], ordered[away - 1])
        for home, away in berger_pairings(len(ordered))
    ]
