# FINAL REVIEW — pre-flight checklist

Companion to FINAL_HANDOFF. Run after implementation, before the first unattended
run. Every item is pass/fail. Section references are to FINAL_HANDOFF.

**Gating sections: A, B, C, K.** Do not start the 80-day run with a failure in
any of those four. D through J can close during the first supervised week.

---

## 0. Regression checks against the six v2 defects

These exist because the v2 spec was internally inconsistent in ways its own tests
did not catch. Verify each fix landed before anything else.

- [ ] **0.1** G7 tolerance is `±0.10`, not `±0.05`. Run it and read the printed
      value: expect `4.0706` with 3 fit points. If your implementation prints
      `4.0942`, it is still using the v2 three-finest-ratios rule. (defect 1)
- [ ] **0.2** `Q15_INEXACT` does not appear in `REJECT_CODES`. Grep the whole
      repo — zero hits. (defect 2)
- [ ] **0.3** All eight classical tableaus pass `verify()`, including `rk4`
      (holds `1/6`) and `kutta3` (holds `2`). If any is rejected, the coefficient
      representation is wrong. (defect 2, tests V1 / V10c)
- [ ] **0.3b** `COEFF_UNREPRESENTABLE` fires on a coefficient of `40000` and
      **not** on `1/3`. It is a range check, not an exactness check. (V10b)
- [ ] **0.3c** `NO_ASYMPTOTIC_WINDOW` fires when `measured_order` returns `None`,
      and `ScoreVector.measured_order` is typed `float | None`. (V10d)
- [ ] **0.4** `grep -n is_dyadic rk_harness/costmodel.py` returns **nothing**.
      Dyadic is the impossibility predicate; CSD weight is the cost predicate.
      (defect 3, test C9)
- [ ] **0.5** `csd_weight` of `3` is `2`, not `1`. `3/8` is dyadic and costs two
      shifts plus an add. (defect 3, test G25)
- [ ] **0.6** `count_sequence` exists and is what C1 calls. `cycle_count` takes a
      `Tableau` and cannot score an assembly file. (defect 4)
- [ ] **0.7** The cost model is named `AVR_APPROX`, not `AVR`, and no findings
      page draws a conclusion from it. (defect 5)
- [ ] **0.8** `runner.py`, `ledger.py`, `sitegen.py`, `dashboard.py` all exist
      with the §4.12 signatures. (defect 6)

## A. Trust boundary

- [ ] **A1** `/harness` mounted `:ro`. Attempt a write from inside the running
      container; must fail EROFS. (K4)
- [ ] **A2** GitHub PAT returns **403** on a write to `rk-harness`. Actually run
      `gh api repos/jgoetzmann/rk-harness --method PATCH -f description=x`. Do
      not assume the scope is right. (K5)
- [ ] **A3** `entrypoint.sh` hashes the **ten** files in §4.11 in that order and
      compares to a pinned value. Alter one byte of `coeffrep.py`; the container
      must refuse to start. Test `coeffrep.py` specifically — it was added after
      v2 and is easy to omit from the hash list. (K3)
- [ ] **A4** `verify()` has no network, no LLM, no writes. Grep its import
      closure for `requests`, `openai`, `httpx`, `socket`, `open(`, `subprocess`.
- [ ] **A5** `assign_tier` lives in `archive.py` and is pure code. Grep every LLM
      prompt template for `heldout_verified`, `search_only`, `unreplicated` —
      zero hits. (K8)
- [ ] **A6** Only `runner.py` imports `openai`. Grep every other module's import
      graph — zero hits. (K13)
- [ ] **A7** `ledger.py` contains no `eval`, `exec`, or `compile`. (K15)
- [ ] **A8** `parse_predicate("__import__('os')")` raises `PredicateSyntaxError`.
      **Read the parser yourself.** It handles untrusted model output, and a
      generated parser that quietly falls back to `eval` is an arbitrary-code
      path wearing a grammar as a disguise. This is on the hand-write list for
      that reason. (K14)
