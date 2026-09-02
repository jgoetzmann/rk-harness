"""Methodology page for the findings site.

Long-form article describing the method: the experimental setup, what is measured
and how, the statistical protocol, the trust boundaries, the testing, and the
limits. System internals (Q15 arithmetic behaviour, cost model counting rules,
search machinery, enumeration space math, archive mechanics) are summarised here
and covered in depth on the overview architecture page; this page links there
rather than repeating them. The page callable (sitegen._page) is injected by the
caller; this module never imports sitegen, so there is no circular import. All
content is a fixed string: no wall clock, no randomness, byte-identical output
for every call.

Every number and filename in the article was checked against the repository at the
time of writing: rk_harness/fixedpoint.py, coeffrep.py, orderconditions.py,
problems.py, simulate.py, evaluator.py, verifier.py, costmodel.py, search.py,
enumeration.py, archive.py, ledger.py, quarantine.py, verifier_hash.py, directive.py,
falsification.py, entrypoint.sh, tests/, and docs/HANDOFF.md.
"""
from __future__ import annotations

TITLE = "methodology"

_SUBTITLE = "The method: setup, measurement, protocol, trust, testing"

_ARCH = "https://jgoetzmann.github.io/rk-overview/architecture.html"

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
.meth-refs{font-size:13px;max-width:82ch;padding-left:2.2em}
.meth-refs li{margin:4px 0}
sup.meth-cite{font-size:10.5px;line-height:0}
sup.meth-cite a{text-decoration:none}
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

<p>The scoring path is pure code: a deterministic numerical core, a search layer, and
an append-only archive with mechanically assigned evidence tiers. A narrowly scoped
language-model integration may steer the search and propose hypotheses; it can never
score, tier, or verify anything. Every published number is produced by code.</p>

<p>This page documents the method, not the machinery and not the outcomes. Measured
results live on the other pages of this site; how the system works internally (the
arithmetic, the cost model counting rules, the search and enumeration machinery, the
archive) is summarised where the method depends on it, with the full treatment on the
<a href=\"""" + _ARCH + """\">overview architecture page</a> and definitions in the
<a href="glossary.html">glossary</a>. Each specific claim below traces to a file in
the rk-harness repository or to a section of the frozen specification, docs/HANDOFF.md,
cited in brackets like <sup class="meth-cite"><a href="#meth-ref-21">[21]</a></sup>.</p>
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
<tr><th>Verification gate</th><td>Nine ordered checks, pure code, never raises</td></tr>
<tr><th>Effect threshold</th><td>Cohen's d &ge; 0.2, else inconclusive</td></tr>
<tr><th>Integrity pin</th><td>sha256 over ten files, checked at container start</td></tr>
<tr><th>Start-up gate</th><td>Golden tests G1-G20 plus canaries K1 and K2</td></tr>
</table>
"""

_TOC = """
<ol class="meth-toc">
<li><a href="#meth-setup">Experimental setup</a></li>
<li><a href="#meth-measurement">Measurement</a></li>
<li><a href="#meth-protocol">Statistical protocol</a></li>
<li><a href="#meth-trust">Verification and trust</a></li>
<li><a href="#meth-testing">Testing</a></li>
<li><a href="#meth-reproducibility">Reproducibility</a></li>
<li><a href="#meth-limitations">Limitations</a></li>
<li><a href="#meth-references">References</a></li>
</ol>
"""

_S1 = """
<h2 id="meth-setup">1. Experimental setup</h2>

<p>The object of study is the explicit Runge-Kutta tableau: symbolic orders 1 to 4,
stages 2 to 6, the A matrix on dyadic lattices no finer than 1/32768, and every
coefficient stored as an integer-and-shift pair, value = m / 2<sup>s</sup>, with
|m| &le; 32767 and 0 &le; s &le; 20. When no exact pair exists (1/3, for instance,
becomes 21845 / 2<sup>16</sup>), the closest pair is used and the gap is recorded as
<code>coeff_quant_error</code>, a measured property, never a
rejection<sup class="meth-cite"><a href="#meth-ref-2">[2]</a></sup>.</p>

