"""Methodology page for the findings site.

Long-form article describing how the harness works and how it was tested. The page
callable (sitegen._page) is injected by the caller; this module never imports sitegen,
so there is no circular import. All content is a fixed string: no wall clock, no
randomness, byte-identical output for every call.

Every number and filename in the article was checked against the repository at the
time of writing: rk_harness/fixedpoint.py, coeffrep.py, orderconditions.py,
problems.py, simulate.py, evaluator.py, verifier.py, costmodel.py, search.py,
enumeration.py, archive.py, ledger.py, quarantine.py, verifier_hash.py, directive.py,
falsification.py, entrypoint.sh, tests/, and docs/HANDOFF.md.
"""
from __future__ import annotations

TITLE = "Methodology"

_SUBTITLE = "How the harness computes, searches, verifies, and is tested"

_STYLE = """
<style>
.meth-infobox{float:right;width:320px;margin:4px 0 18px 24px;border-collapse:collapse;
  background:var(--surface-1,#fcfcfb);border:1px solid var(--line,#dedcd6);font-size:12.5px}
.meth-infobox caption{caption-side:top;font-weight:700;font-size:13.5px;text-align:left;
  padding:6px 2px;color:var(--text-1,#0b0b0b)}
.meth-infobox th,.meth-infobox td{padding:4px 8px;border-bottom:1px solid var(--line,#dedcd6);
  text-align:left;vertical-align:top;font-weight:400}
.meth-infobox th{width:42%;color:var(--text-2,#52514e);font-weight:600;font-size:12px}
@media(max-width:760px){.meth-infobox{float:none;width:100%;margin:12px 0}}
.meth-toc{background:var(--surface-1,#fcfcfb);border:1px solid var(--line,#dedcd6);
  border-radius:10px;padding:12px 18px 12px 40px;max-width:44ch;font-size:13.5px;margin:18px 0}
.meth-toc li{margin:3px 0}
.meth-scroll{overflow-x:auto}
.meth-eq{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;line-height:1.5;
  background:var(--surface-1,#fcfcfb);border:1px solid var(--line,#dedcd6);border-radius:8px;
  padding:10px 14px;overflow-x:auto;max-width:76ch}
.meth-refs{font-size:13px;max-width:82ch;padding-left:2.2em}
.meth-refs li{margin:4px 0}
sup.meth-cite{font-size:10.5px;line-height:0}
sup.meth-cite a{text-decoration:none}
.meth-checklist{max-width:76ch;font-size:14px}
.meth-checklist li{margin:4px 0}
</style>
"""

_LEAD = """
<p>rk-harness is a self-contained system that searches for explicit Runge-Kutta
tableaus minimising end-to-end integration error in Q15 fixed-point arithmetic at a
fixed cycle budget under an ARM Cortex-M0+ cost model. Classical tableaus were derived
for exact real arithmetic. On a microcontroller without a floating-point unit, at step
sizes a real controller would use, roundoff competes with truncation, and coefficients
carry a cycle cost that depends on their bit structure. The harness measures which
coefficient sets integrate a fixed suite of test problems most accurately when every
multiply is an integer multiply and every coefficient application is paid for in
cycles<sup class="meth-cite"><a href="#meth-ref-21">[21]</a></sup>.</p>

<p>The system has four layers. A deterministic numerical core provides the fixed-point
primitives, the order-condition algebra over exact rationals, a Q15 simulator, an
evaluator, a nine-check verifier, and an analytic cycle-cost model. A search layer
produces candidate tableaus: exhaustive enumeration in the two early phases, CMA-ES
afterwards. An append-only archive stores every verified candidate with a mechanically
assigned evidence tier. Finally, a narrowly scoped language-model integration may steer
the search through schema-validated directives, propose hypotheses in a closed predicate
grammar, and stage new test problems into a quarantine; it can never score, tier, or
verify anything. Every published number is produced by code.</p>

<p>This page documents mechanisms and fixed constants, not outcomes: measured results
of the long-running search live on the other pages of this site. Each specific claim
below (a range, a count, a threshold, a formula) traces to a file in the rk-harness
repository or to a section of the frozen specification, docs/HANDOFF.md, cited in
brackets like <sup class="meth-cite"><a href="#meth-ref-21">[21]</a></sup>.</p>
"""

_INFOBOX = """
<table class="infobox meth-infobox">
<caption>rk-harness</caption>
<tr><th>Object of study</th><td>Explicit Runge-Kutta tableaus, orders 1 to 4, stages 2 to 6</td></tr>
<tr><th>State arithmetic</th><td>Q15: int16 in [-32768, 32767], scale 2<sup>-15</sup></td></tr>
<tr><th>Coefficient form</th><td>m / 2<sup>s</sup> with |m| &le; 32767, 0 &le; s &le; 20</td></tr>
<tr><th>Target</th><td>ARM Cortex-M0+, analytic cycle model, two multiplier variants; AVR model advisory only</td></tr>
<tr><th>Evaluation budget</th><td>65,536 cycles per problem run</td></tr>
<tr><th>Test problems</th><td>7 fixed (3 search, 4 held out)</td></tr>
<tr><th>Phases</th><td>0 and 1 exhaustive enumeration; 2 and 3 CMA-ES</td></tr>
<tr><th>Archive</th><td>Append-only JSONL; MAP-Elites grid per order, keyed by stages and cycle bucket</td></tr>
<tr><th>Verification gate</th><td>Nine ordered checks, pure code, never raises</td></tr>
<tr><th>Integrity pin</th><td>sha256 over ten files, checked at container start</td></tr>
<tr><th>Start-up gate</th><td>Golden tests G1-G20 plus canaries K1 and K2</td></tr>
</table>
"""

_TOC = """
<ol class="meth-toc">
<li><a href="#meth-number-system">Number system</a></li>
<li><a href="#meth-test-problems">Test problems</a></li>
<li><a href="#meth-simulation">Simulation and evaluation</a></li>
<li><a href="#meth-measured-order">Measured order</a></li>
<li><a href="#meth-cost-model">Cost model</a></li>
<li><a href="#meth-search">Search</a></li>
<li><a href="#meth-enumeration">Exhaustive enumeration</a></li>
<li><a href="#meth-archive">Archive</a></li>
<li><a href="#meth-ledger">Hypothesis ledger</a></li>
<li><a href="#meth-falsification">Falsification protocol</a></li>
<li><a href="#meth-trust">Model integration and trust boundaries</a></li>
<li><a href="#meth-testing">Testing</a></li>
<li><a href="#meth-reproducibility">Reproducibility</a></li>
<li><a href="#meth-limitations">Limitations</a></li>
<li><a href="#meth-references">References</a></li>
</ol>
"""

