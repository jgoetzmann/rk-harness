# REVIEW REPORT — pre-flight checklist (docs/REVIEW.md)

Generated 2026-08-29 by `scripts/preflight.py` (suite run: True; docker: True).
Statuses: PASS / FAIL / MANUAL (needs the host, hardware or a human) / SKIP (prerequisite absent).

| Section | Status |
| --- | --- |
| 0 | green |
| A | green (pending MANUAL: 2) |
| B | green |
| C | green |
| D | green |
| E | green (pending MANUAL: 2) |
| F | green (pending MANUAL: 6) |
| G | green (pending MANUAL: 2) |
| H | green |
| I | green |
| J | green |
| K | green |

| Item | Status | Measured / evidence |
| --- | --- | --- |
| 0.1 | PASS | measured_order(rk4) = 4.0706 with 3 fit points (expect 4.0706 / 3; 4.0942 = v2 rule) |
| 0.2 | PASS | Q15_INEXACT hits in code/tests/scripts/fixtures: none (docs/HANDOFF.md mentions it historically) |
| 0.3 | PASS | verify(): euler=pass, midpoint=pass, heun2=pass, ralston2=pass, heun3=pass, kutta3=pass, rk4=pass, rk38=pass |
| 0.3b | PASS | 40000 -> COEFF_UNREPRESENTABLE; heun3 (1/3) cheap checks -> None; to_rep(1/3) = CoeffRep(m=21845, s=16, exact=False, csd_weight=8) |
| 0.3c | PASS | ScoreVector.measured_order annotated `float \| None`; V10d passed |
| 0.4 | PASS | grep is_dyadic costmodel.py -> nothing |
| 0.5 | PASS | csd_weight(3) = 2 |
| 0.6 | PASS | count_sequence exists; cycle_count(t: 'Tableau', model: 'CostModel', n_states: 'int') -> 'int' |
| 0.7 | PASS | cost models: ['m0plus_fast', 'm0plus_slow', 'avr_approx']; no bare AVR |
| 0.8 | PASS | §4.12 names present; missing: none |
| A2 | MANUAL | needs a fine-grained PAT in .env; then run scripts/check_pat.ps1 (expects HTTP 403) |
| A4 | PASS | verify() closure = ['coeffrep', 'costmodel', 'evaluator', 'fixedpoint', 'orderconditions', 'paths', 'problems', 'simulate', 'tableau', 'types', 'verifier']; network/subprocess hits: none; file writes: none; read-only open(): ['problems.py:30', 'tableau.py:159'] |
| A5 | PASS | assign_tier in archive.py (11 lines, pure); prompt template tier-string hits: none |
| A6 | PASS | openai in any import graph except runner.py (credentials.py names OPENAI_API_KEY per §2.2, excluded): none |
| A7 | PASS | ledger.py eval/exec/compile hits: none |
| A8 | PASS | PredicateSyntaxError(expected field, got '__import__'); parser is a hand-rolled tokenizer + recursive descent (323 lines), no dynamic execution |
| A9 | PASS | rk-harness=ignored, rk-work=ignored, rk-findings=ignored |
| A10 | PASS | docker history grepped for token prefixes: nothing |
| A11 | MANUAL | apply scripts/network.sh inside WSL as root, then from the container: curl https://example.com must fail, api.openai.com must resolve |
| A12 | PASS | K9 passed (unknown directive key rejected, not ignored) |
| A13 | PASS | check_source walks the AST (ast.parse/ast.walk present, no regex on source); 'import os' -> ["import of 'os' not allowed"] |
| A1 | PASS | write to /harness inside the container: touch: cannot touch '/harness/.probe': Read-only file system rc=1 |
| A3 | PASS | coeffrep.py altered by one line: container exit 1; stderr: VERIFIER HASH MISMATCH â€” refusing to run |
| A3+ | PASS | untampered harness: exit 0; hash ok=True; golden/canary gate: ['55 passed, 888 deselected in 4.46s'] |
| B1 | PASS | G1, G2, G3, G4, G5, G6, G7, G8, G9, G10, G11, G12, G13, G14, G15, G16, G17, G18, G19, G20, G21, G22, G23, G24, G25, G26, G27 passed |
| B2 | PASS | convergence: euler 0.9849 (7 pts), heun2 2.0109 (8 pts), kutta3 3.0404 (5 pts), rk4 4.0706 (3 pts) |
| B3 | PASS | rk4 real -2.785294, imag 2.828427 |
| B4 | PASS | kutta3 imag 1.732051 (sqrt3 = 1.732051), real -2.512745 |
| B5 | PASS | euler imag 1.41e-06, heun2 imag 1.68e-03 |
| B6 | PASS | residuals(rk4,4): 8 values, all zero = True |
| B7 | PASS | order-5 residuals: 1/120, 1/240, -1/240, 1/120, 1/80, -1/120, -1/240, 1/240, -1/120 \| match §9.2 as multiset = True |
| B8 | PASS | tree counts 1..6 = [1, 1, 2, 4, 9, 20] |
| B9 | PASS | to_rep: 1->1/2^0 exact=True w=1; 2->2/2^0 exact=True w=1; -1->-1/2^0 exact=True w=1; 1/2->1/2^1 exact=True w=1; 1/4->1/2^2 exact=True w=1; 3/4->3/2^2 exact=True w=2; 3/8->3/2^3 exact=True w=2; 1/3->21845/2^16 exact=False w=8; 1/6->21845/2^17 exact=False w=8; 2/3->21845/2^15 exact=False w=8; -1/3->-21845/2^16 exact=False w=8 |
| B10 | PASS | F1, F2, F3, F4, F5, F6, F7, F8, F9, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21 passed |
| B11 | PASS | q15_mul(-32768,-32768) raises Q15OverflowError; floor check: q15_mul(-1,1) = -1, q15_mul(-3,5) = -1 |
| B12 | PASS | count_sequence fast=13 slow=75; hand check: LDR2+LDR2+MULS1+ASRS1+ADDS1+LSLS1+SUBS1+MULS1+ASRS1+STR2 = 13; slow: two MULS at 32 -> 13-2+64 = 75 |
| B13 | PASS | fast/slow at n=1: euler 5/5, midpoint 11/11, heun2 13/13, ralston2 16/30, heun3 23/50, kutta3 26/65, rk4 33/85, rk38 36/64 |
| C1 | PASS | rk4/n1/fast=33; rk4/n1/slow=85; rk4/n2/fast=66; rk4/n2/slow=170; rk4/n4/fast=132; rk4/n4/slow=340; rk38/n1/fast=36; rk38/n1/slow=64; rk38/n2/fast=72; rk38/n2/slow=128; rk38/n4/fast=144; rk38/n4/slow=256 |
| C2 | PASS | fast: rk4 33 < rk38 36; slow: rk38 64 < rk4 85 -> ordering reverses |
| C3 | PASS | cycle_count(t, m, n) == n * cycle_count(t, m, 1) for all eight, n in 2..4 |
| C4 | PASS | Phase 0: 16 valid points of 256 candidate a21 values |
| C5 | PASS | slow cycles (all 16): 11:1/2->b=(0,1) [midpoint] \| 13:-1/2->b=(2,-1) \| 13:1->b=(1/2,1/2) [heun2] \| 13:1/4->b=(-1,2) \| 15:-1->b=(3/2,-1/2) \| 16:-1/16->b=(9,-8) \| 16:-1/32->b=(17,-16) \| 16:-1/4->b=(3,-2) \| 16:-1/64->b=(33,-32) \| 16:-1/8->b=(5,-4) \| 16:-2->b=(5/4,-1/4) \| 16:1/16->b=(-7,8) \| 16:1/32->b=(-15,16) \| 16:1/64->b=(-31,32) \| 16:1/8->b=(-3,4) \| 16:2->b=(3/4,1/4) |
| C6 | PASS | runner: phase 0 -> enumeration.enumerate_phase0 (exhaustive), phase 1 -> enumerate_phase1 with cap fallback, phases 2/3 -> search.cmaes_island; encourager only routes (SEARCH_CELL/WIDEN/...), never selects the optimizer |
| C7 | PASS | site labels present: exhaustive=True, 'search result'=True (9 pages from 22 records) |
| D1 | PASS | planted search-tuned tableau (search 0.005 < 0.02, heldout 0.20 > 0.05, 3 search families improved) -> search_only |
| D2 | PASS | single-family winner (dahlquist only, worse aggregates) -> unreplicated |
| D3 | PASS | search.py import graph = ['coeffrep', 'costmodel', 'fixedpoint', 'orderconditions', 'paths', 'problems', 'search', 'simulate', 'tableau', 'types']; HELDOUT_SET loads: none |
| D4 | PASS | search.objective + helpers ['_search_rms', '_residual_penalty'] (24 lines) reference SEARCH_SET only; HELDOUT absent |
| D5 | PASS | objective(heun2) with SEARCH_SET:=HELDOUT_SET = 0.096102 vs evaluate().heldout_error 0.096102 (gap 0.0e+00); reverted objective = 0.017405 (gap 0.0787) |
| D6 | PASS | better on both aggregates but only 1 family -> unreplicated (heldout_verified needs >= 2 families) |
| D7 | PASS | 6 yielded tableaus; every A entry is k/2^s with s<=15 before the runner verifies (snap happens inside search.project) |
| E1 | PASS | E2 passed (two runs, same seed -> byte-identical archive) |
| E2 | PASS | local kill -9 x3 then restart: kill#1: replay ok, 2 records; kill#2: replay ok, 10 records; kill#3: replay ok, 22 records; final run exit 0: 22 -> 22 records (22 = 8 baselines + 14 Phase 0 points), duplicates=0, cycle_done logged=True; last events: ['{"ts": "2026-09-21T10:00:00Z", "kind": "cycle_done", "cycle_id": 2, "phase": 0, "improved": true, "stall_counter": 0, "accepted": 12, "rejected": 0, "spend_usd": 0.0, "cap_usd": 50.0}', '{"ts": "2026-09-21T10:00:00Z", "kind": "candidates_processed", "accepted": 0, "rejected": 0, "skipped": 0, "total": 0}', '{"ts": "2026-09-21T10:00:00Z", "kind": "cycle_done", "cycle_id": 3, "phase": 1, "improved": false, "stall_counter": 1, "accepted": 0, "rejected": 0, "spend_usd": 0.0, "cap_usd": 50.0}']; stderr: ss\.venv\Lib\site-packages\cma\s.py:15: UserWarning: Could not import matplotlib.pyplot, therefore ``cma.plot()`` etc. is not available _warnings.warn('Could not import matplotlib.pyplot, therefore' |
| E3 | PASS | R2, B34 passed (truncated last JSONL line discarded) |
| E4 | MANUAL | docker pause rk for 60 s mid-evaluation, then compare the cycle's records against an unpaused run (R3); cost model is analytic so host load cannot change scores |
| E5 | PASS | R4, R5 passed (missing / corrupt RUNSTATE.json rebuild from replay) |
| E6 | MANUAL | pull the laptop power mid-run once; restart; confirm the runner replays and loses <= 1 cycle |
| E7 | PASS | save_state -> _atomic_write_text: writes <name>.<pid>.tmp, fsync, then os.replace (never in place): True |
| E8 | PASS | B62, E3 passed (sitegen deterministic; byte-identical rebuild) |
| F1 | MANUAL | C:\Users\jacob\.wslconfig differs from §13 (have->want): {'processors': ('4', '8'), 'swap': ('0', '4GB'), 'autoMemoryReclaim': (None, 'gradual'), 'sparseVhd': (None, 'true')}. It carries another project's settings, so it was not overwritten; the §13 file is at scripts/wslconfig.rk — copy it, then `wsl --shutdown` and confirm with `free -h` |
| F2 | MANUAL | start the container, load Windows: scripts/watchdog.ps1 must print 'docker pause' within ~40 s |
| F3 | MANUAL | drop the foreground load: watchdog must print 'docker unpause' within ~40 s |
| F4 | MANUAL | watch Vmmem in Task Manager for one hour: plateau, not climb (autoMemoryReclaim) |
| F5 | PASS | run.ps1 sets --pids-limit=512; bounded fork test under --pids-limit=64: 62/300 forks succeeded before 'can't fork' (host unaffected) |
| F6 | PASS | harness at D:\Programming-Projects\Integration-Harness\rk-harness, work at D:\Programming-Projects\Integration-Harness\rk-work (D:); vhdx location is a Docker Desktop setting -> check Settings > Resources > Disk image location is on D: |
| F7 | MANUAL | NitroSense: battery charge limit 80% (irreversible cell wear otherwise) |
| F8 | MANUAL | elevate for airflow, Windows power profile Balanced, NitroSense fans auto |
| G1 | MANUAL | set the monthly cap in the OpenAI dashboard and screenshot it |
| G2 | PASS | spend 0.02 > cap 0.01: runner exit 3, event spend_cap_exceeded=True, no cycle ran=True |
| G3 | PASS | STOP present: runner exited 0 in 1.0s at the cycle boundary, event stopped_by_killfile=True |
| G4 | PASS | stale HEARTBEAT (300 s): watchdog printed kill=True; container state now 'exited' |
| G5 | PASS | disk threshold forced (MinFreeGB=999999): watchdog printed stop=True; container state 'exited' |
| G6 | MANUAL | C:\Users\jacob\.codex\auth.json missing: authenticate Codex on the host before the first unattended night; run.ps1 mounts it :ro |
| H1 | PASS | site built from the live archive (22 records, 5 elites, 9 pages); every elite shows tier + hash on index |
| H2 | PASS | planted 'novel'/'beats' -> BannedWordError(banned word 'novel' at offset 5); build() raises before writing (E4 passed) |
| H3 | PASS | banner on 9/9 pages |
| H4 | PASS | costmodel.html carries AVR_APPROX figures with the note: True |
| H5 | PASS | 5 cell pages each show tableau_hash and verifier_hash |
| H6 | PASS | GET https://jgoetzmann.github.io/rk-findings/ -> 200 (Pages may take up to 10 minutes after the first push) |
| H7 | PASS | archive files in rk-work history: none yet; runner commits only files other than today's (source checked) |
| I1 | PASS | accepted the §6 example (2 terms); rejected 9/9 malformed: ['fast.p2s2.cycles = 16', 'fast.p2s2.foo < 1', 'medium.p2s2.heldout < 1', 'fast.p2s2.heldout < 1 XOR fast.p2s2.heldout < 2', '', 'fast.p2s2.heldout < (1)', "__import__('os')", 'import os', 'fast.p2s2.heldout < 1; print(1)'] |
| I2 | PASS | two buckets at (p2,s2) with heldout 0.30 / 0.10 -> p2s2.heldout resolves to min 0.1 (n=2); 'fast.p2s2.heldout < 0.2' -> supported |
| I3 | PASS | empty cell p4s6 -> (inconclusive, 0, 0.0) |
| I4 | PASS | min_samples 200 with n=2 -> inconclusive |
| I5 | PASS | prompt template never asks the model for a verdict and the directive schema has no verdict field; the words appear only as data labels in the refuted list (required by I6): no instruction |
| I6 | PASS | assembled prompt (5679 chars) lists refuted H-047 with its verdict |
| I7 | PASS | n=1e6 per cell, means 0.1000 vs 0.1001, sd 0.1 -> Cohen's d 0.0010 -> inconclusive (threshold applied at large n) |
| I8 | PASS | cells with n=50 and n=900 -> n_samples = 50 (smallest) |
| J1 | PASS | E5 passed (1000 random states before 2026-11-20: never PACKAGE/FREEZE) |
| J2 | PASS | E6 passed |
| J3 | PASS | E7 passed |
| J4 | PASS | runner.now() driven by RK_CLOCK through the real code path: {'2026-11-19T23:59:00Z': 'SEARCH_CELL', '2026-11-21T00:00:00Z': 'PACKAGE', '2026-12-06T00:00:00Z': 'FREEZE'} (system clock left untouched) |
| J5 | PASS | phase table in runner: {0: ('order 2', 'stages 2', 'enumerate_phase0'), 1: ('order 3', 'stages 3(-4)', 'enumerate_phase1 / CMA-ES fallback'), 2: ('order 4', 'stages 4-5', 'cmaes_island'), 3: ('order 4', 'stages 4-6', 'cmaes_island')}; lattice bounds {'PHASE0_S_MAX': 6, 'PHASE1_S_MAX': 8}; s<=12 / s<=20 for phases 2/3 are the to_rep s_max (20) with dyadic_denominator_max in the directive |
| J6 | PASS | PHASE1_CAP = 100,000,000; runner logs 'phase1_cap_exceeded' and falls back to CMA-ES; enumerate_phase1() returns (tableaus, cap_exceeded) = (5094, False) |
| J7 | PASS | stall 0/5/10/20/30 -> ['SEARCH_CELL', 'SEARCH_CELL', 'WIDEN', 'HYPOTHESIZE', 'ADVANCE_PHASE'] |
| K1 | PASS | §15 run recorded at D:\Programming-Projects\Integration-Harness\rk-work\falsification.json |
| K2 | PASS | rk4 coefficient-arithmetic fraction: m0plus_fast 0.559322033898305, m0plus_slow 0.3617021276595745; heun2: {'m0plus_fast': 0.5, 'm0plus_slow': 0.14772727272727273} |
| K3 | PASS | rk4 crossover h = 0.15625; heun2 crossover h = 0.0390625; verdict = mixed |
| K4 | PASS | decision recorded in rk-findings/docs/falsification.html (verdict mixed): True |

## Sign-off

```
Date started:         2026-08-29
Section 0 green:      2026-08-29
A green:              2026-08-29  (green (pending MANUAL: 2))
B green:              2026-08-29
C green:              2026-08-29
D green:              2026-08-29  (green)
K decision:           proceed
First unattended run: gating sections green; MANUAL items remain
```

