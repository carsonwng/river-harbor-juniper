# Open questions (ranked)

| # | Question | Why it matters | How to answer | Status |
| --- | --- | --- | --- | --- |
| Q1 | Does practice submission 1 (868.3 s) contain a miss, and from which rule? | A systematic miss costs 300 s on every such commit in the final | Ablation probes in `submissions.md` | open |
| Q2 | When can `test_max_nodes` (#1490, 28.8 s) be proven unaffected? | It sets a 28.8 s floor on every commit that selects it | Read the test; trace what it depends on; heavy-test gate | mostly closed: with coverage it is never selected on the sample; on m1 it comes in only with every-test selections |
| Q3 | Which changes really reach `test_execute_tpcds` (#1106, 21.5 s)? | It fails on some sample optimizer and executor edits, so it can't be dropped blindly | Coverage plus executor-path analysis | partly closed: relprof (E7) removes it on `2haz` and `66ks`; no finer slicing tried |
| Q4 | Per-key attribution for dispatch tables (`FUNCTIONS`, `TRANSFORMS`, …) | Import-time code is impact's main source of over-selection | Record dict lookups per test at build time | closed by E8/E9 (per-test attribute and key read profile) |
| Q5 | How different are maps built on Windows from those built in the Linux image? | Maps are built in the image; local development is on Windows | Build both and diff | open |
| Q6 | Is the mutant set representative of the hidden practice and final sets? | Validation is only as good as its distribution | Compare the kind mix and the failing-set sizes with the sample | open |
| Q7 | **Is relprof within the "no table from a kind of edit" rule?** It stores, per comparison site of the pinned commit, which relations the operands had during the suite, and derives at run time whether the edited operator gives the same truth values. It stores nothing about failing tests | Worth 53 s on the sample and 48 s on m1; a wrong call disqualifies | Human decision, ask the organisers if unsure. `BR_IMPACT_RELPROF=0` removes it | **open, blocks the choice of submission** |
| Q8 | Does the combo build fit the 10 min image build on the scorer's 4 cores? | Impact step is 282 s locally at 4 jobs; triage and pip come on top | `br.py eval --docker` on `strat/impact-combo-sound` | open |
| Q9 | Does the run-time import-check subprocess work under the scorer (read-only disk, `nobody`, 2 cores)? | It closes both held-out misses; on failure the selector falls back to a selection that misses import failures | Docker eval; change the fallback to every test | open |
| Q10 | Isolate's registry rule depends on the xdist worker layout of the build (19 or 53 tests for `ktz3` with 1-job or 4-job maps) | Precision only, about 1.3 s on the sample | Make the rule layout-independent | open, low priority |
| Q11 | Fixture commits are now the largest remaining cost on m1 (135 s of 570 s, 18 commits) | Impact work cannot reduce it | Triage layer: finer fixture-line to test-case mapping | open |

## Hypotheses from the 2026-11-21 cloud run (ranked; see `runs/20261121-impact-cluster.md`)

Starting point: `strat/impact-base` (= `cloud-base-1` + `origin/strat/impact`) scores sample/local
**177.8 s, 0 misses** on Linux (oracle 94.4 s). Of the 83 s gap, 73 s is code mutants that break
nothing: `2haz` 25.5 s and `66ks` 24.8 s (`is` -> `==`, both pull in #1106 through statement
coverage), `535z` 11.2 s and `4d63` 9.1 s (class-level constants, selected through class users),
and five small ones (2 to 4 s each). #1490 is never selected on the sample any more.

| # | Hypothesis | Expected gain (sample) | Miss risk | How to test | Branch |
| --- | --- | --- | --- | --- | --- |
| H1 | Coverage recorded in one long-lived process misses tests that only *consume* state another test computed (lru_cache, lazily built tables, class-level caches). Forked or fresh-state per-test coverage closes that miss class | 0 s or slightly worse; removes a 300 s miss class | lowers risk | Diff isolated coverage against in-process coverage; count (line, test) pairs gained; held-out misses | `strat/impact-isolate` |
| H2 | A comparison whose operator changed (`is`/`==`, `<`/`<=`, ...) cannot change any test if, for every evaluation seen at build time, the old and new operator give the same truth value. A per-site, per-test relation profile (identical / equal / less / greater) proves that | up to 52 s (`2haz`, `66ks`, `2qn5`) | medium: needs determinism and the profile to see every evaluation; rule check needed (value profile of the pinned commit, not a table of failing tests) | AST-instrumented second suite pass at build; held-out misses on operator mutants | `strat/impact-relprof` |
| H3 | Class-level constants and tables are read through `obj.NAME`; recording, per test, which (owner class, attribute) pairs were read (and for dict tables which keys) replaces "name loaders ∩ class users" | up to 20 s (`535z`, `4d63`, `ktz3`, `gbav`) | medium: reads at import time (metaclasses) and reads through `getattr`/`vars` must fall back | AST-instrumented attribute reads at build; held-out misses on constant and dict-entry mutants | `strat/impact-attrs` |
| H4 | Running the candidate tests in the container to prune them | none: CPU is the *sum* of test time, the scorer charges the *max* worker. For a set of total S s: select = S/k + 2k, run = S + about 0.5 s start-up, plus the failing tests again on a failure. Break-even needs S under about 1 s and a pass rate above about 60% | flaky-run risk | closed by the cost model; not run | none |
| H5 | Sub-test slicing of #1106 (run only the TPC-DS queries that reach the edit) | at most the 2 s worker cost per pass, since the two sample commits where #1106 is a false positive touch code every query runs | medium | not run; H2 covers the same commits | none |

Outcome (2026-11-21): H1 confirmed as a real miss class but not sufficient alone (E6). H2 and
H3 confirmed (E7, E8). The combination E9 is the candidate: sample 103.6 s, m1 570.3 s, 0 misses.
A miss class none of H1 to H5 predicted, edits that make a module fail to import, was found by
the attrs agent's probes and then by m1.
