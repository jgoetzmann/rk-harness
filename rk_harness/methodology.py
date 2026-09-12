"""Methodology page for the findings site.

A long-form article on the method: the experimental setup, what is measured and how,
the statistical protocol, the trust boundaries, the testing and the limits. System
internals (Q15 arithmetic, cost model counting rules, search machinery, the archive)
are summarised only where the method depends on them; the overview architecture page
covers them in depth and this page links there.

The page callable (sitegen._page) is injected by the caller, and so are the closing
sections that need run data or the pinned cost model (the cost model tables, the
measurement ledger, the glossary). This module never imports sitegen, so there is no
circular import. The article itself is a fixed string: no wall clock, no randomness,
byte-identical output for the same sections.

Every number and filename in the article was checked against the repository at the
time of writing: rk_harness/fixedpoint.py, coeffrep.py, orderconditions.py,
problems.py, simulate.py, evaluator.py, verifier.py, costmodel.py, search.py,
enumeration.py, archive.py, ledger.py, quarantine.py, verifier_hash.py, directive.py,
falsification.py, entrypoint.sh, tests/, and docs/HANDOFF.md.
"""
from __future__ import annotations

import html

TITLE = "Methodology"

_SUBTITLE = "How the run measures, checks and reproduces its numbers."

_ARCH = "https://jgoetzmann.github.io/rk-overview/architecture.html"


def _cite(*refs: int) -> str:
    return ('<sup class="meth-cite">'
            + "".join(f'<a href="#meth-ref-{n}">[{n}]</a>' for n in refs) + "</sup>")


_LEAD = f"""
<p>rk-harness searches for explicit Runge-Kutta tableaus that give the lowest integration
error in Q15 fixed-point arithmetic at a fixed cycle budget, priced by an ARM Cortex-M0+
cost model. Classical tableaus were derived for exact real arithmetic. On a
microcontroller with no floating-point unit, roundoff competes with truncation at
practical step sizes, and each coefficient costs cycles that depend on its bit
pattern{_cite(21)}.</p>

<p>Scoring is pure code: a language model may steer the search and propose
hypotheses, but it never scores, tiers or verifies anything. Results are on the other
tabs and the machinery on the <a href="{_ARCH}">overview architecture page</a>. Terms are
in the <a href="methodology.html#glossary">glossary</a> below, and a bracketed number such
as {_cite(21)} cites a file in the rk-harness repository, a section of the frozen
specification, docs/HANDOFF.md, or a published paper.</p>
"""

_INFOBOX = """
<table class="infobox meth-infobox">
<caption>rk-harness</caption>
<tr><th>Object of study</th><td>Explicit Runge-Kutta tableaus, orders 1 to 4, stages 2 to 6</td></tr>
<tr><th>State arithmetic</th><td>Q15: int16 in [-32768, 32767], scale 2<sup>-15</sup></td></tr>
<tr><th>Target</th><td>ARM Cortex-M0+, analytic cycle model, two multiplier variants; AVR model advisory only</td></tr>
<tr><th>Evaluation budget</th><td>65,536 cycles per problem run</td></tr>
<tr><th>Test problems</th><td>7 fixed (3 search, 4 held out)</td></tr>
<tr><th>Verification gate</th><td>Nine ordered checks, pure code, never raises</td></tr>
<tr><th>Integrity pin</th><td>sha256 over ten files, checked at container start</td></tr>
</table>
"""

_TOC_HEAD = (
    ("meth-setup", "Experimental setup"),
    ("meth-measurement", "Measurement"),
    ("meth-protocol", "Statistical protocol"),
    ("meth-trust", "Verification and trust"),
    ("meth-testing", "Testing"),
    ("meth-reproducibility", "Reproducibility"),
    ("meth-limitations", "Limitations"),
)