<p>All state arithmetic is Q15: 16-bit signed integers at scale 2<sup>-15</sup>,
multiplied as <code>(a * b) &gt;&gt; 15</code> with an arithmetic right shift that
floors toward negative infinity, matching the ARM <code>ASRS</code> instruction and
deliberately not matching C truncation. Nothing saturates and nothing wraps: any
out-of-range value raises, and the evaluation pipeline converts the exception into a
verifier rejection rather than a silent wrong answer. The floor is one-sided, a
systematic deficit of about half a least-significant bit per product, and at small
step sizes that accumulated bias, not roundoff variance, dominates Q15 error; the
harness reproduces the hardware semantics on purpose, because shipped code on this
target would floor too. The exact semantics, the pinned divergence vectors, and the
bias mechanism are on the
<a href=\"""" + _ARCH + """#arithmetic\">architecture page</a> and under
<a href="glossary.html#floor-rounding">floor rounding</a> in the
glossary<sup class="meth-cite"><a href="#meth-ref-1">[1]</a><a
href="#meth-ref-21">[21]</a></sup>.</p>

<h3>Budget and cost accounting</h3>

<p>Methods are always compared at an equal cycle budget, never at an equal step size.
For a tableau costing k cycles per step under a given cost model, the step count is
n = B // k with budget B = 65,536 cycles, so a two-stage method takes roughly twice
the steps of a four-stage method for the same cost. Comparing at equal h instead is
the most common route to a wrong conclusion in this problem domain, because it hands
expensive methods free extra work<sup class="meth-cite"><a
href="#meth-ref-5">[5]</a></sup>.</p>

<p>Cycle counts are analytic: a pure function of the tableau, with no compiler or
emulator in the loop. Three models price five operation classes; m0plus_fast
(single-cycle multiplier) and m0plus_slow (32-cycle multiplier) are primary, and
avr_approx is advisory only. Each coefficient application is charged the cheaper of a
shift-add expansion, whose length is the coefficient's
<a href="glossary.html#csd-weight">CSD weight</a>, and a hardware multiply, which is
what makes coefficient bit structure matter under a slow multiplier. The model is
anchored by a hand-counted ARMv6-M assembly fixture and by the pinned rk4-versus-rk38
comparison; per-method tables are on the <a href="costmodel.html">cost model page</a>,
and the counting rules with their cross-checks are on the
<a href=\"""" + _ARCH + """#costmodel\">architecture page</a><sup class="meth-cite"><a
href="#meth-ref-7">[7]</a><a href="#meth-ref-2">[2]</a></sup>.</p>

<h3>Test problems and the held-out split</h3>

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

<p>rc_thermal is the stiff member, with a stiffness ratio of about 70; its derivative
exceeds Q15 range at the state scale, so it is stored with an extra power-of-two
factor (DERIV_SCALE = 1/8) that the step size undoes exactly. The scaling mechanism
is described on the <a href=\"""" + _ARCH + """#arithmetic\">architecture
page</a><sup class="meth-cite"><a href="#meth-ref-3">[3]</a><a
href="#meth-ref-4">[4]</a></sup>.</p>

<p>The split is enforced structurally, not by convention: the held-out set is
unreachable from the search module's import graph, which a canary test checks, so the
optimizer's fitness can only ever read the three search
problems<sup class="meth-cite"><a href="#meth-ref-9">[9]</a><a
href="#meth-ref-17">[17]</a></sup>.</p>
"""

_S2 = """
<h2 id="meth-measurement">2. Measurement</h2>

<p>The error metric depends on the problem. The default is the Euclidean distance
between the final Q15 state (converted back to physical units) and the reference
solution at t_end, divided by the problem's recorded peak amplitude. Two held-out
problems use structural invariants instead: pendulum measures relative energy drift
|E - E0| / E0 with E = &omega;&sup2;/2 + (1 - cos &theta;), and quaternion measures
unit-norm drift, the absolute difference between the final quaternion norm and
1<sup class="meth-cite"><a href="#meth-ref-3">[3]</a></sup>.</p>

