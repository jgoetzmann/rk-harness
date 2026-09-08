"""Embedded-pair census over dyadic A matrices (epoch-2 side track).

PRELIMINARY, EXACT-ARITHMETIC, OFF-ARCHIVE. Nothing here is verifier-pinned and
nothing here writes to the archive.

docs/EPOCH2-DESIGN.md section 2 assumes, without counting, that a dyadic strictly
lower-triangular A generally admits both an order-p weight vector b and an
order-(p-1) vector b_hat with a free parameter left over, so that the pair has an
error estimate at all. This module counts that over the whole dyadic lattice for
small stage counts, exactly over Fractions.

For one A the two questions are linear. `orderconditions.b_linear_system(A, c, p)`
returns (G, r) with the order conditions up to p written as G @ b == r, so:

* order 3 solvable at all  -> the propagated b exists,
* order 2 solvable with at least one free column -> a b_hat exists that is not
  forced to equal b, i.e. the estimate d = b - b_hat is not identically zero.

The interesting failure is the second one: an A whose order-2 system has a unique
solution admits exactly one order-2 vector, so any order-3 b that also satisfies
order 2 *is* that vector and d is forced to zero. Those matrices cannot carry an
embedded pair however the weights are chosen, and counting them is the point.

Deliberately independent of search.py. Importing it would pull the optimizer
(and cma) into a side-track firing, and would make artifacts depend on a module
the side-track code digest does not cover.
"""
from __future__ import annotations

import itertools
from fractions import Fraction

from rk_harness import enumeration, orderconditions
from rk_harness.coeffrep import to_rep

# The lattice bound. EPOCH2-DESIGN section 2 keeps A entries inside [-1, 1]; the
# scored epoch-1 lattice allows 2, but a stage coefficient above 1 is already
# outside the pair families the design describes.
CENSUS_ABS_MAX: int = 1

# to_rep's own limits, duplicated here only as a prefilter (see _rep_is_exact).
_REP_S_MAX = 20
_REP_M_MAX = 32767

# How many example FSAL matrices to keep in a census result. The artifact has to
# stay a few KB (SIDETRACK-AUTOMATION risk R3), so this is a sample, not a dump.
FSAL_EXAMPLES: int = 8


def census_lattice(s_max: int) -> list[Fraction]:
    """The dyadic values an A entry may take: m / 2**s with s <= s_max, 0 < |x| <= 1."""
    return enumeration.lattice(s_max, CENSUS_ABS_MAX)


# --------------------------------------------------------------------------- exact linear algebra

def _rank_and_consistency(G: list[list[Fraction]], r: list[Fraction]):
    """Gaussian elimination over Fractions on the augmented system G @ x == r.

    Returns (rank, consistent, n_free, particular), where `particular` is the
    solution with every free column set to zero, or None when the system has no
    solution. Exact throughout: no float ever touches an order condition.
    """
    rows = [list(g) + [rr] for g, rr in zip(G, r)]
    n_cols = len(G[0]) if G else 0
    pivots: list[int] = []
    row = 0
    for col in range(n_cols):
        pick = None
        for i in range(row, len(rows)):
            if rows[i][col] != 0:
                pick = i
                break
        if pick is None:
            continue
        rows[row], rows[pick] = rows[pick], rows[row]
        pv = rows[row][col]
        rows[row] = [v / pv for v in rows[row]]
        for i in range(len(rows)):
            if i != row and rows[i][col] != 0:
                f = rows[i][col]
                rows[i] = [a - f * b for a, b in zip(rows[i], rows[row])]
        pivots.append(col)
        row += 1
        if row == len(rows):
            break
    consistent = True
    for i in range(len(rows)):
        if all(v == 0 for v in rows[i][:n_cols]) and rows[i][n_cols] != 0:
            consistent = False
            break
    rank = len(pivots)
    n_free = n_cols - rank
    if not consistent:
        return rank, False, n_free, None
    particular = [Fraction(0)] * n_cols
    for i, col in enumerate(pivots):
        particular[col] = rows[i][n_cols]
    return rank, True, n_free, particular