_S1 = f"""
<h2 id="meth-setup">1. Experimental setup</h2>

<p>The object of study is the explicit tableau with symbolic order 1 to 4 and 2 to 6
stages. A entries sit on dyadic lattices no finer than 1/32768, and each coefficient is
stored as m / 2<sup>s</sup> with |m| &le; 32767 and 0 &le; s &le; 20. Where no exact
pair exists (1/3 becomes 21845 / 2<sup>16</sup>) the closest one is used and the gap is
recorded as <code>coeff_quant_error</code>, a measured property rather than a reason to
reject{_cite(2)}.</p>

<p>State arithmetic is Q15: 16-bit signed integers at scale 2<sup>-15</sup>, multiplied
as <code>(a * b) &gt;&gt; 15</code> with an arithmetic shift that floors toward negative
infinity, as the ARM <code>ASRS</code> instruction does, and an out-of-range value becomes
a verifier rejection. Because the floor is one-sided, each
product loses about half a least-significant bit on average, and at small step sizes
that bias dominates the Q15 error; the harness copies the hardware on purpose, since
code shipped to this target would floor too. The semantics are on the
<a href="{_ARCH}#arithmetic">architecture page</a> and under
<a href="methodology.html#floor-rounding">floor rounding</a>{_cite(1, 21)}.</p>

<h3>Budget and cost accounting</h3>

<p>Methods are always compared at an equal cycle budget, never at equal step size. A
tableau costing k cycles per step takes n = B // k steps with B = 65,536 cycles, so a
two-stage method takes about twice the steps of a four-stage one. Comparing at equal h
would hand the expensive methods free work{_cite(5)}.</p>

<p>Cycle counts are analytic, a pure function of the tableau with no compiler in the
loop. m0plus_fast (single-cycle multiplier) and m0plus_slow (32-cycle multiplier) are
primary; avr_approx is advisory. Each coefficient application is charged the cheaper of
a shift-add chain of its <a href="methodology.html#csd-weight">CSD weight</a> and a
hardware multiply, which is why bit structure matters under a slow multiplier. The tables
are in the <a href="methodology.html#costmodel">cost model</a> section below and the
counting rules on the <a href="{_ARCH}#costmodel">architecture page</a>{_cite(7, 2)}.</p>

<h3>Test problems and the held-out split</h3>

<p>Seven fixed initial-value problems drive every error measurement: three form the
search set the optimizer sees, and four are held out to catch overfitting. Peaks and
scales come from <code>fixtures/problems.json</code>{_cite(3)}.</p>

<div class="scroll">
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

<p>rc_thermal is the stiff member, with a stiffness ratio of about 70. Its derivative
would exceed Q15 range at the state scale, so it carries an extra factor
(DERIV_SCALE = 1/8) that the step size undoes exactly. The held-out set cannot be reached
from the search module's import graph, and a canary test checks that{_cite(3, 4, 9, 17)}.</p>
"""

_S2 = f"""
<h2 id="meth-measurement">2. Measurement</h2>

<p>Error is the Euclidean distance between the final state, converted back to physical
units, and the reference at t_end, divided by the problem's peak amplitude. pendulum uses
relative energy drift |E - E0| / E0 instead, and quaternion the drift of its norm from
1{_cite(3)}.</p>

<p>The Q15 integrator takes n equal steps of h = t_end / n, and an overflow anywhere
aborts the run. The evaluator never raises: a failure becomes an inf entry, a zero
overflow margin or a None measured order in the ScoreVector it returns, and its
m0plus_fast results are primary{_cite(4, 5)}.</p>

<p>A verifier then applies nine checks in a fixed order and returns the earliest failure
as a coded rejection. Five symbolic checks run before any simulation: NOT_EXPLICIT,
ROW_SUM_INCONSISTENT, DYADIC_IMPOSSIBLE, ORDER_NOT_MET (exact residuals over rooted
trees) and COEFF_UNREPRESENTABLE. Then come Q15_OVERFLOW, UNSTABLE, NO_ASYMPTOTIC_WINDOW
and NAN_OR_INF. The verifier never raises, never calls a language model, never opens a
socket and never writes{_cite(6, 8)}.</p>

<h3>Measured order</h3>

<p>Symbolic order says what a tableau should do; measured order says what the
implementation does. The float64 integrator runs dahlquist to t_end = 10 at
n = 8 &middot; 2<sup>k</sup> steps for k = 0 to 11. Slopes between consecutive halvings
are kept where the error is finite and above 1e-12, the longest run of slopes whose
spread stays within 0.08 is fitted by least squares, and the fitted slope is the measured
order. With no run of at least two slopes the verifier rejects the tableau with
NO_ASYMPTOTIC_WINDOW{_cite(5)}.</p>

<p>The golden tests reproduce the specification's values, rk4 at 4.0706 over 3 points
among them. The study runs in float64 because Q15 roundoff would flatten every method's
slope; the problem runs measure the Q15 error separately{_cite(5, 17)}.</p>
"""

