"""The HANDOFF §15 falsification experiment.

rk4 and heun2 in Q15 on damped_osc: (1) what fraction of the per-step cycle count is
coefficient arithmetic rather than derivative evaluation, under both primary cost
models; (2) an h sweep locating where Q15 roundoff overtakes truncation error.
Writes work_dir()/falsification.json. Deterministic; no network.
"""
from __future__ import annotations

import json
import math
from fractions import Fraction
from pathlib import Path

from rk_harness.types import CostModel, Tableau
from rk_harness.paths import FIXTURES_DIR, work_dir
from rk_harness.costmodel import M0PLUS_FAST, M0PLUS_SLOW, cycle_count
from rk_harness.problems import PROBLEMS, FLOAT_RHS, error_metric
from rk_harness.simulate import solve_float, problem_error

_INF = float("inf")

# Analytic op counts of one derivative evaluation f(t, y) in Q15 per problem.
# damped_osc: x' = v ; v' = -2*zeta*omega*v - omega^2*x  ->  2 loads, 2 mul, 2 shift,
# 1 add, 2 stores (= 13 cycles m0plus_fast, 75 m0plus_slow). The others are the same
# style of count; only damped_osc is required by the experiment.
_DERIVATIVE_OPS: dict[str, dict[str, int]] = {
    "dahlquist":      {"load": 1, "mul": 1, "shift": 1, "add": 0, "store": 1},
    "damped_osc":     {"load": 2, "mul": 2, "shift": 2, "add": 1, "store": 2},
    "vanderpol_mild": {"load": 2, "mul": 3, "shift": 3, "add": 2, "store": 2},
    "pendulum":       {"load": 2, "mul": 3, "shift": 3, "add": 3, "store": 2},
    "dc_motor":       {"load": 2, "mul": 4, "shift": 4, "add": 4, "store": 2},
    "rc_thermal":     {"load": 3, "mul": 7, "shift": 7, "add": 4, "store": 3},
    "quaternion":     {"load": 4, "mul": 12, "shift": 12, "add": 8, "store": 4},
}

KILL_FRACTION = 0.15
PROCEED_FRACTION = 0.30
PRACTICAL_H_MIN = 1e-3
PRACTICAL_H_MAX = 1.0
SWEEP_NS = [8 * 2**k for k in range(10)]
PROBLEM_NAME = "damped_osc"
METHOD_NAMES = ("rk4", "heun2")


def derivative_cost(problem_name: str, model: CostModel) -> int:
    """Analytic cycle count of one derivative evaluation under `model`."""
    if problem_name not in _DERIVATIVE_OPS:
        raise ValueError(f"no derivative op count for problem {problem_name!r}")
    ops = _DERIVATIVE_OPS[problem_name]
    return sum(count * model.cycles[op] for op, count in ops.items())


def _stages(t: Tableau) -> int:
    return len(t.b)


def coefficient_fraction(t: Tableau, problem_name: str, model: CostModel) -> float:
    """cycle_count(t, model, n_states) / (cycle_count + stages * derivative_cost)."""
    n_states = PROBLEMS[problem_name].n_states
    coeff = cycle_count(t, model, n_states)
    deriv = _stages(t) * derivative_cost(problem_name, model)
    total = coeff + deriv
    if total == 0:
        return 0.0
    return coeff / total


def _y0_physical(problem_name: str) -> tuple[float, ...]:
    p = PROBLEMS[problem_name]
    return tuple(q / 32768.0 / p.scale for q in p.y0)


def sweep(t: Tableau, problem_name: str, ns: list[int]) -> list[dict]:
    """For each n: {"n", "h", "q15_error", "float_error"}; overflow -> inf."""
    p = PROBLEMS[problem_name]
    rhs = FLOAT_RHS[problem_name]
    y0 = _y0_physical(problem_name)
    rows: list[dict] = []
    for n in ns:
        h = p.t_end / n
        try:
            q15_err = float(problem_error(t, p, n)[0])
        except Exception:
            q15_err = _INF
        if not math.isfinite(q15_err):
            q15_err = _INF
        try:
            y = solve_float(t, rhs, y0, p.t_end, n)
            float_err = float(error_metric(problem_name, tuple(float(v) for v in y)))
        except Exception:
            float_err = _INF
        if not math.isfinite(float_err):
            float_err = _INF
        rows.append({"n": int(n), "h": float(h), "q15_error": q15_err, "float_error": float_err})
    return rows


