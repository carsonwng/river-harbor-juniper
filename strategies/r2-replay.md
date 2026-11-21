# strat/r2-replay: a record-replay gate for test_execute_tpcds

Branch `strat/r2-replay`, cut from `origin/strat/runcpu2` (`ab9e4d5`, code identical to `f89594d`).
Impact layer only: `blast/impact.py` (`_replay_refine`), plus the new `blast/replaylib.py`,
`blast/replay_child.py`, `build/replay.py` and `build/impact_lib/br_replay_rec.py`.


## Claim

`test_execute_tpcds` (#1106, 21.454 s) is selected by optimizer and executor function-body
coverage. It really does run the edited statement, so no structural cut can remove it. The
test passes at the pin. Let the patched run of the test be compared with the pinned run,
stage by stage:

- A **stage** is one call of one optimizer rule (`optimize` calls 14 rules for each of the
  57 executed TPC-DS queries, so 798 rule calls). The other stages are the rest of the test:
  setUpClass, parse, pickle, find_tables/execute glue, plan, exec and the duckdb transpile.
- If every edit is inside function bodies, the two runs are identical until the first call
  of an edited function. (The module and class namespaces are the same, and only code
  objects differ.)
- If every edited function ran only inside rule stages in the pinned run, that first call
  happens inside a rule stage. That stage's input is the pinned input, which was recorded.
- Replaying every recorded call of those rules with the patched code is enough. If each call
  gives the same return value, the same arguments afterwards (the tree, including types,
  meta, comments, parent pointers, aliasing and cached-hash staleness, and the schema with
  its caches) and the same sqlglot global state, then the next stage gets the pinned input
  again. By induction the whole patched run is the pinned run, so the test passes.

## Mechanism

- **Build** (`build/replay.py`, about 50 s of CPU, sequential, not deadline-skippable):
  - `br_replay_rec.py` does the test's work in one process with `PYTHONHASHSEED=0`.
    `sys.monitoring` LINE events (DISABLE, restarted at each stage switch) record which
    sqlglot lines run in which stage. PY_START/PY_RETURN on the rule code objects, called
    from `optimize`, record each call's arguments with a slot-preserving pickle (which
    bypasses `Expression.__reduce__`, since that serialises only a subtree) and a canonical
    digest of (return value, arguments after the call). 5.4 MB of zlib-compressed inputs.
  - **Self-check.** `replay_child.py` replays every recorded call with the pinned code in a
    fresh process. A stage counts as replayable only if all its calls reproduce: 14/14 do.
    The only global state the pinned replay changes is `generator._DISPATCH_CACHE`, and its
    pinned contents are stored by qualified name.