_S1 = """
<h2 id="meth-number-system">1. Number system</h2>

<p>States are Q15: 16-bit signed integers in [-32768, 32767], where the integer q
represents the real value q / 32768, so the representable range is [-1, 1) with a
resolution of 2<sup>-15</sup> (about 3.05e-05). Conversion from float rounds half to
even and raises on any non-finite input or out-of-range result. Multiplication is
<code>(a * b) &gt;&gt; 15</code> using an arithmetic right shift, which floors toward
negative infinity. This matches the ARM <code>ASRS</code> instruction and deliberately
does not match C integer division on negatives, which truncates toward zero. The two
conventions disagree on every negative product with a nonzero remainder: for example
q15_mul(-1, 1) is -1 under floor and would be 0 under truncation. Getting this wrong
would bias every result in the project by a consistent amount, so the divergence
vectors are pinned as fixtures<sup class="meth-cite"><a href="#meth-ref-1">[1]</a></sup>.</p>

<p>Nothing saturates and nothing wraps. Addition and multiplication range-check their
operands and results, and any value outside int16 raises <code>Q15OverflowError</code>,
which the evaluation pipeline converts into a verifier rejection
(<code>Q15_OVERFLOW</code>) rather than a silent wrong answer. Exactly one in-range
operand pair overflows the multiply: q15_mul(-32768, -32768) evaluates to 32768, one
past the top of int16. The no-saturation choice mirrors the hardware: Cortex-M0+ has no
<code>SSAT</code> instruction, and a software saturation would need a compare and
branch, destroying both branchlessness and exact cycle counting<sup class="meth-cite"><a
href="#meth-ref-1">[1]</a></sup>.</p>

<p>Coefficients are not Q15. Q15 spans [-1, 1), while the classical rk4 tableau
contains 1 and kutta3 contains 2. Each coefficient is instead stored as an
integer-and-shift pair, value = m / 2<sup>s</sup>, with |m| &le; 32767 and 0 &le; s
&le; 20, and applied to a Q15 value as <code>(v * m) &gt;&gt; s</code>. When no exact
pair exists (1/3, for instance, becomes 21845 / 2<sup>16</sup>), the closest pair is
used and the gap is recorded as <code>coeff_quant_error</code>, a measured property,
never a rejection<sup class="meth-cite"><a href="#meth-ref-2">[2]</a></sup>.</p>

<h3>The floor bias</h3>

<p>Floor rounding is one-sided: the computed product is never above the exact product,
and the discarded low bits average close to half of one least-significant bit, a
systematic deficit of about 0.5 LSB per term. Round-to-nearest would make the per-term
error zero-mean, so errors would partially cancel over a trajectory; flooring instead
accumulates the bias linearly across every coefficient application and every step. A
fixed-step integration at the standard budget performs thousands of such products, so
at small step sizes this one-sided drift, not roundoff variance, dominates the Q15
error. The harness reproduces the hardware semantics on purpose: shipped code on this
target would floor, so the measurement must floor too. The bias is part of the object
of study, and one of the ways a tableau can win here is by needing fewer biased
operations per unit of accuracy<sup class="meth-cite"><a href="#meth-ref-1">[1]</a><a
href="#meth-ref-21">[21]</a></sup>.</p>
"""

_S2 = """
<h2 id="meth-test-problems">2. Test problems</h2>

<p>Seven fixed initial-value problems drive every error measurement. Three form the
search set, which the optimizer sees; four are held out to detect overfitting. Each
problem carries a float64 right-hand side in physical units, a power-of-two scale
factor mapping physical state into Q15, a reference solution, and a family label used
by the tier rules. Peaks and scales come from the pinned fixture
<code>fixtures/problems.json</code><sup class="meth-cite"><a href="#meth-ref-3">[3]</a></sup>.</p>

<div class="meth-scroll">
<table>
<tr><th>Problem</th><th>Set</th><th>Dynamics</th><th>States</th><th>t_end</th><th>Family</th><th>Scale</th><th>Reference</th></tr>
<tr><td>dahlquist</td><td>search</td><td>y' = -y, y(0) = 1</td><td>1</td><td>10</td><td>linear</td><td>2<sup>-2</sup></td><td>exp(-t)</td></tr>
<tr><td>damped_osc</td><td>search</td><td>x'' + 2&zeta;&omega;x' + &omega;&sup2;x = 0, &zeta; = 0.1, &omega; = 1</td><td>2</td><td>40</td><td>oscillatory</td><td>2<sup>-2</sup></td><td>closed form</td></tr>
<tr><td>vanderpol_mild</td><td>search</td><td>x'' = &mu;(1 - x&sup2;)x' - x, &mu; = 0.5</td><td>2</td><td>20</td><td>nonlinear</td><td>2<sup>-4</sup></td><td>mpmath, 30 digits</td></tr>
<tr><td>pendulum</td><td>held out</td><td>&theta;'' = -sin &theta;, &theta;(0) = 1 rad</td><td>2</td><td>60</td><td>nonlinear</td><td>2<sup>-2</sup></td><td>energy invariant</td></tr>
<tr><td>dc_motor</td><td>held out</td><td>affine RL motor: R = 2, L = 0.5, K<sub>e</sub> = K<sub>t</sub> = 0.1, B = J = 0.02, V = 1</td><td>2</td><td>5</td><td>linear</td><td>2<sup>-3</sup></td><td>matrix exponential</td></tr>
<tr><td>rc_thermal</td><td>held out</td><td>y' = Ay, 3-compartment linear network</td><td>3</td><td>4</td><td>stiff</td><td>2<sup>-2</sup></td><td>matrix exponential</td></tr>
<tr><td>quaternion</td><td>held out</td><td>q' = &frac12;&Omega;(&omega;)q, body rates (0.3, 0.2, 0.5)</td><td>4</td><td>30</td><td>geometric</td><td>2<sup>-2</sup></td><td>norm invariant</td></tr>
</table>
</div>

<p>The rc_thermal system matrix has rows (-11, 10, 0), (5, -6, 1), (0, 2, -2), with
eigenvalues -16.047758, -2.723435 and -0.228807, a stiffness ratio of about 70. The
error metric depends on the problem. The default is the Euclidean distance between the
final Q15 state (converted back to physical units) and the reference solution at
t_end, divided by the problem's recorded peak amplitude. Two held-out problems use
structural invariants instead: pendulum measures relative energy drift |E - E0| / E0
with E = &omega;&sup2;/2 + (1 - cos &theta;) and E0 = 1 - cos(1), and quaternion
measures unit-norm drift, the absolute difference between the final quaternion norm
and 1<sup class="meth-cite"><a href="#meth-ref-3">[3]</a></sup>.</p>

<h3>Derivative scaling</h3>

<p>The Q15 right-hand side wraps the physical float64 derivative: the Q15 state is
converted to physical units (q / 32768 / scale), the derivative is evaluated in
float64, and the result is scaled back and converted to Q15, raising on any component
that does not fit int16. Derivatives can be larger than states. rc_thermal's
derivative at t = 0 is (-11, 5, 0) in physical units; multiplied by the state scale
0.25 that is -2.75, outside Q15. Its derivative is therefore stored with an extra
factor <code>DERIV_SCALE = 1/8</code> (every other problem uses 1), and the step
undoes it by computing the step-size constant as h_q = q15(h / DERIV_SCALE), so the
product of scaled derivative and inflated step size reproduces h times the true
derivative. The compensation is exact because both factors are powers of
two<sup class="meth-cite"><a href="#meth-ref-3">[3]</a><a href="#meth-ref-4">[4]</a></sup>.</p>
"""