<p>The Q15 integrator takes n equal steps of size h = t_end / n; every coefficient
application is a range-checked Q15 primitive, and any overflow anywhere aborts the
run. The evaluator never raises: internal failures become inf entries, a zero
overflow margin, or a None measured order. It returns a ScoreVector per candidate:
measured order and its fit-point count, the error constant, stability extents,
per-model cycle counts, total CSD weight, maximum coefficient quantisation error,
search-set and held-out RMS errors, an overflow margin, and a per-problem error map.
The m0plus_fast results are primary; the m0plus_slow and avr_approx runs are stored
as prefixed archive columns for analysis only. Integrator internals (per-stage
storage, the overflow-margin formula, the float64 twin used only by the convergence
study) are on the <a href=\"""" + _ARCH + """#arithmetic\">architecture
page</a><sup class="meth-cite"><a href="#meth-ref-4">[4]</a><a
href="#meth-ref-5">[5]</a></sup>.</p>

<p>A nine-check verifier then gates every candidate, applying its checks in a fixed
order and returning the earliest failure as a coded rejection: NOT_EXPLICIT,
ROW_SUM_INCONSISTENT, DYADIC_IMPOSSIBLE, ORDER_NOT_MET, COEFF_UNREPRESENTABLE (the
five cheap symbolic checks, run before any simulation, with symbolic order checked
as exact rational residuals over rooted trees), then Q15_OVERFLOW, UNSTABLE,
NO_ASYMPTOTIC_WINDOW, and NAN_OR_INF. The verifier never raises, never calls a
language model, never opens a socket, and never writes; the annotated list is on the
<a href=\"""" + _ARCH + """\">architecture page</a><sup class="meth-cite"><a
href="#meth-ref-6">[6]</a><a href="#meth-ref-8">[8]</a></sup>.</p>

<h3>Measured order</h3>

<p>Symbolic order says what a tableau should do; measured order says what its
implementation actually does. The measurement runs the float64 integrator on
dahlquist with t_end = 10 and records the absolute final-state error against
exp(-10) at the twelve step counts n = 8 &middot; 2<sup>k</sup> for k = 0 to 11.
Between consecutive halvings it forms local slopes, keeping only points whose error
is finite and above the floor 1e-12, finds the longest consecutive run of slopes
whose spread stays within 0.08 (ties broken toward the earliest run), and fits a
least-squares line to log error against log h over that run. The slope of the fit is
the measured order. If no run of at least two slopes exists, the result is None and
the verifier rejects the tableau with NO_ASYMPTOTIC_WINDOW<sup class="meth-cite"><a
href="#meth-ref-5">[5]</a></sup>.</p>

<p>This rule locates the asymptotic window instead of assuming where it sits. The
specification records verified values for the classical baselines, reproduced by the
golden tests: euler measures 0.9849 over 7 points, heun2 2.0109 over 8, kutta3
3.0404 over 5, and rk4 4.0706 over 3; a broken implementation reads 2.0 or 3.0, not
4.07. The convergence study runs in float64 rather than Q15 because Q15 roundoff
would floor the observed order for every method; the study asks what the
coefficients converge like, and the Q15 error is measured separately by the problem
runs<sup class="meth-cite"><a href="#meth-ref-5">[5]</a><a
href="#meth-ref-17">[17]</a></sup>.</p>
"""

_S3 = """
<h2 id="meth-protocol">3. Statistical protocol</h2>