_S3 = f"""
<h2 id="meth-protocol">3. Statistical protocol</h2>

<p>Code assigns each verified candidate an evidence <a href="methodology.html#tiers">tier</a>
when it is archived. <em>heldout_verified</em>: it improved on the incumbent elite in both
search-set and held-out error, across at least two problem families.
<em>search_only</em>: the search error improved and the held-out error did not, which is
the signature of overfitting. <em>no_incumbent</em>: the cell was empty.
<em>no_improvement</em>: there was an incumbent and neither rule above applied, which
includes improving held-out error but not search error. Those last two were one word,
<em>unreplicated</em>, before the split, and every record written earlier still carries it. The tier
words appear in no
prompt template, and a planted tableau tuned to the search set must land in search_only;
canaries check both. The archive
behind the cells is on the <a href="{_ARCH}#archive">architecture page</a>{_cite(11, 13)}.</p>

<p>The two early phases enumerate their spaces completely, so their entries are marked
"exhaustive" with the note "optimal within the enumerated space", CMA-ES entries
are marked "search result", and tableaus seeded at startup "seeded classical
baseline". The lattices are on the
<a href="{_ARCH}#candidates">architecture page</a>{_cite(10, 20)}.</p>

<h3>Hypotheses and verdicts</h3>

<p>The model may record predictions only as predicates in a closed grammar over archive
statistics, and code writes every verdict. A predicate that names an empty cell is
<em>inconclusive</em>, because absence of evidence is not refutation. No verdict is issued
below the declared minimum sample count, and an effect size below Cohen's d = 0.2 forces
<em>inconclusive</em> whatever the comparison says; otherwise the verdict is
<em>supported</em> or <em>refuted</em>. Every model call receives the refuted list. The
grammar is on the
<a href="{_ARCH}#outer">architecture page</a>{_cite(12, 21)}.</p>

<h3>Falsification protocol</h3>

<p>Before the long run started, its premise was tested on rk4 and heun2 in Q15 on
damped_osc, against criteria fixed in advance: coefficient arithmetic must be a material
share of per-step cost, and Q15 roundoff must dominate the error somewhere in the
practical step range. Code computes the verdict (kill, mixed or proceed), and the
validation tab publishes it with its thresholds, whichever way it falls{_cite(19)}.</p>

<h3 id="meth-practical">Practical validation</h3>

<p>A practical validation suite sits outside the search: equations from real
applications that the optimizer never sees and that no score, tier or archive statistic
includes. The archived champions and the classical anchors run each one at the same
65,536-cycle budget, to see whether methods selected on the fixed problems hold up on
dynamics they were not selected on{_cite(5, 21)}.</p>

<p>Two numeric filters run before any win or loss from that suite is published. A
problem is flagged degenerate when its finishers' Q15 errors span less than 5 percent
from best to worst, because a field that tight is reporting the problem rather than the
method; a flagged problem is left out of every tally with its reason printed. The second
criterion, an error within 5 percent of the reference solution's norm, catches a state
that decayed to nothing. validation/results.json stores that norm for each problem, on
the same scale as the errors, so the pages read it from the document rather than
assuming it. Any error ratio within 2 percent of 1.0 is counted as a tie. A third
criterion, an identical peak magnitude equal to the initial condition, was dropped because
it flagged a healthy problem{_cite(5, 20)}.</p>
"""

_S4 = f"""
<h2 id="meth-trust">4. Verification and trust</h2>

<p>The design assumes the language model will try shortcuts. Only the runner talks to it,
every path from its output into the system passes a validator, and a directive can narrow
the search but never change the objective, the problems, the cost model or the tier
rules{_cite(15)}.</p>

<p>The container mounts rk-harness read-only and exits at start-up unless a sha256 over
the ten scoring files matches VERIFIER_HASH and the golden tests pass, and every record
stores the hash it was scored under. Model-written code runs only inside the problem
quarantine, and the container never receives the GitHub token. The
<a href="{_ARCH}#verify">architecture page</a> goes through the checks in
order{_cite(13, 14, 16)}.</p>
"""

_S5 = f"""
<h2 id="meth-testing">5. Testing</h2>

<p>The golden fixtures are ground truth computed before the implementation existed (the
classical tableaus, rooted-tree counts, the rk4-against-rk38 anchor, the Q15
multiplication vectors and a hand-counted ARM sequence), kept verbatim under fixtures/ so
the tests check against an outside truth instead of ratifying the specification. A
preflight script runs every machine-checkable item of the review checklist, and the
<a href="{_ARCH}#tests">architecture page</a> describes the suite{_cite(17, 18)}.</p>
"""