_S3 = """
<h2 id="meth-simulation">3. Simulation and evaluation</h2>

<p>The Q15 integrator takes n equal steps of size h = t_end / n. Within a step, each
stage input starts from the current state; for every nonzero A entry, the stored
h-scaled stage derivative is multiplied by the coefficient's (m, s) pair and added,
each operation a range-checked Q15 primitive. The stage derivative is then evaluated
and immediately multiplied by h_q, so what is stored per stage is h times the
derivative, ready for coefficient application. The final combination applies the b
weights the same way. The simulator tracks the maximum absolute integer value seen
across every state and every intermediate stage input; that maximum feeds the overflow
margin below. Any Q15 overflow anywhere aborts the run<sup class="meth-cite"><a
href="#meth-ref-4">[4]</a></sup>.</p>

<p>A separate float64 twin of the integrator exists for the convergence study only. It
accumulates coefficient sums in float64 and multiplies by h once per stage, and it
plays no part in scoring Q15 error<sup class="meth-cite"><a href="#meth-ref-4">[4]</a></sup>.</p>

<p>Methods are always compared at an equal cycle budget, never at an equal step size.
For a tableau costing k cycles per step under a given cost model, the step count is n
= B // k with budget B = 65,536 cycles. A two-stage method therefore takes roughly
twice the steps of a four-stage method for the same cost. Comparing at equal h instead
is the most common route to a wrong conclusion in this problem domain, because it
hands expensive methods free extra work<sup class="meth-cite"><a href="#meth-ref-5">[5]</a></sup>.</p>

<p>The evaluator returns a ScoreVector: measured order and its fit-point count, the
error constant (the L2 norm of the residuals one order above the achieved order),
real and imaginary stability extents, per-model cycle counts, total CSD weight,
maximum coefficient quantisation error, search-set and held-out RMS errors, an
overflow margin, and a per-problem error map. Errors are computed under all three
cost models; the m0plus_fast results are primary, while the m0plus_slow and
avr_approx runs are stored as prefixed archive columns ("slow:", "avr_approx:") for
analysis only. The overflow margin is 1 / (2 &middot; max|q| / 32768): it exceeds 1.0
exactly when every state and intermediate stayed below half of full scale, which is
the headroom needed to survive twice the nominal amplitude<sup class="meth-cite"><a
href="#meth-ref-5">[5]</a></sup>.</p>

<p>The evaluator never raises. Internal failures become inf entries, a zero margin, or
a None measured order. The verifier then applies nine checks in a fixed order,
returning the earliest failure as a coded rejection: NOT_EXPLICIT,
ROW_SUM_INCONSISTENT, DYADIC_IMPOSSIBLE, ORDER_NOT_MET, COEFF_UNREPRESENTABLE (the
five cheap symbolic checks, run before any simulation), then Q15_OVERFLOW (margin
&le; 1.0), UNSTABLE (real stability extent above -0.5), NO_ASYMPTOTIC_WINDOW
(measured order is None), and NAN_OR_INF. The NAN_OR_INF check requires the six
headline scalars and every primary per-problem error to be finite; the prefixed
"slow:" and "avr_approx:" columns are exempt, because an expensive method can
legitimately receive too few steps under a 32-cycle multiplier and those columns carry
no claims. The verifier never raises, never calls a language model, never opens a
socket, and never writes<sup class="meth-cite"><a href="#meth-ref-6">[6]</a></sup>.</p>
"""

_S4 = """
<h2 id="meth-measured-order">4. Measured order</h2>

<p>Symbolic order (exact rational residuals over rooted trees) says what a tableau
should do; measured order says what its implementation actually does. The measurement
runs the float64 integrator on dahlquist with t_end = 10 and records the absolute
final-state error against exp(-10) at the twelve step counts n = 8 &middot;
2<sup>k</sup> for k = 0 to 11. Between consecutive halvings it forms local slopes
log(e<sub>i</sub>/e<sub>i+1</sub>) / log(h<sub>i</sub>/h<sub>i+1</sub>), keeping only
points whose error is finite and above the floor 1e-12. It then finds the longest
consecutive run of slopes whose spread (maximum minus minimum) stays within 0.08,
breaking ties toward the earliest run, and fits a least-squares line to log error
against log h over that run. The slope of the fit is the measured order, reported
together with the number of points in the run. If no run of at least two slopes
exists, the result is None and the verifier rejects the tableau with
NO_ASYMPTOTIC_WINDOW<sup class="meth-cite"><a href="#meth-ref-5">[5]</a></sup>.</p>

<p>This rule locates the asymptotic window instead of assuming where it sits. The
specification records verified values for the classical baselines, reproduced by the
golden tests: euler measures 0.9849 over 7 points, heun2 measures 2.0109 over 8,
kutta3 measures 3.0404 over 5, and rk4 measures 4.0706 over 3. rk4 descends into the
float64 roundoff floor quickly enough that only three points survive, which is why the
rk4 golden tolerance is &plusmn;0.10 rather than &plusmn;0.05: mild pre-asymptotic
contamination is expected, and a broken implementation reads 2.0 or 3.0, not
4.07<sup class="meth-cite"><a href="#meth-ref-5">[5]</a><a href="#meth-ref-17">[17]</a></sup>.</p>

<p>The convergence study runs in float64 rather than Q15 because Q15 roundoff would
floor the observed order for every method; the study asks what the coefficients
converge like, and the Q15 error is measured separately by the problem
runs<sup class="meth-cite"><a href="#meth-ref-5">[5]</a></sup>.</p>
"""

