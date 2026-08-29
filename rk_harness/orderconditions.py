"""Order conditions for explicit Runge-Kutta tableaus via Butcher's rooted trees.

HANDOFF §4.3, stated exactly. A tree is a tuple of its subtrees; ``()`` is the
single node. Canonical form: subtrees sorted (Python tuple ordering, each
subtree itself canonical). Everything is ``fractions.Fraction``; no floats.
"""
from __future__ import annotations

from fractions import Fraction

from rk_harness.types import Tableau

# OEIS A000081: number of unlabelled rooted trees with n nodes, n = 1..8.
_A000081 = (1, 1, 2, 4, 9, 20, 48, 115)

_TREE_CACHE: dict[int, list[tuple]] = {}


def tree_order(t: tuple) -> int:
    return 1 + sum(tree_order(s) for s in t)


def gamma(t: tuple) -> int:
    g = tree_order(t)
    for s in t:
        g *= gamma(s)
    return g


def _forests(pool: list[tuple], orders: list[int], remaining: int, start: int) -> list[tuple]:
    """Non-decreasing index sequences into ``pool`` whose tree orders sum to
    ``remaining``; returned as tuples of trees (already sorted because the pool
    is sorted and indices are non-decreasing)."""
    if remaining == 0:
        return [()]
    out: list[tuple] = []
    for idx in range(start, len(pool)):
        o = orders[idx]
        if o > remaining:
            continue
        head = pool[idx]
        for rest in _forests(pool, orders, remaining - o, idx):
            out.append((head,) + rest)
    return out


def trees(order: int) -> list[tuple]:
    """All rooted trees with exactly ``order`` nodes, canonical, deduplicated."""
    if order < 1:
        return []
    if order in _TREE_CACHE:
        return list(_TREE_CACHE[order])
    if order == 1:
        result = [()]
    else:
        pool: list[tuple] = []
        for k in range(1, order):
            pool.extend(trees(k))
        pool.sort()
        orders = [tree_order(t) for t in pool]
        found: set[tuple] = set()
        for forest in _forests(pool, orders, order - 1, 0):
            found.add(tuple(sorted(forest)))
        result = sorted(found)
    if order <= len(_A000081) and len(result) != _A000081[order - 1]:
        raise AssertionError(
            f"tree generator broken: order {order} produced {len(result)} trees, "
            f"expected {_A000081[order - 1]}"
        )
    _TREE_CACHE[order] = result
    return list(result)


def all_trees(order: int) -> list[tuple]:
    out: list[tuple] = []
    for k in range(1, order + 1):
        out.extend(trees(k))
    return out


def _internal_weights(A, t: tuple, memo: dict[tuple, list[Fraction]]) -> list[Fraction]:
    """g_t(i) for every stage i. g_()(i) = 1; g_t(i) = prod_k sum_j A[i][j] g_{t_k}(j)."""
    if t in memo:
        return memo[t]
    s = len(A)
    if t == ():
        g = [Fraction(1)] * s
    else:
        g = [Fraction(1)] * s
        for sub in t:
            gs = _internal_weights(A, sub, memo)
            for i in range(s):
                acc = Fraction(0)
                row = A[i]
                for j in range(s):
                    a = row[j]
                    if a != 0:
                        acc += a * gs[j]
                g[i] = g[i] * acc
    memo[t] = g
    return g


def elementary_weight(tab: Tableau, t: tuple) -> Fraction:
    g = _internal_weights(tab.A, t, {})
    phi = Fraction(0)
    for i in range(len(tab.b)):
        phi += tab.b[i] * g[i]
    return phi


def residuals(tab: Tableau, order: int) -> list[Fraction]:
    memo: dict[tuple, list[Fraction]] = {}
    out: list[Fraction] = []
    for t in all_trees(order):
        g = _internal_weights(tab.A, t, memo)
        phi = Fraction(0)
        for i in range(len(tab.b)):
            phi += tab.b[i] * g[i]
        out.append(phi - Fraction(1, gamma(t)))
    return out


def achieved_order_symbolic(tab: Tableau, max_order: int = 8) -> int:
    memo: dict[tuple, list[Fraction]] = {}
    achieved = 0
    for p in range(1, max_order + 1):
        for t in trees(p):
            g = _internal_weights(tab.A, t, memo)
            phi = Fraction(0)
            for i in range(len(tab.b)):
                phi += tab.b[i] * g[i]
            if phi != Fraction(1, gamma(t)):
                return achieved
        achieved = p
    return achieved


def is_dyadic(x: Fraction) -> bool:
    d = Fraction(x).denominator
    return d & (d - 1) == 0


def dyadic_order_bound() -> int:
    return 2


def b_linear_system(
    tab_A: tuple[tuple[Fraction, ...], ...],
    c: tuple[Fraction, ...],
    order: int,
) -> tuple[list[list[Fraction]], list[Fraction]]:
    """(G, r): one row per tree in all_trees(order); G[k][i] = g_t(i), r[k] = 1/gamma(t).
    The order conditions up to ``order`` are exactly ``G @ b == r``."""
    A = tuple(tuple(Fraction(x) for x in row) for row in tab_A)
    memo: dict[tuple, list[Fraction]] = {}
    G: list[list[Fraction]] = []
    r: list[Fraction] = []
    for t in all_trees(order):
        g = _internal_weights(A, t, memo)
        G.append([Fraction(v) for v in g])
        r.append(Fraction(1, gamma(t)))
    return G, r