<p>Every verified candidate is archived with a mechanically assigned evidence
<a href="glossary.html#tiers">tier</a>, applied by code when the record is written:
<em>heldout_verified</em> when it improves on the incumbent elite in both search-set
and held-out error and improves at least one problem in each of two or more problem
families; <em>search_only</em> when it improves the search error but not the held-out
error (the signature of overfitting to the optimizer's own problems);
<em>unreplicated</em> otherwise, including when the cell had no incumbent. The tier
vocabulary is deliberately absent from every language-model prompt template, and a
planted tableau tuned to the search set must land in search_only; both properties are
canary tests. The archive structure behind the cells (MAP-Elites grids, cycle
buckets, replay) is on the <a href=\"""" + _ARCH + """#archive\">architecture
page</a><sup class="meth-cite"><a href="#meth-ref-11">[11]</a><a
href="#meth-ref-13">[13]</a></sup>.</p>

<p>Results from the two early phases carry a stronger label: those phases enumerate
their spaces completely, so the findings site marks their entries "exhaustive" with
the note "optimal within the enumerated space", against "search result" for the
CMA-ES phases. The enumeration lattices and their counts are on the
<a href=\"""" + _ARCH + """#candidates\">architecture page</a><sup class="meth-cite"><a
href="#meth-ref-10">[10]</a><a href="#meth-ref-20">[20]</a></sup>.</p>

<h3>Hypotheses and verdicts</h3>

<p>The language model may record falsifiable predictions, but only as
machine-checkable predicates in a closed grammar over archive statistics, and it
never writes the verdict. After each cycle the runner evaluates open predicates
against the archive under fixed rules: a predicate referencing any cell with no
records is <em>inconclusive</em>, never refuted, because an absent cell is absence of
evidence; no verdict is issued below the hypothesis's declared minimum sample count;
and for field-versus-field comparisons an effect size below Cohen's d = 0.2 forces
<em>inconclusive</em> regardless of the comparison's truth value, so the third bucket
stays reachable. Otherwise the verdict is <em>supported</em> or <em>refuted</em>,
appended to the ledger with the sample count, effect size, and resolving cycle, and
every model call receives the accumulated list of refuted hypotheses so refuted
ideas are not re-proposed in different words. The grammar and its parser are on the
<a href=\"""" + _ARCH + """#outer\">architecture page</a><sup class="meth-cite"><a
href="#meth-ref-12">[12]</a><a href="#meth-ref-21">[21]</a></sup>.</p>

<h3>Falsification protocol</h3>

<p>Before the long run was allowed to start, the project's premise was itself put
under test. The premise has two load-bearing parts: coefficient arithmetic must be a
material fraction of the per-step cycle cost, and there must be a step-size regime,
at practical step sizes, where Q15 roundoff rather than truncation dominates the
error. The experiment implements rk4 and heun2 in Q15 on damped_osc, computes the
coefficient-arithmetic fraction of one step's cycles under both primary cost models,
and sweeps the step count to find the largest h at which the Q15 error stops
improving while the float64 error is still falling<sup class="meth-cite"><a
href="#meth-ref-19">[19]</a></sup>.</p>

<p>The protocol committed to numeric decision criteria in advance. If the
coefficient fraction is below 15 percent for both methods and no crossover appears
in the practical range h between 1e-3 and 1, the verdict is "kill": stop the
project, keep the benchmark. If the fraction is at least 30 percent for both and a
crossover appears in that range, the verdict is "proceed". Anything between is
"mixed". The verdict is computed by code from the measured values and published on
this site either way; a clean negative is a real
finding<sup class="meth-cite"><a href="#meth-ref-19">[19]</a></sup>.</p>

<h3 id="meth-practical">Practical validation</h3>

<p>A practical-validation suite sits outside the search entirely: a set of equations
drawn from real applications, never seen by the optimizer, excluded from scoring,
tiers, and the archive statistics. Each is evaluated at the same 65,536-cycle budget
under the same arithmetic and primary cost model, comparing the archived champions
against the classical anchor methods under identical conditions. The suite asks
whether methods selected on the fixed problem set hold up on dynamics they were not
selected on; its results are published on their own page as they are
produced<sup class="meth-cite"><a href="#meth-ref-5">[5]</a><a
href="#meth-ref-21">[21]</a></sup>.</p>
"""

_S4 = """
<h2 id="meth-trust">4. Verification and trust</h2>

<p>A language model participates in the run, and the design assumes it will try
shortcuts, so every path from model output into the system passes a validator, and
the scoring path is out of its reach entirely. The runner is the only module
permitted to talk to the model; a canary greps the import graph of every other
module to keep it that way. The model steers the search by returning one JSON
directive per call, validated against a schema in which unknown keys are a
rejection, not ignored; a malformed directive is discarded and logged, and a
deterministic fallback runs instead. A directive can only narrow the search; it
cannot change the objective, the problems, the cost model, or the tier rules. The
schema's field-by-field limits are on the
<a href=\"""" + _ARCH + """#outer\">architecture page</a><sup class="meth-cite"><a
href="#meth-ref-15">[15]</a></sup>.</p>

<h3>The pinned hash and the container gate</h3>

<p>The container mounts the rk-harness repository read-only: if the agent could edit
the scorer, the shortest path to a high score would be editing the scorer.
Independently, a sha256 digest is computed over the concatenation, in fixed order,
of the ten files that determine every score: rk_harness/coeffrep.py,
orderconditions.py, verifier.py, costmodel.py, evaluator.py, problems.py, and
fixtures/classical.json, problems.json, q15.json, known_sequence.s. The digest must
equal the value pinned in the VERIFIER_HASH file or the container exits before doing
anything, and every archive record stores the hash under which it was scored, so any
historical change to the scoring code is detectable per record. At every container
start the entrypoint runs, in order: a writability probe on the harness mount, the
verifier hash comparison, and a pytest selection of the golden tests G1 through G20
plus canaries K1 and K2; any failure exits with status 1 and the runner never
starts<sup class="meth-cite"><a href="#meth-ref-14">[14]</a><a
href="#meth-ref-16">[16]</a></sup>.</p>

<h3>Credentials</h3>

<p>All pushes are performed from the host with the owner's own git credentials; the
container pushes nothing, and the GitHub credential never enters the container at
all. The env file the container receives is filtered on the host to drop the token,
the runner only commits into the mounted work and findings checkouts, and a
pre-flight check verifies the boundary directly: the filtered file carries no token,
and the environment inside a container started from it shows
none<sup class="meth-cite"><a href="#meth-ref-14">[14]</a><a
href="#meth-ref-21">[21]</a></sup>.</p>

<h3>Quarantine</h3>

<p>The one place model-written code executes is the problem quarantine. A proposed
derivative function is staged to the work repository, never into the harness, and
must pass six admission checks before it is loaded, including byte-identical output
across two runs, an import allowlist of exactly the math module enforced by walking
the parsed syntax tree, a time bound, a range check at twice nominal amplitude, a
reference solution, and a promotion gate requiring the classical methods to rank on
the new problem as they rank on the existing held-out set. Admitted problems join
the held-out set only, never the search set, and spend 10 cycles in shadow mode
before affecting tiers. If the model concludes the evaluator or cost model itself
needs changing, it can only write a proposal file and stop; that category of edit
sits behind a human gate. The full check list is on the
<a href=\"""" + _ARCH + """\">architecture page</a><sup class="meth-cite"><a
href="#meth-ref-13">[13]</a></sup>.</p>
"""

_S5 = """
<h2 id="meth-testing">5. Testing</h2>

<h3>Golden fixtures</h3>

<p>Sections 9 through 12 of the specification are ground truth computed and verified
before the implementation existed: the eight classical tableaus with their exact
coefficients, CSD totals, quantisation errors, and cycle counts under both primary
models; the nine order-5 residuals of rk4; the stability extents of the classical
methods; the rooted-tree counts against OEIS A000081; the twelve-number
rk4-versus-rk38 anchor; the Q15 multiplication vectors including the
floor-versus-truncate divergence rows; the seven problem definitions with measured
peaks; and the hand-counted ARM assembly sequence. These live verbatim in the four
files under fixtures/, are loaded by path, and are never regenerated by any test.
Spec-derived tests alone would merely ratify the spec; the golden values give the
tests external truth to check against<sup class="meth-cite"><a
href="#meth-ref-17">[17]</a><a href="#meth-ref-21">[21]</a></sup>.</p>

<h3>Suites and canaries</h3>

<p>The acceptance criteria are grouped by identifier: G1-G27 golden results, F1-F21
fixed-point behaviour, V1-V11 verifier verdicts, C1-C10 cost model properties,
K1-K16 anti-gaming canaries, R1-R5 crash recovery, and E1-E7 end-to-end properties
including byte-identical same-seed runs. K1 plants a tableau tuned to the search set
and bad on the held-out set and requires it to tier as search_only; it is the check
that catches the whole project silently overfitting while its dashboard looks
healthy<sup class="meth-cite"><a href="#meth-ref-17">[17]</a></sup>.</p>

<p>The pytest suite is organised in numbered tier files that mirror the dependency
stack, each written against the frozen interface specification before or
independently of the implementation: the numerical core at the bottom, then the
search and archive layers, the model integration, the operational layer, display
formatting, this methodology page, and the out-of-band practical-validation suite.
Many tests are parameterised over the classical methods or fixture rows, so
collecting the suite yields over a thousand test cases; expensive computations run
once per module through shared fixtures and are marked
slow<sup class="meth-cite"><a href="#meth-ref-17">[17]</a></sup>.</p>

<h3>Preflight</h3>

<p>A separate preflight script executes every machine-checkable item of the review
checklist (docs/REVIEW.md) end to end: it runs the suites, greps for forbidden
patterns, exercises the container checks when asked, prints a PASS, FAIL, MANUAL, or
SKIP status per item, and writes the signed report to docs/REVIEW-REPORT.md. Its
gating sections set a nonzero exit code on any
failure<sup class="meth-cite"><a href="#meth-ref-18">[18]</a></sup>.</p>
"""

_S6 = """
<h2 id="meth-reproducibility">6. Reproducibility</h2>

<p>The determinism guarantees are layered. The Q15 core is exact integer arithmetic
on Python integers, so it is platform-independent by construction. The evaluator
reads no wall clock, spawns no threads, and draws no random numbers; the same
tableau and budget give an identical score vector every time, and the convergence
study and stability bisection use fixed grids. The verifier is a pure function that
never raises. The search is seeded, with restart seeds derived arithmetically from
the island seed, and the enumerations iterate their lattices in sorted order, so
both layers replay exactly<sup class="meth-cite"><a href="#meth-ref-5">[5]</a><a
href="#meth-ref-9">[9]</a></sup>.</p>

<p>Persistence is replayable. The archive is append-only JSONL whose derived state
is a pure function of file contents; the end-to-end acceptance test requires two
runs with the same seed to produce byte-identical archives. Tableaus have a
canonical JSON serialisation whose sha256 digest is the content hash used for
deduplication and record integrity, and every record additionally stores the
verifier hash pinning the exact scoring code that produced it. Stored timestamps are
UTC throughout; conversion to US Central time happens only in human-facing display
code, as a pure function of the stored value. The findings site itself is
regenerated from the archive with no wall-clock reads and no randomness, so the same
archive always produces byte-identical pages<sup class="meth-cite"><a
href="#meth-ref-11">[11]</a><a href="#meth-ref-20">[20]</a></sup>.</p>
"""

_S7 = """
<h2 id="meth-limitations">7. Limitations</h2>

<p>The cost model is analytic and has never been closed against silicon inside the
loop. It is anchored by a hand-counted assembly fixture and a reference C emitter a
human can compare against compiler output, but no measurement on a physical
Cortex-M0+ feeds back into scores. Derivative evaluation is excluded from the cycle
count, which is harmless when comparing methods at the same stage count and a real
simplification when stage counts differ. The AVR model is approximate by its own
definition and is confined to an advisory column; no claim rests on
it<sup class="meth-cite"><a href="#meth-ref-7">[7]</a></sup>.</p>

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
    + _REFS
)


def render_page(page) -> str:
    """Render the methodology article through the injected page callable.

    `page` is sitegen._page (or any callable with the same signature):
    page(title, body, active="", subtitle="") -> str.
    """
    return page(TITLE, _BODY, "methodology.html", _SUBTITLE)