_S5 = """
<h2 id="meth-cost-model">5. Cost model</h2>

<p>Costs are counted analytically. There is no compiler or emulator in the loop: the
cycle count is a pure function of the tableau, evaluated identically on every machine.
Three models assign cycles to five operation classes<sup class="meth-cite"><a
href="#meth-ref-7">[7]</a></sup>:</p>

<div class="meth-scroll">
<table>
<tr><th>Model</th><th>mul</th><th>add</th><th>shift</th><th>load</th><th>store</th><th>Role</th></tr>
<tr><td>m0plus_fast</td><td>1</td><td>1</td><td>1</td><td>2</td><td>2</td><td>Cortex-M0+ with single-cycle multiplier; primary</td></tr>
<tr><td>m0plus_slow</td><td>32</td><td>1</td><td>1</td><td>2</td><td>2</td><td>Cortex-M0+ with iterative multiplier; primary</td></tr>
<tr><td>avr_approx</td><td>14</td><td>2</td><td>8</td><td>2</td><td>2</td><td>8-bit AVR approximation; advisory only</td></tr>
</table>
</div>

<p>Applying one coefficient x with representation (m, s) to one Q15 value costs
nothing when x is 0 (the term is omitted) or &plusmn;1 (a copy whose sign folds into
the add). Otherwise the cost is the smaller of two implementations: a shift-add
expansion costing w shifts plus w - 1 adds, where w is the CSD weight of m (the
minimum number of nonzero signed powers of two in its non-adjacent form), or a
hardware multiply costing one mul plus one shift. Taking the minimum is the point of
the model: with a single-cycle multiplier the multiply nearly always wins and
coefficient structure barely matters, while with a 32-cycle multiplier the shift-add
expansion wins up to about w = 10, making low-CSD-weight coefficients dramatically
cheaper. Per stage whose A row has any nonzero entry, and once more for the b
combination, the model charges one load, then the coefficient cost plus one add per
nonzero entry, then one store, all multiplied by the state count. Derivative
evaluation is excluded: it is identical across methods at the same stage count, so it
only shifts every method by the same constant<sup class="meth-cite"><a
href="#meth-ref-7">[7]</a><a href="#meth-ref-2">[2]</a></sup>.</p>

<p>The model is cross-checked two ways. A pinned, hand-counted ARMv6-M assembly
sequence (fixtures/known_sequence.s) must count to exactly 13 cycles under
m0plus_fast and 75 under m0plus_slow. And a reference C emitter can print the step
function a compiler would see, for one-off human comparison against real compiler
output; the harness never compiles or executes it<sup class="meth-cite"><a
href="#meth-ref-7">[7]</a></sup>.</p>

<h3>The anchor comparison</h3>

<p>rk4 and rk38 are both four-stage order-4 methods with identical stability
polynomials; they differ only in coefficients. Under m0plus_fast, rk4 costs 33 cycles
per step per state and rk38 costs 36; under m0plus_slow, rk4 costs 85 and rk38 costs
64. The ordering reverses between two chips running the same instruction set. The
mechanism is visible in the weights: rk4's b vector (1/6, 1/3, 1/3, 1/6) is inexact
with CSD weight 8 per entry, while rk38's (1/8, 3/8, 3/8, 1/8) is exact with weights
1, 2, 2, 1. When a multiply costs 32 cycles that difference dominates; when it costs
1, rk4's sparser A matrix wins instead. These twelve numbers (three state counts,
two methods, two models) are pinned as golden test G21 and were verified before any
search ran; an implementation that fails to reproduce them has a broken cost
model<sup class="meth-cite"><a href="#meth-ref-7">[7]</a><a href="#meth-ref-17">[17]</a></sup>.</p>
"""

_S6 = """
<h2 id="meth-search">6. Search</h2>

<p>In the CMA-ES phases, a candidate is parameterised by s(s-1)/2 free entries of the
strictly lower triangle of A plus s guesses for b, where s is the stage count. Each
CMA-ES sample is projected onto the order conditions before it is scored. The A
entries are snapped to the dyadic lattice k / D, where D is the directive's
<code>dyadic_denominator_max</code> (a power of two, at most 32768); the abscissae c
are then the exact row sums of A. The b vector is not snapped. Instead the order
conditions up to the target order, which are linear in b once A and c are fixed, are
assembled as an exact linear system G b = r over Fractions (one row per rooted tree,
right-hand side 1/&gamma;(tree)) and solved by Gaussian elimination over Fractions.
Components determined by the system come out as exact rationals; leftover degrees of
freedom take the CMA guess snapped to the same dyadic lattice; an inconsistent system
returns no tableau<sup class="meth-cite"><a href="#meth-ref-9">[9]</a><a
href="#meth-ref-8">[8]</a></sup>.</p>

<h3>Why b is solved exactly</h3>

<p>Dyadic rationals (denominator a power of two) are closed under addition and
multiplication. If A is dyadic then so is c, and if b were snapped to the dyadic
lattice as well, every elementary weight of the tableau would be dyadic. But the
order-3 condition for the bushy tree requires &Sigma; b<sub>i</sub>c<sub>i</sub>&sup2;
= 1/3, and 1/3 is not dyadic. No fully dyadic explicit method can therefore exceed
order 2; the verifier encodes the same theorem as its DYADIC_IMPOSSIBLE rejection.
Snapping b would silently cap the entire search at order 2. Solving b exactly over
the rationals lets b absorb the non-dyadic content (weights like 1/6 appear as exact
Fractions, later applied through their closest (m, s) pair with the gap recorded as
quantisation error) while A stays on the cheap dyadic lattice<sup class="meth-cite"><a
href="#meth-ref-8">[8]</a><a href="#meth-ref-9">[9]</a></sup>.</p>

<h3>The outer loop</h3>

<p>The projection sits inside a CMA-ES loop (initial step size 0.3) whose fitness is
the RMS error over the three search problems at the 65,536-cycle budget, plus a
quadratic penalty of 1e6 times the sum of squared order-condition residuals at the
requested order, with a fitness of 1e9 assigned to candidates that overflow or cannot
be projected. Only the search set is ever evaluated inside this loop; the held-out
set is unreachable from the search module's import graph, which a canary test checks.
On stagnation the loop restarts CMA-ES with a derived seed (seed + 1000k for restart
k), and improving tableaus are deduplicated by content hash before being yielded.
Islands run this loop with different seeds; migration copies the record with the
lowest held-out error over the others, using scores already computed downstream by
the evaluator, not by the search itself<sup class="meth-cite"><a
href="#meth-ref-9">[9]</a></sup>.</p>

<p>Exact order-4 conditions are rarely solvable once A is snapped to a coarse dyadic
lattice, so a strict projector at order 4 would return nothing for months. The
projection therefore falls back: it tries the requested order, then steps down one
order at a time (never below 2) until the system solves exactly. The runner verifies
each candidate at the order it actually achieves, and the residual penalty still
pulls the population toward the requested order<sup class="meth-cite"><a
href="#meth-ref-9">[9]</a></sup>.</p>
"""