def contains_vector(G: list[list[Fraction]], r: list[Fraction], v) -> bool:
    """Does the specific vector v satisfy G @ v == r exactly?

    Used for the FSAL test, which asks whether the last row of A is itself an
    order-3 weight vector. Phrasing it as a membership question rather than a
    comparison against `particular` keeps the answer independent of which
    particular solution the elimination happened to pick.
    """
    vv = [Fraction(x) for x in v]
    for row, rhs in zip(G, r):
        acc = Fraction(0)
        for a, b in zip(row, vv):
            if a and b:
                acc += a * b
        if acc != rhs:
            return False
    return True


def _rep_is_exact(x: Fraction) -> bool:
    """coeffrep.to_rep(x).exact, with the values that cannot be exact rejected up front.

    to_rep looks for an s <= 20 with x * 2**s an integer of magnitude <= 32767, so a
    non-dyadic denominator can never be exact and there is no need to run the search.
    The prefilter is what keeps the 4-stage census inside a firing: most b vectors the
    order conditions produce carry a denominator of 3 or 9.
    """
    d = x.denominator
    if d & (d - 1):                                  # not a power of two
        return False
    if d.bit_length() - 1 > _REP_S_MAX or abs(x.numerator) > _REP_M_MAX:
        return False
    return to_rep(x).exact


# --------------------------------------------------------------------------- the census

def _matrix_from(entries, stages: int):
    """Fill the strictly lower triangle row by row, and take c as the row sums."""
    A = [[Fraction(0)] * stages for _ in range(stages)]
    k = 0
    for i in range(stages):
        for j in range(i):
            A[i][j] = entries[k]
            k += 1
    A_t = tuple(tuple(row) for row in A)
    c = tuple(sum(row) for row in A_t)
    return A_t, c


def census(stages: int, s_max: int, cap: int) -> dict:
    """Walk every strictly lower-triangular dyadic A at this lattice size.

    Returns a document, not a list: the enumeration is far too large to keep. A
    space bigger than `cap` is reported rather than walked, which is itself the
    answer to where an exhaustive epoch-2 enumeration stops being possible.
    """
    lat = census_lattice(s_max)
    entries = stages * (stages - 1) // 2
    space = len(lat) ** entries
    head = {
        "stages": stages,
        "s_max": s_max,
        "lattice_size": len(lat),
        "free_entries": entries,
        "space_size": space,
        "cap": cap,
    }
    if space > cap:
        return {**head, "status": "capped", "counts": {}, "fsal_examples": []}

    n_matrices = 0
    both_solvable = 0
    order3_consistent = 0
    order2_consistent = 0
    b_hat_free = 0
    d_forced_zero = 0
    fsal = 0
    b_hat_exact = 0
    examples: list[dict] = []

    for combo in itertools.product(lat, repeat=entries):
        A, c = _matrix_from(combo, stages)
        n_matrices += 1
        G3, r3 = orderconditions.b_linear_system(A, c, 3)
        _rank3, ok3, _free3, _part3 = _rank_and_consistency(G3, r3)
        G2, r2 = orderconditions.b_linear_system(A, c, 2)
        _rank2, ok2, free2, part2 = _rank_and_consistency(G2, r2)
        if ok3:
            order3_consistent += 1
        if ok2:
            order2_consistent += 1
        if ok3 and ok2:
            both_solvable += 1
        if ok2 and free2 >= 1:
            b_hat_free += 1
        if ok3 and ok2 and free2 == 0:
            d_forced_zero += 1
        if ok2 and part2 is not None and all(_rep_is_exact(x) for x in part2):
            b_hat_exact += 1
        if c[-1] == 1 and contains_vector(G3, r3, A[-1]):
            fsal += 1
            if len(examples) < FSAL_EXAMPLES:
                examples.append({
                    "A": [[str(x) for x in row] for row in A],
                    "c": [str(x) for x in c],
                    "order2_free_columns": free2,
                })

    return {
        **head,
        "status": "ok",
        "counts": {
            "matrices": n_matrices,
            "order3_consistent": order3_consistent,
            "order2_consistent": order2_consistent,
            "both_solvable": both_solvable,
            "b_hat_free": b_hat_free,
            "d_forced_zero": d_forced_zero,
            "fsal": fsal,
            "b_hat_exact": b_hat_exact,
        },
        "fsal_examples": examples,
    }