- [ ] **A9** `.env` gitignored in all three repos. `git check-ignore .env` each.
- [ ] **A10** No credential in the image. `docker history --no-trunc` grepped for
      the token prefix returns nothing.
- [ ] **A11** Network allowlist active. From inside the container,
      `curl https://example.com` fails; `curl https://api.openai.com` resolves.
- [ ] **A12** Directive with an unknown key is **rejected**, not ignored. (K9)
- [ ] **A13** Quarantine problem importing `os` is rejected by the **AST**
      checker. Read the checker and confirm it walks the AST rather than
      pattern-matching source text. (K11)

## B. Ground truth

Print every measured value. Do not accept a green checkmark alone.

- [ ] **B1** G1–G27 all pass, values printed.
- [ ] **B2** Convergence table matches §4.7 exactly:
      euler **0.9849** (7 pts), heun2 **2.0109** (8), kutta3 **3.0404** (5),
      rk4 **4.0706** (3). Point counts matter as much as the orders — a
      different count means a different window was selected.
- [ ] **B3** RK4 real extent **−2.785** to **−2.786**; imag **2.8284** ± 0.001.
- [ ] **B4** `kutta3` imag reads **1.7321** ± 0.001 (= √3). This catches a
      stability routine that only works for degree-4 polynomials.
- [ ] **B5** `euler` and `heun2` imag extents both `< 0.01`. A substantial
      nonzero value means the imaginary bisection is broken.
- [ ] **B6** `residuals(rk4, 4)` returns exactly **8** values, all zero.
- [ ] **B7** The 9 order-5 residuals compared to §9.2 term by term. If all zero,
      the tree generator is broken and will accept anything.
- [ ] **B8** Tree counts for orders 1–6: **1, 1, 2, 4, 9, 20**. (G17)
- [ ] **B9** `to_rep` reproduces every row of §4.2b including the `exact` flags.
      Check `3/4` and `3/8` specifically: exact, power-of-two denominator, CSD
      weight **2**. Those two rows are defect #3 in miniature. (G24)
- [ ] **B10** F1–F21 pass, including **F17** (floor vs truncate-toward-zero).
      Getting truncation direction backwards biases every result in the project
      by a consistent amount and no other test catches it.
- [ ] **B11** `q15_mul(-32768,-32768)` raises. (F18)
- [ ] **B12** C1 reproduces **13** and **75**. Hand-check one of the two against
      the assembly in §12 yourself.
- [ ] **B13** §9.1 cycle columns reproduced for all eight, fast and slow. (G22)

## C. The anchor result and the enumeration

Both produce real output before the search exists. Both are strong evidence the
cost model measures something.

- [ ] **C1** All **twelve** numbers in §9.5 reproduced: `rk4` and `rk38` at
      `n_states` ∈ {1,2,4} under fast and slow. (G21)
- [ ] **C2** The ordering **reverses**: `rk4` cheaper on fast (33 < 36), `rk38`
      cheaper on slow (64 < 85). If it does not reverse, the CSD path is not
      being taken and the cost model has collapsed to a flat multiply count.
- [ ] **C3** `cycle_count(t, m, n) == n * cycle_count(t, m, 1)` for all eight
      classical methods. (C10)
- [ ] **C4** Phase 0 enumeration finds exactly **16** valid points out of 256
      candidate `a21` values with `s ≤ 6`. (G26)
- [ ] **C5** The cheapest is `midpoint` at **11** slow cycles, with `heun2` at
      13. Read the full 16-row output; it is the first real result the project
      produces.
- [ ] **C6** Phase 0 uses **enumeration, not CMA-ES**. Read `encourager.py` and
      confirm the phase-to-method mapping in §8. Running a stochastic optimizer
      over sixteen points would report a discovery where a proof exists.
- [ ] **C7** The site labels Phase 0/1 results "exhaustive — optimal within the
      enumerated space" and Phase 2/3 results "search result". That distinction
      is the difference between a proof and a best-so-far.

## D. Anti-overfitting