_S7 = """
<h2 id="meth-enumeration">7. Exhaustive enumeration</h2>

<p>The two early phases do not search at all; they enumerate their spaces completely,
which upgrades the result from a best-so-far to optimality within the enumerated
space. The findings site labels such entries "exhaustive" with the note "optimal
within the enumerated space", against "search result" for the CMA-ES
phases<sup class="meth-cite"><a href="#meth-ref-10">[10]</a><a
href="#meth-ref-20">[20]</a></sup>.</p>

<p>Phase 0 covers two-stage order-2 methods. The order conditions leave one free
parameter a<sub>21</sub>, with b<sub>2</sub> = 1 / (2a<sub>21</sub>) and b<sub>1</sub>
= 1 - b<sub>2</sub>. The parameter runs over the dyadic lattice m / 2<sup>s</sup>
with 0 &le; s &le; 6 and 0 &lt; |m / 2<sup>s</sup>| &le; 2, which contains exactly
256 distinct values. Filtering to candidates whose implied b weights are both exactly
representable leaves exactly 16 tableaus. Golden test G26 pins both facts, along with
the identity of the cheapest member under the slow model (the classical midpoint
method, at 11 cycles)<sup class="meth-cite"><a href="#meth-ref-10">[10]</a><a
href="#meth-ref-17">[17]</a></sup>.</p>

<p>Phase 1 covers the three-stage order-3 family, parameterised by the two abscissae
(c<sub>2</sub>, c<sub>3</sub>). Both run over the lattice with 0 &le; s &le; 8 and
magnitude at most 2, which contains 1,024 values, giving 1,048,576 ordered pairs,
well under the module's 100,000,000-candidate cap (if a future lattice exceeded the
cap, the code would fall back to CMA-ES and record that it did). Pairs are kept only
when c<sub>2</sub> &ne; 2/3, c<sub>3</sub> &ne; c<sub>2</sub>, and the induced
matrix entries a<sub>32</sub> = c<sub>3</sub>(c<sub>3</sub> - c<sub>2</sub>) /
(c<sub>2</sub>(2 - 3c<sub>2</sub>)) and a<sub>31</sub> = c<sub>3</sub> -
a<sub>32</sub>, along with c<sub>2</sub> itself, are exactly representable in (m, s)
form; the b weights then follow from the standard order-3 formulas and every emitted
tableau is asserted to satisfy the order-3 residuals exactly. Running the enumeration
yields 5,094 valid tableaus. The b weights are not required to be exact: their
quantisation error is part of what distinguishes candidates<sup class="meth-cite"><a
href="#meth-ref-10">[10]</a></sup>.</p>
"""

_S8 = """
<h2 id="meth-archive">8. Archive</h2>

<p>Every verified candidate becomes one JSON line in a per-day file
(archive/YYYY-MM-DD.jsonl, UTC date), appended with write-then-fsync. A record stores
the tableau (exact fractions), its sha256 content hash, the full score vector, the
mechanically assigned tier, the cycle id and seed, the verifier hash in force when it
was scored, the directive and hypothesis ids that motivated it (if any), and a UTC
timestamp. Replay rebuilds all derived state from the files alone, discarding any
line that fails JSON parsing or schema validation (a partial trailing line after a
crash is expected and dropped silently); a record whose stored hash disagrees with
its recomputed tableau hash is rejected. Tier strings outside the closed three-value
set fail validation<sup class="meth-cite"><a href="#meth-ref-11">[11]</a></sup>.</p>

<p>The working structure over the records is MAP-Elites: one grid per symbolic order
1 through 4, each cell keyed by (stage count, cycle bucket), where the bucket is a
log2 band of the m0plus_fast cycle count: below 16 is bucket 0, 16-31 is 1, 32-63 is
2, and so on up to bucket 7 for 1024 and above. Within a cell the elite is the record
with strictly the lowest held-out error; ties and non-finite comparisons keep the
earlier record. Replay also maintains Welford running statistics (count, mean, sum of
squared deviations, minimum) per (order, stages) cell for each model and metric,
which is what the hypothesis ledger reads<sup class="meth-cite"><a
href="#meth-ref-11">[11]</a></sup>.</p>

<h3>Tiers</h3>

<p>Tier assignment is pure code, applied when a record is written. A candidate is
<em>heldout_verified</em> when it improves on the incumbent elite in both search-set
and held-out error and improves at least one problem in each of two or more problem
families; it is <em>search_only</em> when it improves the search error but not the
held-out error (the signature of overfitting to the optimizer's own problems); it is
<em>unreplicated</em> otherwise, including when the cell had no incumbent. The tier
vocabulary is deliberately absent from every language-model prompt template, and a
planted tableau tuned to the search set must land in search_only; both properties are
canary tests. Problems admitted from quarantine additionally run in shadow mode for
10 cycles, evaluated and recorded, excluded from tier decisions, before they
count<sup class="meth-cite"><a href="#meth-ref-11">[11]</a><a
href="#meth-ref-13">[13]</a></sup>.</p>
"""