_S6 = f"""
<h2 id="meth-reproducibility">6. Reproducibility</h2>

<p>The Q15 core is exact integer arithmetic, the evaluator reads no clock, starts no
threads and draws no random numbers, and the search is seeded, so two runs from one seed
write byte-identical archives. This site is rebuilt with no clock reads, so the same
archive always gives the same pages; the <a href="{_ARCH}#repro">architecture page</a>
has the detail{_cite(5, 9, 11, 20)}.</p>
"""

_S7 = f"""
<h2 id="meth-limitations">7. Limitations</h2>

<p>The cost model is analytic and has never been checked against silicon inside the
loop: an assembly fixture and a reference C emitter anchor it, but no hardware
measurement feeds a score. Derivative evaluation is left out of the cycle count, which is
harmless between methods with the same stage count and a real simplification
otherwise{_cite(7)}.</p>

<p>Measured order comes from one scalar problem in float64, so it certifies the
coefficients, not fixed-point behavior across the suite. Stability extents are sampled
along the real and imaginary axes only. Error is measured at the final time, so a method
could trade path accuracy for endpoint accuracy without penalty. Floor rounding is one
hardware convention; a target that rounds or saturates would shift every Q15
result{_cite(5, 1)}.</p>

<p>The search space is narrow by design, and optimality from the enumeration phases holds
only inside the enumerated lattices. Four held-out problems make held-out verification a
strong filter, not a statistical guarantee, and the two-family rule and the d = 0.2
threshold are conventions chosen in advance, frozen by the specification so results
stay comparable across the run{_cite(21)}.</p>
"""

RELATED_WORK = f"""
<p>Rounding error in time integration is an active line of work, and this project did
not discover it. Croci and Giles analyse Runge-Kutta discretizations of the heat
equation in low-precision floating point and show that under round-to-nearest the
computed solution stagnates once the timestep is small enough, with global rounding
error growing like unit roundoff divided by the timestep until it does{_cite(22)}.
Hopkins and colleagues measure stochastic rounding and reduced-precision fixed point in
ODE solvers built for neuromorphic hardware, the same arithmetic regime as this
work on different hardware{_cite(23)}. Croci and Rosilho de Souza build mixed-precision
explicit stabilized Runge-Kutta methods and show where the low-precision part can sit
without costing accuracy{_cite(24)}.</p>

<p>The crossover this run measures, where Q15 error turns back up as the step size falls,
is the fixed-point counterpart of that stagnation result in a different arithmetic.
Finding the same shape under a different rounding rule is a check on this harness rather
than a new phenomenon, and it is consistent with what that literature shows.</p>

<p>Two things here are different from that line of work, and both are narrow. The
arithmetic is fixed point with directed rounding: every Q15 multiply ends in an
arithmetic right shift that floors, so the error is biased one way, where round-to-nearest
is symmetric and stochastic rounding is unbiased by construction. And the object of study
is the tableau rather than the arithmetic: this run searches coefficient space against
that bias and prices every coefficient on a cost model, where the work above analyses
fixed methods under a rounding mode. Neither difference is a claim of priority over that
work.</p>
"""