- [ ] **D1** K1 passes: planted overfitted tableau classified `search_only`. Run
      it manually and read the output. **Single most important check here.**
- [ ] **D2** K2 passes: single-family winner classified `unreplicated`.
- [ ] **D3** K12 passes: no import path from `search.py` reaches `HELDOUT_SET`.
      Verify by reading the import graph, not just running the test.
- [ ] **D4** Read the CMA-ES objective line by line. Confirm it touches only
      `SEARCH_SET`.
- [ ] **D5** Feed the optimizer the held-out set deliberately; confirm the
      held-out gap metric collapses to zero, proving it measures what it claims.
      Revert and confirm the gap returns.
- [ ] **D6** `assign_tier` requires improvement on **≥ 2 problem families** for
      `heldout_verified`, not just aggregate error.
- [ ] **D7** Candidates are snapped to representable `(m, s)` form **before**
      verification, not after. (§4.9)

## E. Determinism and recovery

- [ ] **E1** E2: two runs, same seed, byte-identical archive. `diff` the files.
- [ ] **E2** R1: `docker kill -9` mid-cycle, three times, ≤ 1 cycle lost each.
- [ ] **E3** R2: truncated JSONL last line handled cleanly.
- [ ] **E4** R3: pause/unpause produces identical results.
- [ ] **E5** R4, R5: missing and corrupt `RUNSTATE.json` both recover by replay.
- [ ] **E6** **Pull the power on the laptop mid-run once.** Actually do it.
      Restart and confirm recovery. This is the failure mode you will hit.
- [ ] **E7** `save_state` writes temp-then-`os.replace`, never in place.
- [ ] **E8** `sitegen.build` is deterministic: same archive, byte-identical HTML.

## F. Resource behavior

- [ ] **F1** `.wslconfig` matches §13. Restart WSL2, confirm with `free -h`.
- [ ] **F2** Start the container, then load Windows. Pause watchdog fires within
      ~40 seconds.
- [ ] **F3** Unpause fires when foreground load drops.
- [ ] **F4** Watch WSL2 memory in Task Manager for one hour. It should plateau,
      not climb. Climbing means `autoMemoryReclaim` is not working and you will
      OOM in week two.
- [ ] **F5** `--pids-limit` set. Fork-bomb inside the container; host survives.
- [ ] **F6** Archive and vhdx on **D:**, not C:.
- [ ] **F7** NitroSense battery charge limit set to **80%**. The only
      irreversible item on this list.
- [ ] **F8** Laptop elevated for airflow, power profile Balanced, fans auto.

## G. Budget guards

- [ ] **G1** OpenAI dashboard monthly cap set. Screenshot it.
- [ ] **G2** Local spend counter hard-stops. Test with
      `OPENAI_MONTHLY_CAP_USD=0.01`.
- [ ] **G3** Killfile stop works: create `D:\rk\work\STOP`, confirm graceful
      shutdown at the next cycle boundary.
- [ ] **G4** Heartbeat timeout works: `SIGSTOP` the inner process; watchdog kills
      the container after 120s.
- [ ] **G5** Disk threshold works: fill D: under 5 GB free, confirm hard stop.
- [ ] **G6** Codex `auth.json` mounted read-only and the container authenticates.
      Confirm before the first unattended night, not during it.

## H. Publishing

- [ ] **H1** Site builds from a fixture archive; every entry shows a tier. (E3)
- [ ] **H2** `sitegen.BANNED_WORDS` enforced — `build()` raises on a hit. Test it
      by planting one. (E4)
- [ ] **H3** "Not reviewed by a human" banner on every page.
- [ ] **H4** Any `AVR_APPROX` figure carries the approximate-model note.
- [ ] **H5** Every entry links `tableau_hash` and `verifier_hash`.
- [ ] **H6** Pages deploys and is reachable.
- [ ] **H7** Archive commits contain only completed daily files. Confirm no file
      is rewritten across two consecutive commits.

## I. Hypothesis machinery

- [ ] **I1** Predicate parser accepts the §6 grammar and rejects everything else.
      Test at least five malformed predicates.