_S9 = """
<h2 id="meth-ledger">9. Hypothesis ledger</h2>

<p>The model may record falsifiable predictions, but only in a closed grammar over
archive statistics, and it never writes the verdict. A hypothesis is a JSON object
with a required id (H-digits), the cycle it was proposed, a statement, a proposed
mechanism, a control condition, a machine-checkable predicate, and a minimum sample
count. The predicate language is, in full<sup class="meth-cite"><a
href="#meth-ref-12">[12]</a></sup>:</p>

<pre class="meth-eq">expr    := term (("AND" | "OR") term)*
term    := field op field | field op number
field   := model "." cell "." metric
model   := "fast" | "slow" | "avr_approx"
cell    := "p" digit "s" digit
metric  := "heldout" | "search" | "cycles" | "order"
op      := "&lt;" | "&gt;" | "&lt;=" | "&gt;=" | "=="</pre>

<p>Nothing else parses. The parser is a hand-written tokenizer and recursive-descent
parser with a 2,000-character cap and a control-character ban; there is no dynamic
code execution anywhere in the module, which a grep canary enforces, and inputs like
a dunder call are syntax errors, not code. A field such as fast.p3s4.heldout resolves
against the archive's cell statistics: the minimum of the metric over every cycle
bucket at that (order, stages), except the order metric, which uses the mean<sup
class="meth-cite"><a href="#meth-ref-12">[12]</a></sup>.</p>

<p>After each cycle the runner evaluates open predicates against the archive. A
predicate referencing any cell with no records is <em>inconclusive</em>, never
refuted: an absent cell is absence of evidence, and treating it as refutation would
let the model settle hypotheses by never searching. The sample count of a predicate
is the smallest record count among the cells it references, and no verdict is issued
below the hypothesis's min_samples. For field-versus-field comparisons the evaluator
also computes Cohen's d from the pooled cell statistics, and an effect size below 0.2
forces <em>inconclusive</em> regardless of the comparison's truth value; the third
bucket must be reachable or everything drifts toward confirmation. Otherwise the
verdict is <em>supported</em> or <em>refuted</em>, appended to the ledger as a
resolution line with the sample count, effect size, and resolving cycle. Every model
call receives the accumulated list of refuted hypotheses, so refuted ideas are not
re-proposed in different words<sup class="meth-cite"><a href="#meth-ref-12">[12]</a><a
href="#meth-ref-21">[21]</a></sup>.</p>
"""

_S10 = """
<h2 id="meth-falsification">10. Falsification protocol</h2>

<p>Before the long run was allowed to start, the project's premise was itself put
under test. The premise has two load-bearing parts: coefficient arithmetic must be a
material fraction of the per-step cycle cost (otherwise no coefficient choice can
matter), and there must be a step-size regime, at practical step sizes, where Q15
roundoff rather than truncation dominates the error (otherwise exact-arithmetic
optimality carries over and the search is pointless)<sup class="meth-cite"><a
href="#meth-ref-19">[19]</a></sup>.</p>

<p>The experiment implements rk4 and heun2 in Q15 on damped_osc. It computes, under
both primary cost models, the fraction of one step's cycles spent on coefficient
arithmetic versus derivative evaluation, using analytic operation counts for the
derivative (for damped_osc: two loads, two multiplies, two shifts, one add, two
stores per evaluation). It then sweeps the step count over n = 8 &middot;
2<sup>k</sup> for k = 0 to 9 and records the Q15 error and the float64 error at each
n. The crossover is the largest h, scanning from coarse to fine steps, at which the
Q15 error stops improving while the float64 error is still falling: the visible onset
of the roundoff-dominated regime<sup class="meth-cite"><a href="#meth-ref-19">[19]</a></sup>.</p>

<p>The protocol commits to numeric decision criteria in advance. If the coefficient
fraction is below 15 percent for both methods and no crossover appears in the
practical range h between 1e-3 and 1, the verdict is "kill": stop the project, keep
the benchmark. If the fraction is at least 30 percent for both and a crossover
appears in that range, the verdict is "proceed". Anything between is "mixed". The
verdict is computed by code from the measured values, written to the work directory,
and published on this site either way; a clean negative is a real
finding<sup class="meth-cite"><a href="#meth-ref-19">[19]</a></sup>.</p>
"""

_S11 = """
<h2 id="meth-trust">11. Model integration and trust boundaries</h2>

<p>A language model participates in the run, and the design assumes it will try
shortcuts, so every path from model output into the system passes a validator, and
the scoring path is out of its reach entirely. The runner is the only module
permitted to talk to the model; a canary greps the import graph of every other module
to keep it that way<sup class="meth-cite"><a href="#meth-ref-15">[15]</a></sup>.</p>

<h3>Directives</h3>

<p>The model steers the search by returning exactly one JSON directive per call. The
directive is validated by a JSON schema with additionalProperties set to false at
every object level, plus hand-applied rules the schema cannot express: target order
in 1 to 4, a stage list of at most three values each between 2 and 6, force-zero
positions strictly below the diagonal, a dyadic denominator cap that is a power of
two between 2 and 32768, fraction-parseable fixed abscissae, islands 1 to 8, a
budget between 5 and 120 minutes, and a rationale of at most 500 characters. Unknown
keys are a rejection, not ignored: silently dropping a key is how a directive would
quietly do something other than what the model intended. A malformed directive is
discarded and logged, and a deterministic fallback (search the emptiest cell in the
current grid) runs instead. A directive can only narrow the search; it cannot change
the objective, the problems, the cost model, or the tier
rules<sup class="meth-cite"><a href="#meth-ref-15">[15]</a></sup>.</p>

<h3>Read-only harness and the pinned verifier hash</h3>

<p>The container mounts the rk-harness repository read-only. If the agent could edit
the scorer, the shortest path to a high score would be editing the scorer, disguised
as a plausible refactor. The entrypoint probes writability at start and refuses to
run if the mount is writable. Independently, a sha256 digest is computed over the
concatenation, in fixed order, of the ten files that determine every score:
rk_harness/coeffrep.py, orderconditions.py, verifier.py, costmodel.py, evaluator.py,
problems.py, and fixtures/classical.json, problems.json, q15.json,
known_sequence.s. The digest must equal the value pinned in the VERIFIER_HASH file
or the container exits before doing anything. Every archive record stores the hash
under which it was scored, so any historical change to the scoring code is
detectable per record<sup class="meth-cite"><a href="#meth-ref-14">[14]</a><a
href="#meth-ref-16">[16]</a></sup>.</p>

<h3>Credentials</h3>

<p>All pushes are performed from the host: the watchdog pushes the work and
findings repositories on a timer with the owner's own git credentials, and the
container pushes nothing. The GitHub credential never enters the container at all.
The env file the container receives is filtered on the host to drop the token
before docker run, the runner only commits into the mounted work and findings
checkouts, and a pre-flight check verifies the boundary directly: the filtered file
carries no token, and the environment inside a container started from it shows
none. The token itself is the owner's general-purpose fine-grained personal access
token with write access to all three repositories, harness included, which is why
the guarantee is placed at the container wall rather than in the token's scope; a
probe that exercises the token against the harness repository still runs, but as
an advisory check reported for information, not as a gate. Other secrets live in a
gitignored .env file on the host, are never baked into the container image, and
are read through a small module that never logs
values<sup class="meth-cite"><a href="#meth-ref-14">[14]</a><a
href="#meth-ref-21">[21]</a></sup>.</p>

<h3>Quarantine</h3>

<p>The one place model-written code executes is the problem quarantine. A proposed
derivative function is staged to the work repository, never into the harness, and
must pass six admission checks before it is loaded: byte-identical output across two
runs; an import allowlist of exactly the math module, enforced by walking the parsed
syntax tree (never by regular expressions), with banned builtins, no dunder access,
no imports inside function bodies, and a 20,000-byte source cap; 10,000 evaluations
in under two seconds; all states within [-1, 1) at twice nominal amplitude; a
reference solution, either supplied analytically or computed with mpmath at 30
digits; and a promotion gate requiring rk4, heun2, and midpoint to rank on the new
problem in the same order they rank on the existing held-out set, since a problem
where a low-order method overtakes rk4 is suspicious. Admitted problems join the
held-out set only, never the search set, so the optimizer cannot be steered toward a
problem the model invented, and they spend 10 cycles in shadow before affecting
tiers. If the model concludes the evaluator or cost model itself needs changing, it
can only write a proposal file and stop; that category of edit sits behind a human
gate<sup class="meth-cite"><a href="#meth-ref-13">[13]</a></sup>.</p>
"""