_REFS = """
<h2 id="meth-references">References</h2>

<ol class="meth-refs">
<li id="meth-ref-1">rk_harness/fixedpoint.py; HANDOFF 4.2 (Q15 semantics) and 10 (divergence vectors).</li>
<li id="meth-ref-2">rk_harness/coeffrep.py; HANDOFF 4.2b (coefficient representation, CSD weight).</li>
<li id="meth-ref-3">rk_harness/problems.py and fixtures/problems.json; HANDOFF 11 (problem fixture).</li>
<li id="meth-ref-4">rk_harness/simulate.py (Q15 and float64 integrators, derivative scaling, budget-to-steps).</li>
<li id="meth-ref-5">rk_harness/evaluator.py; HANDOFF 4.7 (equal-budget rule, measured order, stability extents).</li>
<li id="meth-ref-6">rk_harness/verifier.py; HANDOFF 4.4 (nine ordered checks).</li>
<li id="meth-ref-7">rk_harness/costmodel.py, fixtures/known_sequence.s; HANDOFF 4.5 (counting rules), 9.5 (anchor comparison) and 12 (assembly fixture).</li>
<li id="meth-ref-8">rk_harness/orderconditions.py; HANDOFF 4.3 (rooted trees, dyadic impossibility).</li>
<li id="meth-ref-9">rk_harness/search.py; HANDOFF 4.9 (CMA-ES, projection, islands).</li>
<li id="meth-ref-10">rk_harness/enumeration.py; HANDOFF 8 (phase table) and 9.6 (phase 0 count).</li>
<li id="meth-ref-11">rk_harness/archive.py; HANDOFF 4.8 (grids, buckets, tiers, replay).</li>
<li id="meth-ref-12">rk_harness/ledger.py; HANDOFF 6 (predicate grammar, verdicts, effect size).</li>
<li id="meth-ref-13">rk_harness/quarantine.py; HANDOFF 7 (admission checks, shadow mode, proposal gate).</li>
<li id="meth-ref-14">rk_harness/verifier_hash.py, VERIFIER_HASH, rk_harness/credentials.py; HANDOFF 4.11 and 2.2.</li>
<li id="meth-ref-15">rk_harness/directive.py, rk_harness/runner.py; HANDOFF 5 (directive schema and validation).</li>
<li id="meth-ref-16">entrypoint.sh; HANDOFF 13.1 (start-up order: probe, hash, golden gate).</li>
<li id="meth-ref-17">tests/test_t1_fixedpoint_coeff_cost.py through tests/test_t7_methodology.py; HANDOFF 14 (acceptance criteria G, F, V, C, K, R, E).</li>
<li id="meth-ref-18">scripts/preflight.py and docs/REVIEW-REPORT.md (executed review checklist).</li>
<li id="meth-ref-19">rk_harness/falsification.py; HANDOFF 15 (criteria and sweep).</li>
<li id="meth-ref-20">rk_harness/sitegen.py; HANDOFF 17 (auto-publish rules, determinism, labels).</li>
<li id="meth-ref-21">docs/HANDOFF.md (frozen specification; section numbers as cited above).</li>
<li id="meth-ref-22">M. Croci and M. B. Giles, "Effects of round-to-nearest and stochastic rounding in the numerical solution of the heat equation in low precision", IMA Journal of Numerical Analysis 43(3), 2023, pages 1358-1390.</li>
<li id="meth-ref-23">M. Hopkins, M. Mikaitis, D. R. Lester and S. Furber, "Stochastic rounding and reduced-precision fixed-point arithmetic for solving neural ordinary differential equations", Philosophical Transactions of the Royal Society A 378:20190052, 2020.</li>
<li id="meth-ref-24">M. Croci and G. Rosilho de Souza, "Mixed-precision explicit stabilized Runge-Kutta methods for single- and multi-scale differential equations", Journal of Computational Physics 464, 2022, 111349.</li>
</ol>
"""


def _toc(sections) -> str:
    items = [f'<li><a href="#{sid}">{title}</a></li>' for sid, title in _TOC_HEAD]
    items += [f'<li><a href="#{html.escape(str(sid), quote=True)}">'
              f"{html.escape(str(title))}</a></li>" for sid, title, _body in sections]
    items.append('<li><a href="#meth-references">References</a></li>')
    return '<ol class="meth-toc">\n' + "\n".join(items) + "\n</ol>\n"


def _body(sections=()) -> str:
    """The article, with any injected sections after section 7 and before References.

    Each injected section is (anchor, heading, trusted HTML body). The caller builds the
    body, so this module stays free of run data and of any import of sitegen.
    """
    extra = "".join(
        f'\n<h2 id="{html.escape(str(sid), quote=True)}">{html.escape(str(title))}</h2>\n'
        f"{body}\n" for sid, title, body in sections)
    return (_INFOBOX + _LEAD + _toc(sections)
            + _S1 + _S2 + _S3 + _S4 + _S5 + _S6 + _S7 + extra + _REFS)


def render_page(page, sections=()) -> str:
    """Render the methodology article through the injected page callable.

    `page` is sitegen._page (or any callable with the same signature):
    page(title, body, active="", subtitle="") -> str. `sections` is an optional
    sequence of (anchor, heading, html) tuples appended after section 7.
    """
    return page(TITLE, _body(tuple(sections)), "methodology.html", _SUBTITLE)
