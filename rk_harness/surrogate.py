"""Surrogate model — HANDOFF §4.10; SPEC §Surface/surrogate.

A HistGradientBoostingRegressor predicting heldout_error from tableau structure.
AVR_APPROX cycles are deliberately excluded from the features.
"""
from __future__ import annotations

import math

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from rk_harness.coeffrep import tableau_csd_total, tableau_quant_error, to_rep
from rk_harness.costmodel import M0PLUS_FAST, M0PLUS_SLOW, cycle_count
from rk_harness.evaluator import stability_extents
from rk_harness.orderconditions import achieved_order_symbolic
from rk_harness.tableau import stages
from rk_harness.types import Record, Tableau

TRAIN_THRESHOLD = 5000


def should_train(n_records: int) -> bool:
    return n_records >= TRAIN_THRESHOLD


def features(t: Tableau) -> list[float]:
    """Exactly 12 entries, in the SPEC order."""
    s = stages(t)
    fast = cycle_count(t, M0PLUS_FAST, 1)
    slow = cycle_count(t, M0PLUS_SLOW, 1)
    sum_b = float(sum(t.b))
    lower = [t.A[i][j] for i in range(s) for j in range(i)]
    max_abs_a = 0.0
    for row in t.A:
        for x in row:
            ax = abs(float(x))
            if ax > max_abs_a:
                max_abs_a = ax
    zeros_a = sum(1 for x in lower if x == 0)
    single_bit = sum(1 for x in (lower + list(t.b)) if to_rep(x).csd_weight == 1)
    st_real, st_imag = stability_extents(t)
    order = achieved_order_symbolic(t, max_order=5)
    return [
        float(s),
        float(tableau_csd_total(t)),
        float(tableau_quant_error(t)),
        float(fast),
        float(slow),
        sum_b,
        max_abs_a,
        float(zeros_a),
        float(single_bit),
        float(st_real),
        float(st_imag),
        float(order),
    ]


def _finite(x: float) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def train(records: list[Record]) -> object:
    xs: list[list[float]] = []
    ys: list[float] = []
    for r in records:
        y = r.score.heldout_error
        if not _finite(y):
            continue
        xs.append(features(r.tableau))
        ys.append(float(y))
    if not xs:
        raise ValueError("train: no records with a finite heldout_error")
    X = np.asarray(xs, dtype=np.float64)
    Y = np.asarray(ys, dtype=np.float64)
    m = HistGradientBoostingRegressor(random_state=0)
    m.fit(X, Y)
    return m


def predict(m: object, t: Tableau) -> float:
    X = np.asarray([features(t)], dtype=np.float64)
    return float(m.predict(X)[0])


def calibration_error(m: object, holdout: list[Record]) -> float:
    total = 0.0
    n = 0
    for r in holdout:
        y = r.score.heldout_error
        if not _finite(y):
            continue
        total += abs(predict(m, r.tableau) - float(y))
        n += 1
    if n == 0:
        return 0.0
    return total / n