_S12 = """
<h2 id="meth-testing">12. Testing</h2>

<h3>Golden fixtures</h3>

<p>Sections 9 through 12 of the specification are ground truth computed and verified
before the implementation existed: the eight classical tableaus with their exact
coefficients, CSD totals, quantisation errors, and cycle counts under both primary
models; the nine order-5 residuals of rk4; the stability extents of the classical
methods (including the fact that euler, heun2, and midpoint have zero imaginary-axis
extent, so tests assert below 0.01 rather than equality); the rooted-tree counts 1,
1, 2, 4, 9, 20 against OEIS A000081; the twelve-number rk4-versus-rk38 anchor; the
Q15 multiplication vectors including the floor-versus-truncate divergence rows; the
seven problem definitions with measured peaks; and the hand-counted ARM assembly
sequence. These live verbatim in the four files under fixtures/ (classical.json,
problems.json, q15.json, known_sequence.s), are loaded by path, and are never
regenerated by any test. Spec-derived tests alone would merely ratify the spec; the
golden values give the tests external truth to check against<sup class="meth-cite"><a
href="#meth-ref-17">[17]</a><a href="#meth-ref-21">[21]</a></sup>.</p>

<h3>Acceptance identifiers and the container-start gate</h3>

<p>The acceptance criteria are grouped by identifier: G1-G27 golden results (orders,
residuals, measured orders, stability extents, tree counts, representations, cycle
counts, the phase 0 enumeration), F1-F21 fixed-point behaviour, V1-V11 verifier
verdicts, C1-C10 cost model properties, K1-K16 anti-gaming canaries, R1-R5 crash
recovery, and E1-E7 end-to-end properties including byte-identical same-seed runs.
K1 plants a tableau tuned to the search set and bad on the held-out set and requires
it to tier as search_only; it is the check that catches the whole project silently
overfitting while its dashboard looks healthy. At container start, the entrypoint
runs in order: the writability probe on the harness mount, the verifier hash
comparison, and a pytest selection of the golden tests G1 through G20 plus canaries
K1 and K2. Any failure exits with status 1 and the runner never
starts<sup class="meth-cite"><a href="#meth-ref-16">[16]</a><a
href="#meth-ref-17">[17]</a></sup>.</p>

<h3>Suites</h3>

<p>The pytest suite is organised in seven tiers, one file per tier, each written
against the frozen interface specification before or independently of the
implementation. Tier 1 covers fixedpoint, coeffrep, tableau construction, and the
cost model (95 test functions); tier 2 covers order conditions, problems, simulation,
the evaluator, and the verifier (105); tier 3 covers the archive, surrogate,
encourager, search, enumeration, directives, prompts, and the import-graph canaries
(149); tier 4 covers the ledger, quarantine, runner, recovery, site generator, and
dashboard (83); tier 5 covers the operational configuration and watch layer (10);
tier 6 covers the timestamp display formatting used across the site (8); and tier 7
covers this methodology page itself (14). That is 464 test functions in all; many
are parameterised over the classical methods or fixture rows, so collecting the
full suite yields 987 test cases. Expensive computations (full evaluations,
convergence studies) run once per module through shared fixtures and are marked
slow<sup class="meth-cite"><a
href="#meth-ref-17">[17]</a></sup>.</p>

<h3>Preflight</h3>

<p>A separate preflight script executes every machine-checkable item of the review
checklist (docs/REVIEW.md) end to end: it runs the suites, greps for forbidden
patterns, exercises the container checks when asked, prints a PASS, FAIL, MANUAL, or
SKIP status per item, and writes the signed report to docs/REVIEW-REPORT.md. Its
gating sections (the v2 regression checks, trust boundary, ground truth, the anchor
and enumeration results, and the falsification result) set a nonzero exit code on
any failure<sup class="meth-cite"><a href="#meth-ref-18">[18]</a></sup>.</p>
"""

_S13 = """
<h2 id="meth-reproducibility">13. Reproducibility</h2>

<p>The determinism guarantees are layered. The Q15 core is exact integer arithmetic
on Python integers, so it is platform-independent by construction; there is no
floating-point ambiguity in the object of study itself. The evaluator reads no wall
clock, spawns no threads, and draws no random numbers; the same tableau and budget
give an identical score vector every time, and the convergence study and stability
bisection use fixed grids (twelve step counts; 4,000 samples and 200 bisection
iterations). The verifier is a pure function that never raises. The search is seeded,
with restart seeds derived arithmetically from the island seed, and the enumerations
iterate their lattices in sorted order, so both layers replay
exactly<sup class="meth-cite"><a href="#meth-ref-5">[5]</a><a
href="#meth-ref-9">[9]</a></sup>.</p>

<p>Persistence is replayable. The archive is append-only JSONL whose derived state is
a pure function of file contents; the end-to-end acceptance test requires two runs
with the same seed to produce byte-identical archives. Tableaus have a canonical JSON
serialisation (fractions as numerator/denominator strings, sorted keys) whose sha256
digest is the content hash used for deduplication and record integrity, and every
record additionally stores the verifier hash pinning the exact scoring code that
produced it. Stored timestamps are UTC throughout; tests can pin the archive date
through an environment variable, and conversion to US Central time happens only in
human-facing display code, as a pure function of the stored value. The findings site
itself is regenerated from the archive with no wall-clock reads and no randomness,
so the same archive always produces byte-identical pages<sup class="meth-cite"><a
href="#meth-ref-11">[11]</a><a href="#meth-ref-20">[20]</a></sup>.</p>
"""