def crossover(rows: list[dict]) -> float | None:
    """The first h (scanning from large h to small h) at which the Q15 error stops
    decreasing while the float64 error keeps decreasing; None if never."""
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        q_prev, q_cur = prev["q15_error"], cur["q15_error"]
        f_prev, f_cur = prev["float_error"], cur["float_error"]
        if not (math.isfinite(q_prev) and math.isfinite(q_cur)):
            continue
        if not (math.isfinite(f_prev) and math.isfinite(f_cur)):
            continue
        if q_cur >= q_prev and f_cur < f_prev:
            return float(cur["h"])
    return None


def _parse_fraction(s: str) -> Fraction:
    return Fraction(str(s).strip())


def _load_classical(name: str) -> Tableau:
    data = json.loads((FIXTURES_DIR / "classical.json").read_text(encoding="utf-8"))
    d = data[name]
    A = tuple(tuple(_parse_fraction(x) for x in row) for row in d["A"])
    b = tuple(_parse_fraction(x) for x in d["b"])
    c = tuple(_parse_fraction(x) for x in d["c"])
    return Tableau(A=A, b=b, c=c)


def _practical(h: float | None) -> bool:
    return h is not None and PRACTICAL_H_MIN <= h <= PRACTICAL_H_MAX


def _verdict(fractions: list[float], crossovers: list[float | None]) -> str:
    any_crossover = any(_practical(h) for h in crossovers)
    if fractions and all(f >= PROCEED_FRACTION for f in fractions) and any_crossover:
        return "proceed"
    if fractions and all(f < KILL_FRACTION for f in fractions) and not any_crossover:
        return "kill"
    return "mixed"


def run() -> dict:
    """rk4 and heun2 on damped_osc, ns = 8*2**k for k in 0..9, both primary models."""
    models = (M0PLUS_FAST, M0PLUS_SLOW)
    methods: dict[str, dict] = {}
    all_fractions: list[float] = []
    all_crossovers: list[float | None] = []
    n_states = PROBLEMS[PROBLEM_NAME].n_states
    for name in METHOD_NAMES:
        t = _load_classical(name)
        fractions = {m.name: coefficient_fraction(t, PROBLEM_NAME, m) for m in models}
        cycles = {m.name: cycle_count(t, m, n_states) for m in models}
        deriv = {m.name: derivative_cost(PROBLEM_NAME, m) for m in models}
        rows = sweep(t, PROBLEM_NAME, SWEEP_NS)
        x = crossover(rows)
        methods[name] = {
            "stages": _stages(t),
            "coefficient_fraction": fractions,
            "cycles_per_step": cycles,
            "derivative_cost": deriv,
            "sweep": rows,
            "crossover_h": x,
            "crossover_practical": _practical(x),
        }
        all_fractions.extend(fractions.values())
        all_crossovers.append(x)
    return {
        "problem": PROBLEM_NAME,
        "n_states": n_states,
        "t_end": PROBLEMS[PROBLEM_NAME].t_end,
        "ns": list(SWEEP_NS),
        "models": [m.name for m in models],
        "thresholds": {
            "kill_fraction": KILL_FRACTION,
            "proceed_fraction": PROCEED_FRACTION,
            "practical_h_min": PRACTICAL_H_MIN,
            "practical_h_max": PRACTICAL_H_MAX,
        },
        "methods": methods,
        "verdict": _verdict(all_fractions, all_crossovers),
    }


def _summary(data: dict) -> str:
    lines = [f"falsification experiment on {data['problem']}: verdict = {data['verdict']}"]
    for name, m in data["methods"].items():
        fr = ", ".join(f"{k} {v:.3f}" for k, v in m["coefficient_fraction"].items())
        x = m["crossover_h"]
        xs = "none" if x is None else f"{x:.4g}"
        lines.append(f"  {name}: coefficient fraction {fr}; crossover h = {xs}")
        for row in m["sweep"]:
            lines.append(f"    n={row['n']:5d} h={row['h']:.4g} "
                         f"q15={row['q15_error']:.3e} float={row['float_error']:.3e}")
    return "\n".join(lines)


def main() -> int:
    """Write work_dir()/falsification.json and print a summary."""
    data = run()
    out: Path = work_dir() / "falsification.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    print(_summary(data))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