- [ ] **I2** `pXsY` resolves to the **minimum `heldout_error` across all cost
      buckets** at that stage count. Verify with a constructed archive holding
      two buckets at the same stage count.
- [ ] **I3** A predicate naming an empty cell returns `inconclusive`, never
      `refuted`. Otherwise the model can "refute" hypotheses by never searching.
      (K16)
- [ ] **I4** `inconclusive` is reachable from insufficient samples.
- [ ] **I5** Verdict computed by code. Grep the LLM prompt for `supported`,
      `refuted`, `inconclusive` — zero hits.
- [ ] **I6** Every LLM call includes the refuted list. Read one real assembled
      prompt and confirm. Without it the model re-proposes the same idea every
      eight cycles for three months.
- [ ] **I7** Cohen's d threshold (0.2) applied, not skipped at large n.
- [ ] **I8** `n_samples` for a multi-cell predicate is the **smallest** count
      among referenced cells.

## J. Encourager and calendar

- [ ] **J1** E5: `next_action` never returns `PACKAGE` or `FREEZE` before
      2026-11-20. Property-test with 1000 random states.
- [ ] **J2** E6: clock at 2026-11-21 returns `PACKAGE`.
- [ ] **J3** E7: clock at 2026-12-06 returns `FREEZE`.
- [ ] **J4** Set the system clock forward and confirm all three transitions fire.
      Do not trust date logic you have not executed.
- [ ] **J5** Phase table (§8) implemented with the stated `s` bounds **and the
      stated method per phase**.
- [ ] **J6** Phase 1 enumeration cap (`1e8`) enforced, with CMA-ES fallback, and
      which one ran is recorded.
- [ ] **J7** Escalation ladder fires in order. Force a stall and watch it climb.

## K. The falsification result

- [ ] **K1** §15 has actually been run.
- [ ] **K2** Coefficient-arithmetic fraction of cycles: ________ %
- [ ] **K3** Roundoff/truncation crossover step size: ________
- [ ] **K4** Decision recorded in `rk-findings` with both numbers, either way.

**If K2 < 15% and K3 shows no crossover in a practical range, stop here.** You
have a reusable benchmark, an anchor result, and a proof of optimality at order 2
— all real contributions — and you have saved three months searching for
something that is not there.

Note that §9.5 gives partial evidence in advance: `rk4` and `rk38` differ by 25%
under the slow multiplier purely through coefficient structure. That is a strong
prior for K2 landing well above 15%, but it is not a substitute for measuring it.

---

## Sign-off

```
Date started:         ____________
Section 0 green:      ____________
A green:              ____________
B green:              ____________
C green:              ____________
D green:              ____________
K decision:           kill / proceed
First unattended run: ____________
```

---

## Known gaps this checklist does not cover

Stated plainly so they do not surprise you at week six.

- **The cost model has never touched silicon.** It is internally consistent and
  reproduces a hand-counted assembly sequence, which is as far as you can go
  without hardware. Every published result must say so.
- **`emit_c` has been cross-checked against `arm-none-eabi-gcc` zero times**
  unless you do it manually. Doing it once, by hand, for `rk4` costs an hour and
  materially raises the credibility of everything downstream. Strongly
  recommended during review week.
- **`AVR_APPROX` is admittedly wrong in structure**, not just in constants.
  `CostModel` cannot express width-dependent cost. Treat every AVR number as
  advisory.
- **The 2× overflow margin is a judgment call.** If too many candidates reject on
  `Q15_OVERFLOW`, that is the knob — but changing it invalidates prior records,
  so bump the schema version.
- **Problem peaks in §11 were measured with float64 RK4 at 40,000 steps.** They
  are accurate well past Q15 resolution but are not analytic bounds.
- **`UNSTABLE`'s −0.5 threshold is arbitrary.** Same schema-version rule applies.
- **E1's candidate-rate threshold is uncalibrated by design.** Measure it once,
  then set it. An asserted number here blocks the run for no reason.