- **Run** (`impact._replay_refine`, only when #1106 is in impact's bits). Every changed file
  must be a sqlglot `.py` whose AST differs only inside function bodies. For each touched
  line, take the innermost def whose body holds it, then over its whole body:
  - no line runs at any import (impact's import probe);
  - every line the coverage map gives to #1106 is also in the recording;
  - the stages that ran it are all replayable rule stages, and there is at least one.

  If all of that holds, a child (`PYTHONHASHSEED=0`) loads the patched modules through a
  meta-path finder, preloads the modules the recording had loaded, and replays every
  recorded call of those stages. It stops at the first difference. It then compares the
  global-state fingerprint (identity-based, every sqlglot module and class namespace).
  #1106 is dropped only when the child answers `same`.

### Extensions (third commit)

- **Lambda bodies at class or module level** (typing annotator tables and the like). The
  edit must lie inside lambda bodies, with everything else in the module equal. The lambda
  must not start during any import (gap-mix's `lambdas.json`). Its stages come from the
  lambda code objects' own line events, which the recorder keeps under `rel@lambda`,
  apart from the import-time statement on the same line.
- Every eligible edit also needs the code-readers map, and the test must not be among the
  code readers (a body edit changes `__code__`).

## Feasibility numbers (syn640, base = the runcpu2 image's selection)

| | commits | upper-bound saving if #1106 were dropped |
| --- | --- | --- |
| select #1106 | 123 | 1572 s |
| body-only edit, function runs only in rule stages (eligible) | 39 | 660 s |
| ... and the replay is identical (dropped) | 15 | 248 s |
| function runs in exec/plan/child/transpile/parse stages | 34 | 423 s |
| not body-only (class/module level) | 37 | 269 s |
| not a .py edit (fixtures) | 11 | 187 s |

- tpcds CPU breakdown, in-process: executor 13.9 s, all rules 2.3 s, plan 0.5 s, parse
  0.26 s. Replaying the executor would cost as much as the test, which is why only rule
  stages replay.
- Replay CPU: about 0.3 s for a negative answer (start-up and preload, then the first
  difference) and 1.3-5.6 s for a positive one (all 57 calls of each stage).
- Pinned outputs do not depend on the hash seed: seeds 0 and 1 give identical digests on
  all 798 calls. The inputs carry seed-0 cached `Expression._hash` values, so the replay
  must run with seed 0. The child refuses any other seed.

## Dead ends

- **Pipeline continuation (built, measured, removed).** When a query's rule output
  differed, keep running the patched rules k+1..14, threading the patched expression and
  schema, and compare only the final optimized expression (what `Plan` and the executor
  see). Its pinned self-check reproduced all 57 final digests. It found **0 extra drops** on
  syn640 and the 240 optimizer mutants: all 22 differing syn640 replays still differ at
  the end, because the differences propagate. It also raised the CPU of a negative answer
  from about 0.3 s to about 0.55 s, so it was taken out.

- **Function-level replay** (the brief's first idea). Optimizer helpers take nodes that sit
  inside trees and walk `.parent`, scopes and the schema, so a faithful per-call snapshot
  means the whole tree for every call, and the hot helpers run 10^5+ times.
- **Pickle-bytes digest.** Not reproducible: string identity (interning) differs after
  unpickling. The digest is canonical instead: atoms by value, everything else by
  structure, with aliasing kept.
- `python -I` ignores `PYTHONHASHSEED`, which cost one round of 0/14 self-checks. The child
  and the recorder use `-s -P` instead.

## Results (8feeeeb)

`python harness/br.py verify strat/r2-replay`: **VERIFY PASS, 0 misses on every set.**
Docker, cgroup CPU, compared with the base runcpu2 f89594d:

| set | runcpu2 total | r2-replay total | change |
| --- | --- | --- | --- |
| sample (30) | 125.407 | 125.328 | -0.08 (no drop; CPU 2.32 s) |
| m1 (64) | 425.693 | 408.001 | **-17.7** |
| m1-extra (22) | 205.320 | 205.029 | -0.3 |
| local-20261120 (32) | 327.910 | 280.407 | **-47.5** |
| gap-tests-tpc (20) | 361.065 | 360.990 | -0.1 |
| syn-import16 (16) | 592.072 | 591.923 | -0.1 |

Selection cost, local docker selector on the runcpu2 image's maps plus this branch's replay
map (unlabelled sets):

| set | base simulated | r2-replay | #1106 dropped | replays run |
| --- | --- | --- | --- | --- |
| syn640 (640, generator mix) | 5578.3 | 5330.0 (-248.3, -4.5%) | 15 | 42 |
| optimizer-only mutants (240, `generate.py --only-area optimizer --kinds code=1 --seed 9177`) | 4021.0 | 3263.2 (-757.7, -18.8%) | 45 | 106 |

**Run-verification.** For each of the 60 commits where #1106 was dropped (15 on syn640 and
45 on the optimizer set), I applied the patch to a copy of pinned sqlglot in the base image
and ran `tests/test_executor.py::TestExecutor::test_execute_tpcds` with
`SKIP_INTEGRATION=0`. **60/60 pass with 57/57 subtests.** The labelled sets (m1, local,
m1-extra) add their own drops with 0 misses. The gate is conservative: 3 edits it kept
(0ay8, 9u8z, 6nn5) also pass tpcds.

**CPU.** A replay runs only when #1106 is selected and the edit is eligible: about 6.6% of
syn640 commits. A positive answer costs about 2.1 s (one stage, 57 calls: 1-3 s; five
stages: 4.5 s). A negative costs about 0.3-0.5 s. On syn640 that is about 46 s of CPU
against 248 s simulated saved. On local-20261120 the CPU rose by 3.4 s for 51 s saved.

**Build** (cold, `--scorer-limits`, 4 CPU / 8 GB, contended host): **410.9 s total**. The
replay step took 35.0 s (recording 26.1 s, self-check 8.8 s). It runs after impact, and
impact's relprof pass (193 s) is what pushes the build past 400 s. The step is
single-threaded and has its own 300 s timeouts. Any failure leaves no `meta.json`, which
turns the gate off. The map is 5.6 MB in the image, nothing in the repo.

## For other strategies

- The gate lives in the impact layer, and runs after every other rule, on the final bits.
  It only ever removes #1106. It merges cleanly onto anything that keeps `impact.select`'s
  shape: one hook line plus new files.
- `blast/replaylib.py` (slot-preserving pickle, canonical identity-aware digest, global
  state fingerprint) is reusable for other heavy tests whose work splits into
  deterministic stages, such as `test_optimized_tpch` and `test_tpcds`, if they ever
  dominate.
- Where #1106's remaining cost on syn640 comes from: functions also run in exec, plan,
  child or transpile (34 commits), class or module level edits (37), and fixture edits
  (11). Replaying exec would cost as much as running the test. The plan stage needs a
  set-cycle-aware digest (Step dependency sets), for 3 planner edits.