_S14 = """
<h2 id="meth-limitations">14. Limitations</h2>

<p>The cost model is analytic and has never been closed against silicon inside the
loop. It is anchored by a hand-counted assembly fixture and a reference C emitter a
human can compare against compiler output, but no measurement on a physical
Cortex-M0+ feeds back into scores. Derivative evaluation is excluded from the cycle
count, which is harmless when comparing methods at the same stage count and a real
simplification when stage counts differ, since the budget-to-steps conversion then
underweights the derivative work of high-stage methods. The AVR model is approximate
by its own definition (8-bit operand effects are folded into three constants) and is
confined to an advisory column; no claim rests on it<sup class="meth-cite"><a
href="#meth-ref-7">[7]</a></sup>.</p>

<p>Measured order comes from a single scalar problem (dahlquist) in float64, so it
certifies the implementation of the coefficients, not fixed-point behaviour across
the suite. Stability extents are sampled along the real and imaginary axes only,
which does not characterise the full stability region. Error is measured at the
final time against a reference or an invariant, not along the trajectory, so a
method could trade path accuracy for endpoint accuracy without penalty. The floor
semantics are one hardware convention; a target that rounds or saturates would shift
every Q15 result<sup class="meth-cite"><a href="#meth-ref-5">[5]</a><a
href="#meth-ref-1">[1]</a></sup>.</p>

<p>The search space is deliberately narrow: explicit fixed-step methods, symbolic
order at most 4, stages 2 to 6, coefficients within the (m, s) representation, A
entries on dyadic lattices no finer than 1/32768. Optimality statements from the
enumeration phases hold only within their enumerated lattices. The held-out set is
four problems across four families, so held-out verification is a strong filter
rather than a statistical guarantee, and the two-family improvement rule and the
Cohen's d threshold of 0.2 are conventions chosen in advance, not derived
quantities. All of these bounds are frozen in the specification precisely so that
results are comparable across the whole run<sup class="meth-cite"><a
href="#meth-ref-21">[21]</a></sup>.</p>
"""

_REFS = """
<h2 id="meth-references">References</h2>

<ol class="meth-refs">
<li id="meth-ref-1">rk_harness/fixedpoint.py; HANDOFF section 4.2 (Q15 semantics) and section 10 (divergence vectors).</li>
<li id="meth-ref-2">rk_harness/coeffrep.py; HANDOFF section 4.2b (coefficient representation, CSD weight).</li>
<li id="meth-ref-3">rk_harness/problems.py and fixtures/problems.json; HANDOFF section 11 (problem fixture).</li>
<li id="meth-ref-4">rk_harness/simulate.py (Q15 and float64 integrators, derivative scaling, budget-to-steps).</li>
<li id="meth-ref-5">rk_harness/evaluator.py; HANDOFF section 4.7 (equal-budget rule, measured order, stability extents).</li>
<li id="meth-ref-6">rk_harness/verifier.py; HANDOFF section 4.4 (nine ordered checks).</li>
<li id="meth-ref-7">rk_harness/costmodel.py, fixtures/known_sequence.s; HANDOFF sections 4.5 (counting rules), 9.5 (anchor comparison), 12 (assembly fixture).</li>
<li id="meth-ref-8">rk_harness/orderconditions.py; HANDOFF section 4.3 (rooted trees, dyadic impossibility).</li>
<li id="meth-ref-9">rk_harness/search.py; HANDOFF section 4.9 (CMA-ES, projection, islands).</li>
<li id="meth-ref-10">rk_harness/enumeration.py; HANDOFF sections 8 (phase table) and 9.6 (phase 0 count).</li>
<li id="meth-ref-11">rk_harness/archive.py; HANDOFF section 4.8 (grids, buckets, tiers, replay).</li>
<li id="meth-ref-12">rk_harness/ledger.py; HANDOFF section 6 (predicate grammar, verdicts, effect size).</li>
<li id="meth-ref-13">rk_harness/quarantine.py; HANDOFF section 7 (admission checks, shadow mode, proposal gate).</li>
<li id="meth-ref-14">rk_harness/verifier_hash.py, VERIFIER_HASH, rk_harness/credentials.py; HANDOFF sections 4.11 and 2.2.</li>
<li id="meth-ref-15">rk_harness/directive.py, rk_harness/runner.py; HANDOFF section 5 (directive schema and validation).</li>
<li id="meth-ref-16">entrypoint.sh; HANDOFF section 13.1 (start-up order: probe, hash, golden gate).</li>
<li id="meth-ref-17">tests/test_t1_fixedpoint_coeff_cost.py through tests/test_t7_methodology.py; HANDOFF section 14 (acceptance criteria G, F, V, C, K, R, E).</li>
<li id="meth-ref-18">scripts/preflight.py and docs/REVIEW-REPORT.md (executed review checklist).</li>
<li id="meth-ref-19">rk_harness/falsification.py; HANDOFF section 15 (criteria and sweep).</li>
<li id="meth-ref-20">rk_harness/sitegen.py; HANDOFF section 17 (auto-publish rules, determinism, labels).</li>
<li id="meth-ref-21">docs/HANDOFF.md (frozen specification; section numbers as cited above).</li>
</ol>
"""

_BODY = (
    _STYLE
    + _INFOBOX
    + _LEAD
    + _TOC
    + _S1 + _S2 + _S3 + _S4 + _S5 + _S6 + _S7
    + _S8 + _S9 + _S10 + _S11 + _S12 + _S13 + _S14
    + _REFS
)


def render_page(page) -> str:
    """Render the methodology article through the injected page callable.

    `page` is sitegen._page (or any callable with the same signature):
    page(title, body, active="", subtitle="") -> str.
    """
    return page(TITLE, _BODY, "methodology.html", _SUBTITLE)
