"""Q15 realization of the epoch-2 embedded error estimate and step controller.

PRELIMINARY, OFF-ARCHIVE. Nothing here is verifier-pinned and nothing here writes
to the archive. It imports the pinned Q15 primitives read-only and adds nothing
to them.

docs/EPOCH2-DESIGN.md section 3 argues that each floored term of the error
estimate carries about -0.5 LSB of bias, so a four-term estimate sits on a bias
floor of roughly 2 LSB, and section 5 then requires the scored tolerance ladder to
sit above that floor. Both statements are estimates. This module replaces the
former with a measurement and gives the latter something to be chosen from:

* every floor is instrumented. For one application of `q15_apply(v, m, s)` the
  discarded quantity is exactly `(v*m) mod 2**s`, which is `r / 2**s` of one LSB of
  the result, so that term's floor bias is exactly `-r / 2**s` LSB. `FloorStats`
  keeps a count, an exact integer sum of remainders and a 16-bucket histogram per
  term index, all O(1) per floor. The mean bias of a term is then
  `-sum / (count * 2**s)` exactly, and the four-term total is the sum of the four
  means. No per-step arrays: 24 catalogue points of those would break the firing
  budget and the rk-work size budget together.
* the whole adaptive loop runs on the pinned Q15 primitives, so the flattening of
  the work-precision curve at tight tolerances is measured rather than predicted.
* `arithmetic="float"` short-circuits to the float prototype, so the frozen curve
  in `rk-work/prototypes/adaptive_curve.json` can be reproduced before any Q15
  number here is believed.

The factor-table encoding is Q14, not Q15, and that is a correction to the design
document rather than a preference. EPOCH2-DESIGN section 4 as written says
`h_q' = q15_mul(h_q, ftab[clamp(nu)])` with entries `round(32768 * (7/8) *
2**(nu/8))` clamped so the factor stays inside [1/4, 2]. A factor of 1.0 is 32768
and a factor of 2.0 is 65536; both are outside int16, and `fixedpoint.q15_mul`
range-checks its operands, so it cannot carry that table at all. The encoding here
holds the table in Q14 (`FACTOR_Q_BITS`), caps the factor at `2 - 2**-14` so the
largest entry is 32767, and does the update as `(h_q * f) >> FACTOR_Q_BITS` with an
explicit int16 range check. The alternative, capping growth below 1, changes the
method rather than the encoding. Whichever the owner ratifies changes a number that
enters the pinned epoch-2 cost model, so `FACTOR_Q_BITS` is a single named constant
and the choice is recorded as an open item in EPOCH2-DESIGN section 4.

Two further constraints are stated rather than absorbed:

* `costmodel._MNEMONIC_CLASS` prices only LDR, STR, MULS, ASRS, LSLS, LSRS, ADDS
  and SUBS. The tolerance compare is therefore written as SUBS, which is what sets
  the flags on ARMv6-M anyway, and the bit scan's branches cannot be priced at all.
  The scan is booked at its worst case and the branch count is reported beside the
  cycle number as a separate allowance, which makes the analytic count a bound
  rather than an exact figure. Adding a branch class to `costmodel.py` is an epoch
  boundary and is not done here.
* the Q31 time register costs two terms `costmodel.cycle_count` does not price
  today: an unconditional int32 add per accepted step, and a unit conversion,
  because `h_q` and the register carry different units. See `TimeQ31`.

``main()`` writes <RK_WORK_DIR>/prototypes/adaptive_q15_curve.json. It never writes
adaptive_curve.json or sdirk_curve.json (SIDETRACK-AUTOMATION invariant I9).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from fractions import Fraction

from rk_harness.coeffrep import to_rep
from rk_harness.costmodel import AVR_APPROX, M0PLUS_FAST, M0PLUS_SLOW, count_sequence
from rk_harness.fixedpoint import Q15OverflowError, q15_add, q15_apply, q15_mul
from rk_harness.paths import work_dir
from rk_harness.problems import make_q15_rhs, to_physical, to_q15_state
from rk_harness.prototypes.adaptive import BOGACKI_SHAMPINE_32
from rk_harness.types import CoeffRep

# --------------------------------------------------------------------------- (a) the pair, exactly

BS32_A: tuple[tuple[Fraction, ...], ...] = (
    (Fraction(0), Fraction(0), Fraction(0), Fraction(0)),
    (Fraction(1, 2), Fraction(0), Fraction(0), Fraction(0)),
    (Fraction(0), Fraction(3, 4), Fraction(0), Fraction(0)),
    (Fraction(2, 9), Fraction(1, 3), Fraction(4, 9), Fraction(0)),
)
BS32_B: tuple[Fraction, ...] = (Fraction(2, 9), Fraction(1, 3), Fraction(4, 9), Fraction(0))
BS32_B_HAT: tuple[Fraction, ...] = (Fraction(7, 24), Fraction(1, 4), Fraction(1, 3), Fraction(1, 8))
BS32_C: tuple[Fraction, ...] = (Fraction(0), Fraction(1, 2), Fraction(3, 4), Fraction(1))

BS32_EXACT: dict = {"A": BS32_A, "b": BS32_B, "b_hat": BS32_B_HAT, "c": BS32_C}

# The rationals are the point: Fraction(2.0 / 9.0) is not 2/9, and the design's
# d = (-5/72, 1/12, 1/9, -1/8) only comes out exactly from the rationals. Every
# entry must still equal the float pair the frozen curve was measured with.
for _i in range(4):
    assert float(BS32_C[_i]) == BOGACKI_SHAMPINE_32.c[_i]
    assert float(BS32_B[_i]) == BOGACKI_SHAMPINE_32.b[_i]
    assert float(BS32_B_HAT[_i]) == BOGACKI_SHAMPINE_32.b_hat[_i]
    for _j in range(4):
        assert float(BS32_A[_i][_j]) == BOGACKI_SHAMPINE_32.A[_i][_j]

D_EXACT: tuple[Fraction, ...] = tuple(BS32_B[i] - BS32_B_HAT[i] for i in range(4))
assert D_EXACT == (Fraction(-5, 72), Fraction(1, 12), Fraction(1, 9), Fraction(-1, 8))

# --------------------------------------------------------------------------- (b) the error weights

D_REPS: tuple[CoeffRep, ...] = tuple(to_rep(x) for x in D_EXACT)
assert [r.exact for r in D_REPS] == [False, False, False, True], (
    "only -1/8 is exactly representable as m / 2**s; the other three carry a "
    "quantisation error that the estimate inherits")

A_REPS: tuple[tuple[CoeffRep | None, ...], ...] = tuple(
    tuple(to_rep(x) if x != 0 else None for x in row) for row in BS32_A)
B_REPS: tuple[CoeffRep | None, ...] = tuple(to_rep(x) if x != 0 else None for x in BS32_B)

# --------------------------------------------------------------------------- (c) floor accounting

INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
Q15_MIN = -32768
Q15_MAX = 32767

HIST_BUCKETS = 16


class Q15AdaptiveError(Exception):
    """An int32 register left its range, or the controller hit the Q15 wall."""


@dataclass
class FloorStats:
    """Per-term floor accounting for the error estimate.

    Cost per recorded floor is one add and one index, whatever the run length, so
    the instrumentation does not decide how long a point may run.
    """
    shifts: tuple[int, ...]
    counts: list[int] = field(default_factory=list)
    remainder_sums: list[int] = field(default_factory=list)
    histograms: list[list[int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        n = len(self.shifts)
        if not self.counts:
            self.counts = [0] * n
        if not self.remainder_sums:
            self.remainder_sums = [0] * n
        if not self.histograms:
            self.histograms = [[0] * HIST_BUCKETS for _ in range(n)]

    def record(self, i: int, remainder: int) -> None:
        s = self.shifts[i]
        self.counts[i] += 1
        self.remainder_sums[i] += remainder
        bucket = (remainder * HIST_BUCKETS) >> s if s else 0
        if bucket >= HIST_BUCKETS:
            bucket = HIST_BUCKETS - 1
        self.histograms[i][bucket] += 1

    def mean_bias_lsb(self) -> list[float | None]:
        """Exact mean bias per term, in LSB of the estimate. Always in [-1, 0]."""
        out: list[float | None] = []
        for i, s in enumerate(self.shifts):
            n = self.counts[i]
            out.append(None if n == 0
                       else -float(Fraction(self.remainder_sums[i], n * (1 << s))))
        return out

    def total_bias_lsb(self) -> float:
        return sum(v for v in self.mean_bias_lsb() if v is not None)

    def as_dict(self) -> dict:
        return {
            "shifts": list(self.shifts),
            "counts": list(self.counts),
            "remainder_sums": list(self.remainder_sums),
            "histograms": [list(h) for h in self.histograms],
            "histogram_note": (
                f"{HIST_BUCKETS} buckets of the discarded remainder over 2**s, so "
                "bucket k covers a floor loss in [k/16, (k+1)/16) of one LSB"),
            "mean_bias_lsb": self.mean_bias_lsb(),
            "mean_bias_lsb_total": self.total_bias_lsb(),
        }


def new_floor_stats(d_reps: tuple[CoeffRep, ...] = D_REPS) -> FloorStats:
    return FloorStats(shifts=tuple(r.s for r in d_reps))


# --------------------------------------------------------------------------- (d) the estimate

def q15_estimate(hk, d_reps: tuple[CoeffRep, ...] = D_REPS,
                 stats: FloorStats | None = None) -> tuple[int, ...]:
    """E_m = sum_i q15_apply(hk[i][m], d_i.m, d_i.s), accumulated in int32.

    Each individual term fits Q15, which is why `q15_apply` is the right primitive
    for it, but the sum is taken in an int32 register before any comparison, the
    same pattern as the int32 `tmp` in `costmodel.emit_c`. `stats` records the
    quantity every floor threw away.
    """
    n_states = len(hk[0])
    out: list[int] = []
    for m in range(n_states):
        acc = 0
        for i, rep in enumerate(d_reps):
            v = hk[i][m]
            term = q15_apply(v, rep.m, rep.s)
            if stats is not None:
                stats.record(i, (v * rep.m) & ((1 << rep.s) - 1))
            acc += term
            if acc < INT32_MIN or acc > INT32_MAX:
                raise Q15AdaptiveError(f"estimate accumulator {acc} left int32")
        out.append(acc)
    return tuple(out)


# --------------------------------------------------------------------------- (e) the bit scan

MSB_MAX_ITERS: int = 31        # an int32 accumulator; an int16 needs at most 15.
# The largest positive int32 actually needs 30 iterations, so booking 31 is
# conservative by one. The cost model prices the worst case either way.


def msb_and_iterations(x: int) -> tuple[int, int]:
    """(leading bit index, loop iterations) for x > 0, as a bounded software loop.

    ARMv6-M has no CLZ, so this is the shape the hardware would use. The iteration
    count is returned because the cost model books the scan at its worst case, and
    a bound nobody has checked is not a bound.
    """
    if x <= 0:
        raise ValueError(f"msb needs a positive value, got {x}")
    n = 0
    v = x
    iters = 0
    while v > 1:
        v >>= 1
        n += 1
        iters += 1
        if iters > MSB_MAX_ITERS:
            raise AssertionError(f"bit scan exceeded {MSB_MAX_ITERS} iterations on {x}")
    return n, iters


def msb(x: int) -> int:
    return msb_and_iterations(x)[0]


# --------------------------------------------------------------------------- (f) the factor tables

# The ratified encoding. One edit here changes the table format everywhere, which
# is the whole reason it is a constant: whichever encoding the owner settles on
# changes a number that enters the pinned epoch-2 cost model.
FACTOR_Q_BITS: int = 14
FACTOR_SCALE: int = 1 << FACTOR_Q_BITS
FAC_MIN = Fraction(1, 4)
FAC_MAX = Fraction(2) - Fraction(1, FACTOR_SCALE)
SAFETY = Fraction(7, 8)

NU_MIN: int = -16              # eighths of a power of two
NU_MAX: int = 16


@dataclass(frozen=True)
class FactorTable:
    """A precomputed step-size factor table, indexed by the PI exponent nu.

    nu is in eighths of a power of two: with alpha = 1/4 and beta = 1/8 the PI
    exponent works out to nu = e_prev - 2*e, and the factor is
    safety * 2**(nu/8), clamped to [fac_min, fac_max].
    """
    name: str
    nu_min: int
    nu_max: int
    step_eighths: int
    q_bits: int
    entries: tuple[int, ...]


def build_ftab(step_eighths: int, q_bits: int = FACTOR_Q_BITS,
               fac_min: Fraction = FAC_MIN, fac_max: Fraction = FAC_MAX,
               nu_min: int = NU_MIN, nu_max: int = NU_MAX,
               name: str = "") -> FactorTable:
    """Sample safety * 2**(nu/8) every `step_eighths` over [nu_min, nu_max].

    Entries are round(2**q_bits * factor). At q_bits = 14 the clamp [1/4,
    2 - 2**-14] maps to [4096, 32767], so every entry is a positive int16.
    """
    scale = 1 << q_bits
    entries = []
    nu = nu_min
    while nu <= nu_max:
        fac = float(SAFETY) * 2.0 ** (nu / 8.0)
        lo, hi = float(fac_min), float(fac_max)
        if fac < lo:
            fac = lo
        elif fac > hi:
            fac = hi
        entries.append(int(round(scale * fac)))
        nu += step_eighths
    return FactorTable(name=name or f"ftab{len(entries)}", nu_min=nu_min, nu_max=nu_max,
                       step_eighths=step_eighths, q_bits=q_bits, entries=tuple(entries))


# 33 entries at one eighth of a power of two each, and the coarser table the design
# offers as an option: the same range sampled every four eighths, so 9 entries.
FTAB33 = build_ftab(1, name="ftab33")
FTAB9 = build_ftab(4, name="ftab9")
assert len(FTAB33.entries) == 33 and len(FTAB9.entries) == 9


def clamp_nu(nu: int, table: FactorTable = FTAB33) -> int:
    if nu < table.nu_min:
        return table.nu_min
    if nu > table.nu_max:
        return table.nu_max
    return nu


def step_factor(nu: int, table: FactorTable = FTAB33) -> int:
    """The Q(q_bits) factor for this PI exponent: one clamp and one table load."""
    idx = (clamp_nu(nu, table) - table.nu_min + table.step_eighths // 2) // table.step_eighths
    if idx < 0:
        idx = 0
    elif idx >= len(table.entries):
        idx = len(table.entries) - 1
    return table.entries[idx]


def apply_factor(h_q: int, factor: int, q_bits: int = FACTOR_Q_BITS) -> int:
    """(h_q * factor) >> q_bits with an explicit int16 range check.

    Deliberately not `q15_mul`: the table is Q14, not Q15, and a Q15 table cannot
    hold a factor of 1.0 at all. Growth is clamped at the Q15 ceiling rather than
    wrapped, because h_q >= 1.0 in the problem's time units is not representable
    and silently wrapping there would be the failure the whole Q15 discipline
    exists to prevent.
    """
    r = (h_q * factor) >> q_bits
    if r > Q15_MAX:
        return Q15_MAX
    if r < Q15_MIN:
        raise Q15AdaptiveError(f"step factor drove h_q to {r}, outside int16")
    return r


# --------------------------------------------------------------------------- (g) the Q31 time register

TIME_FULL: int = 1 << 31            # the notional scale: register / TIME_FULL is t / t_end
TIME_LAST: int = TIME_FULL - 1      # the largest value an int32 register can hold


@dataclass
class TimeQ31:
    """Elapsed time as an int32 register holding t / t_end in Q31.

    A sum of many Q15 steps leaves int16 immediately, which is why the register is
    Q31. Two terms follow that `costmodel.cycle_count` does not price today:

    * one unconditional int32 add per accepted step, and
    * a unit conversion, because h_q is Q15 of h in the problem's time units while
      the register counts fractions of t_end. That conversion is one coefficient
      application, mul plus shift, with the coefficient 2**16 / t_end: h / t_end in
      Q31 is h_q * 2**16 / t_end, and DERIV_SCALE is 1.0 for all eight validation
      problems, so h_q really is Q15 of h and nothing else has to be undone.

    Converting the register back to the argument a time-dependent rhs wants is a
    separate per-problem term. Only glucose_minimal reads t, so folding that cost
    into the per-step constant would overcharge the seven autonomous problems.
    """
    t_end: float
    reg: int = 0
    rep: CoeffRep = field(init=False)

    def __post_init__(self) -> None:
        self.rep = to_rep(Fraction(1 << 16, int(self.t_end)))

    def increment(self, h_q: int) -> int:
        return (h_q * self.rep.m) >> self.rep.s

    def advance(self, h_q: int) -> int:
        inc = self.increment(h_q)
        self.reg += inc
        if self.reg > TIME_LAST:
            raise Q15AdaptiveError(f"time register {self.reg} left int32")
        return inc

    def steps_for(self, remaining: int) -> int:
        """The largest h_q whose increment fits in `remaining`, at least 1 LSB."""
        if self.rep.m <= 0:
            return 1
        return max(1, (remaining << self.rep.s) // self.rep.m)

    def recover_float(self, t_end: float | None = None) -> float:
        end = self.t_end if t_end is None else t_end
        return self.reg / TIME_FULL * end

    def as_dict(self) -> dict:
        return {
            "register_bits": 31,
            "t_end": self.t_end,
            "unit_coefficient": f"2**16 / {int(self.t_end)}",
            "unit_rep": {"m": self.rep.m, "s": self.rep.s, "exact": self.rep.exact,
                         "csd_weight": self.rep.csd_weight},
            "final_register": self.reg,
            "final_t": self.recover_float(),
            "unpriced_terms": [
                "one int32 add per accepted step",
                "one coefficient application (mul plus shift) to convert h_q into "
                "register units",
                "converting the register back to a float argument, for the one "
                "validation problem whose rhs reads t",
            ],
        }


# --------------------------------------------------------------------------- (h) the solver

Q15_MAX_ATTEMPTS: int = 50_000
TOL_LADDER_LSB: tuple[int, ...] = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512)


def _initial_h_q(t_end: float) -> int:
    """h0 = t_end / 64, in Q15 of the problem's own time units.

    h_q cannot reach 1.0 in those units, so on the long-window problems (t_end 200
    and 260) the initial step is the Q15 ceiling and the run needs at least
    t_end steps whatever the tolerance. That is a property of the encoding, not of
    the controller.
    """
    return max(1, min(Q15_MAX, int(round((t_end / 64.0) * 32768.0))))


def solve_adaptive_q15(name: str, scale: float, tol_q: float, table: FactorTable = FTAB33,
                       arithmetic: str = "q15",
                       max_attempts: int = Q15_MAX_ATTEMPTS) -> dict:
    """One adaptive run of the BS32 pair on a validation problem.

    `arithmetic="q15"` runs the pair through the pinned Q15 primitives with FSAL
    reuse, accepting a step iff max_m |E_m| <= tol_q with tol_q a Q15 constant in
    LSB of the scaled state. `arithmetic="float"` short-circuits to the float
    prototype with tol_q read as the float tolerance, so the frozen curve can be
    reproduced before any Q15 number here is believed.

    Failure is data: the attempt cap, the 1-LSB step floor and a Q15 overflow all
    come back as a status with whatever counters were reached.
    """
    from rk_harness import validation as V

    t_end = V.PROBLEMS[name].t_end
    if arithmetic == "float":
        from rk_harness.prototypes.adaptive import solve_adaptive
        out: dict = {"problem": name, "arithmetic": "float", "tol": float(tol_q),
                     "status": "ok"}
        try:
            res = solve_adaptive(BOGACKI_SHAMPINE_32, V.FLOAT_RHS[name], V.Y0_PHYS[name],
                                 0.0, t_end, rtol=float(tol_q), atol=float(tol_q),
                                 h0=t_end / 64.0, max_attempts=max_attempts)
        except RuntimeError as e:
            out["status"] = "underflow" if "underflow" in str(e) else "attempt_cap"
            return out
        except (OverflowError, ZeroDivisionError, ValueError):
            out["status"] = "overflow"
            return out
        out.update({
            "n_accepted": res.n_accepted, "n_rejected": res.n_rejected,
            "n_fevals": res.n_fevals, "attempts": res.n_accepted + res.n_rejected,
            "achieved_error": V.validation_error(name, res.y),
        })
        return out
    if arithmetic != "q15":
        raise ValueError(f"arithmetic must be 'q15' or 'float', got {arithmetic!r}")

    tol_int = int(tol_q)
    if tol_int < 1:
        raise ValueError("tol_q is in LSB of the scaled state and must be at least 1")

    f = make_q15_rhs(V.FLOAT_RHS[name], scale, 1.0)
    y = to_q15_state(V.Y0_PHYS[name], scale)
    states = range(len(y))
    clock = TimeQ31(t_end)
    stats = new_floor_stats()

    h_q = _initial_h_q(t_end)
    e_prev = 0
    k1: tuple[int, ...] | None = None
    n_acc = n_rej = n_fev = attempts = 0
    n_clamped_high = 0
    n_overflow_rejected = 0
    status = "ok"
    max_abs_state = max(abs(v) for v in y)

    try:
        while clock.reg < TIME_LAST:
            if attempts >= max_attempts:
                status = "attempt_cap"
                break
            attempts += 1
            remaining = TIME_LAST - clock.reg
            inc = clock.increment(h_q)
            final = inc >= remaining
            h_step = clock.steps_for(remaining) if final else h_q
            h_f = h_step / 32768.0
            t_f = clock.recover_float()

            # A trial step whose stage inputs, derivatives or products leave int16 is a
            # rejection, not a run-ending failure: the state is unchanged and a smaller
            # h is exactly the answer. It stops being recoverable once h_q is already
            # 1 LSB, which is the Q15 wall, and that is reported as a status. The count
            # is kept separately so an overflow-driven rejection is never mistaken for
            # an accuracy-driven one.
            try:
                hk: list[tuple[int, ...]] = []
                ks: list[tuple[int, ...]] = []
                for i in range(4):
                    if i == 0 and k1 is not None:
                        k_i = k1
                    else:
                        acc = y
                        row = A_REPS[i]
                        for j in range(i):
                            rep = row[j]
                            if rep is None:
                                continue
                            hkj = hk[j]
                            acc = tuple(q15_add(acc[m], q15_apply(hkj[m], rep.m, rep.s))
                                        for m in states)
                        for v in acc:
                            if abs(v) > max_abs_state:
                                max_abs_state = abs(v)
                        k_i = tuple(f(t_f + float(BS32_C[i]) * h_f, acc))
                        n_fev += 1
                    ks.append(k_i)
                    hk.append(tuple(q15_mul(kk, h_step) for kk in k_i))

                y_new = y
                for i in range(4):
                    rep = B_REPS[i]
                    if rep is None:
                        continue
                    hki = hk[i]
                    y_new = tuple(q15_add(y_new[m], q15_apply(hki[m], rep.m, rep.s))
                                  for m in states)
            except Q15OverflowError:
                n_rej += 1
                n_overflow_rejected += 1
                k1 = None                         # nothing from the failed attempt is reusable
                if h_q <= 1:
                    status = "overflow"
                    break
                h_q = max(1, apply_factor(h_q, step_factor(NU_MIN, table), table.q_bits))
                continue

            est = q15_estimate(hk, D_REPS, stats)
            abs_e = max(abs(v) for v in est)
            e = msb(max(abs_e, 1)) - msb(max(tol_int, 1))

            if abs_e <= tol_int:
                n_acc += 1
                y = y_new
                for v in y:
                    if abs(v) > max_abs_state:
                        max_abs_state = abs(v)
                if final:
                    clock.reg = TIME_LAST
                else:
                    clock.advance(h_step)
                k1 = ks[-1]                       # FSAL: c ends at 1 and the last A row is b
                nu = e_prev - 2 * e
                e_prev = e
                grown = apply_factor(h_q, step_factor(nu, table), table.q_bits)
                if grown >= Q15_MAX:
                    n_clamped_high += 1
                h_q = max(1, grown)
            else:
                n_rej += 1
                k1 = ks[0]                        # y unchanged, so stage one still holds
                shrunk = apply_factor(h_q, step_factor(-2 * e, table), table.q_bits)
                shrunk = min(h_q, shrunk)         # never grow on a rejection
                if shrunk < 1:
                    shrunk = 1
                if shrunk == h_q and h_q == 1:
                    status = "step_underflow"
                    break
                h_q = shrunk
    except Q15AdaptiveError:
        status = "register_overflow"

    out = {
        "problem": name,
        "arithmetic": "q15",
        "tol_q": tol_int,
        "table": table.name,
        "status": status,
        "n_accepted": n_acc,
        "n_rejected": n_rej,
        "n_fevals": n_fev,
        "attempts": attempts,
        "h_q_final": h_q,
        "h_q_initial": _initial_h_q(t_end),
        "h_q_clamped_high": n_clamped_high,
        "overflow_rejected": n_overflow_rejected,
        "max_abs_state": max_abs_state,
        "time_register": clock.as_dict(),
        "floor_stats": stats,
        "mean_bias_lsb_total": stats.total_bias_lsb(),
    }
    if status == "ok":
        out["achieved_error"] = V.validation_error(name, to_physical(y, scale))
    else:
        out["achieved_error"] = None
    return out


# --------------------------------------------------------------------------- (i) the reference listing

def reference_sections() -> dict[str, list[str]]:
    """The ARMv6-M listing, split so each part can be priced on its own.

    Written in the style of fixtures/known_sequence.s, with cycle counts in
    trailing comments, and using only the eight mnemonics costmodel prices. This
    is a listing and a number; it becomes a pinned fixture only at the epoch-2
    boundary, so fixtures/known_sequence.s is not touched.
    """
    est: list[str] = []
    for i, rep in enumerate(D_REPS):
        est.extend([
            f"    LDR   r0, [r4, #{4 * i}]    @ 2   hk[{i}][m]",
            f"    LDR   r1, [r5, #{4 * i}]    @ 2   d[{i}].m = {rep.m}",
            "    MULS  r0, r0, r1       @ 1   (32-cycle variant: 32)",
            f"    ASRS  r0, r0, #{rep.s}      @ 1   d[{i}].s = {rep.s}, floor",
            "    ADDS  r2, r2, r0       @ 1   int32 accumulator",
        ])
    compare = [
        "    SUBS  r3, r2, r6       @ 1   |E| - tol_q; SUBS sets the flags, and CMP "
        "is not in the priced set",
    ]
    scan: list[str] = []
    for label, iters in (("int32 accumulator", MSB_MAX_ITERS), ("int16 tol_q", 15)):
        scan.append(f"    @ bit scan, worst case, {label}: {iters} iterations")
        for _ in range(iters):
            scan.extend([
                "    LSRS  r0, r0, #1       @ 1",
                "    ADDS  r1, r1, #1       @ 1",
            ])
    table = [
        "    LSLS  r0, r0, #1       @ 1   nu to a halfword offset",
        "    LDR   r1, [r7, r0]     @ 2   ftab[clamp(nu)], Q14",
    ]
    update = [
        "    MULS  r2, r2, r1       @ 1   h_q * factor   (32-cycle variant: 32)",
        f"    ASRS  r2, r2, #{FACTOR_Q_BITS}      @ 1   Q{FACTOR_Q_BITS} table, "
        f"so the shift is {FACTOR_Q_BITS} and not 15",
        "    STR   r2, [r8, #0]     @ 2   the new h_q",
    ]
    return {"estimate": est, "compare": compare, "bit_scan": scan,
            "table_load": table, "step_update": update}


def reference_lines() -> list[str]:
    out: list[str] = []
    for lines in reference_sections().values():
        out.extend(lines)
    return out


BRANCH_ALLOWANCE: dict = {
    "conditional_branches": MSB_MAX_ITERS + 15 + 1,
    "what": "one conditional branch per bit-scan iteration, worst case, plus the "
            "acceptance test; costmodel prices no branch class, so this is stated "
            "beside the cycle count and is not folded into it",
    "consequence": "the analytic per-attempt figure is an upper bound rather than an "
                   "exact count, which is a stated change to what a published cycle "
                   "number means",
}


def sequence_costs() -> dict:
    lines = reference_lines()
    per_section = {name: {m.name: count_sequence(sec, m)
                          for m in (M0PLUS_FAST, M0PLUS_SLOW, AVR_APPROX)}
                   for name, sec in reference_sections().items()}
    return {
        "total": {m.name: count_sequence(lines, m)
                  for m in (M0PLUS_FAST, M0PLUS_SLOW, AVR_APPROX)},
        "per_section": per_section,
        "lines": len([ln for ln in lines if ln.split("@", 1)[0].strip()]),
        "branch_allowance": BRANCH_ALLOWANCE,
    }


# --------------------------------------------------------------------------- (j) one artifact point

_FLATTENING_RULE = ("the largest tol_q on the ladder for which halving tol_q improves "
                    "the achieved error by less than 10 percent; null when the curve is "
                    "still improving at the tightest rung")

_BIAS_RULE = (
    "the median, over the ladder rungs that finished, of the four-term mean floor "
    "bias of one estimate. The pooled figure in floor_statistics is not the same "
    "number and is not the one to compare against a per-estimate claim: a rung that "
    "stalls at the 1-LSB step contributes tens of thousands of estimates whose terms "
    "underflow to zero, where the discarded quantity is a small fraction of an LSB "
    "and the whole term is lost instead.")


def _median(values: list[float]) -> float | None:
    vals = sorted(values)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _flattening_tol(rows: list[dict]) -> int | None:
    """Apply the rule above to a ladder sorted by tol_q ascending."""
    by_tol = {r["tol_q"]: r for r in rows}
    tols = sorted(by_tol)
    best: int | None = None
    for i in range(1, len(tols)):
        coarse, fine = by_tol[tols[i]], by_tol[tols[i - 1]]
        ec, ef = coarse.get("achieved_error"), fine.get("achieved_error")
        if ec is None or ef is None or not ec > 0.0:
            continue
        if (ec - ef) / ec < 0.10:
            best = tols[i] if best is None else max(best, tols[i])
    return best


def build_point(problem: str, scale_factor: tuple[int, int],
                tols: tuple[int, ...] = TOL_LADDER_LSB,
                max_attempts: int = Q15_MAX_ATTEMPTS) -> dict:
    """One document per (validation problem, scale factor). Pure function of the code
    and these parameters: no clock, no host detail, no unseeded randomness."""
    from rk_harness import validation as V

    num, den = scale_factor
    base = V.SCALE[problem]
    scale = base * num / den

    tables: dict[str, dict] = {}
    primary_stats = new_floor_stats()
    for table in (FTAB33, FTAB9):
        rows = []
        for tol in tols:
            r = solve_adaptive_q15(problem, scale, tol, table=table,
                                   max_attempts=max_attempts)
            stats: FloorStats = r.pop("floor_stats")
            if table is FTAB33:
                for i in range(len(primary_stats.counts)):
                    primary_stats.counts[i] += stats.counts[i]
                    primary_stats.remainder_sums[i] += stats.remainder_sums[i]
                    for k in range(HIST_BUCKETS):
                        primary_stats.histograms[i][k] += stats.histograms[i][k]
            rows.append({
                "tol_q": r["tol_q"],
                "status": r["status"],
                "n_accepted": r["n_accepted"],
                "n_rejected": r["n_rejected"],
                "n_fevals": r["n_fevals"],
                "attempts": r["attempts"],
                "achieved_error": r["achieved_error"],
                "h_q_final": r["h_q_final"],
                "h_q_clamped_high": r["h_q_clamped_high"],
                "overflow_rejected": r["overflow_rejected"],
                "max_abs_state": r["max_abs_state"],
                "mean_bias_lsb_total": r["mean_bias_lsb_total"],
                "final_register": r["time_register"]["final_register"],
            })
        tables[table.name] = {
            "entries": list(table.entries),
            "step_eighths": table.step_eighths,
            "q_bits": table.q_bits,
            "points": rows,
            "flattening_tol_lsb": _flattening_tol(rows),
        }

    a = tables[FTAB33.name]["points"]
    b = tables[FTAB9.name]["points"]
    tables_agree = all(x["n_accepted"] == y["n_accepted"] and x["n_rejected"] == y["n_rejected"]
                       for x, y in zip(a, b))
    finished = [r for r in a if r["status"] == "ok"]
    clock = TimeQ31(V.PROBLEMS[problem].t_end)

    return {
        "schema": "one document per (validation problem, scale factor): the exact error "
                  "weights and their fixed-point representation, a tolerance ladder in "
                  "LSB of the scaled state run under both controller tables, the measured "
                  "floor statistics of the estimate, the Q31 time register terms, and the "
                  "ARMv6-M reference sequence priced under all three cost models",
        "arithmetic": "Q15 fixed point throughout, on the pinned primitives in "
                      "rk_harness.fixedpoint, with floor multiply; the reference float "
                      "mode used to reproduce the frozen curve is float64",
        "note": "the bit scan is booked at its worst case and its branches cannot be "
                "priced by the pinned cost model, so the sequence figure is an upper "
                "bound rather than an exact count. The factor table is held in Q14 "
                "because a Q15 table cannot represent a factor of 1.0 at all; the "
                "encoding is an open item in EPOCH2-DESIGN section 4.",
        "problem": problem,
        "scale_factor": f"{num}/{den}",
        "base_scale": base,
        "scale": scale,
        "t_end": V.PROBLEMS[problem].t_end,
        "n_states": V.PROBLEMS[problem].n_states,
        "pair": {
            "name": BOGACKI_SHAMPINE_32.name,
            "b": [str(x) for x in BS32_B],
            "b_hat": [str(x) for x in BS32_B_HAT],
            "c": [str(x) for x in BS32_C],
        },
        "error_weights": {
            "d": [str(x) for x in D_EXACT],
            "reps": [{"m": r.m, "s": r.s, "exact": r.exact, "csd_weight": r.csd_weight}
                     for r in D_REPS],
        },
        "encoding": {
            "factor_q_bits": FACTOR_Q_BITS,
            "factor_min": str(FAC_MIN),
            "factor_max": str(FAC_MAX),
            "safety": str(SAFETY),
            "why": "a factor of 1.0 is 32768 in Q15 and 2.0 is 65536, both outside "
                   "int16, so fixedpoint.q15_mul cannot carry the table the design "
                   "specifies; the update is (h_q * f) >> 14 with an int16 range check",
        },
        "tolerance_ladder_lsb": list(tols),
        "max_attempts": max_attempts,
        "tables": tables,
        "floor_statistics": primary_stats.as_dict(),
        "time_register": clock.as_dict(),
        "sequence_costs": sequence_costs(),
        "flattening_rule": _FLATTENING_RULE,
        "bias_rule": _BIAS_RULE,
        "summary": {
            "points": len(a),
            "finished": len(finished),
            "flattening_tol_lsb": tables[FTAB33.name]["flattening_tol_lsb"],
            "mean_bias_lsb_total": _median([r["mean_bias_lsb_total"] for r in finished]),
            "mean_bias_lsb_pooled": primary_stats.total_bias_lsb(),
            "overflow_rejected": sum(r["overflow_rejected"] for r in a),
            "tables_agree": tables_agree,
            "statuses": sorted({r["status"] for r in a} | {r["status"] for r in b}),
        },
    }


# --------------------------------------------------------------------------- (k) the curve artifact

CURVE_PROBLEMS: tuple[str, ...] = ("buck_converter",)
CURVE_SCALE_FACTORS: tuple[tuple[int, int], ...] = ((1, 1), (1, 2))


def build_curve(problems: tuple[str, ...] = CURVE_PROBLEMS,
                scale_factors: tuple[tuple[int, int], ...] = CURVE_SCALE_FACTORS,
                tols: tuple[int, ...] = TOL_LADDER_LSB) -> dict:
    return {
        "label": "preliminary Q15 adaptive prototype (epoch 2 side track, off-archive)",
        "generated_by": "rk_harness.prototypes.adaptive_q15.main; not verifier-pinned",
        "arithmetic": "Q15 fixed point on the pinned primitives, with floor multiply",
        "points": [build_point(p, sf, tols=tols) for p in problems for sf in scale_factors],
    }


def write_curve(problems: tuple[str, ...] = CURVE_PROBLEMS,
                scale_factors: tuple[tuple[int, int], ...] = CURVE_SCALE_FACTORS,
                tols: tuple[int, ...] = TOL_LADDER_LSB):
    out_dir = work_dir() / "prototypes"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "adaptive_q15_curve.json"
    doc = build_curve(problems, scale_factors, tols)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> int:
    print(write_curve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
