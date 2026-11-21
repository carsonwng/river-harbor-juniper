# Open questions (ranked)

| # | Question | Why it matters | How to answer | Status |
| --- | --- | --- | --- | --- |
| Q1 | Does practice submission 1 (868.3 s) contain a miss, and from which rule? | A systematic miss costs 300 s on every such commit in the final | Ablation probes in `submissions.md` | open |
| Q2 | When can `test_max_nodes` (#1490, 28.8 s) be proven unaffected? | It sets a 28.8 s floor on every commit that selects it | Read the test; trace what it depends on; heavy-test gate | open |
| Q3 | Which changes really reach `test_execute_tpcds` (#1106, 21.5 s)? | It fails on some sample optimizer and executor edits, so it can't be dropped blindly | Coverage plus executor-path analysis | open |
| Q4 | Per-key attribution for dispatch tables (`FUNCTIONS`, `TRANSFORMS`, …) | Import-time code is impact's main source of over-selection | Record dict lookups per test at build time | open |
| Q5 | How different are maps built on Windows from those built in the Linux image? | Maps are built in the image; local development is on Windows | Build both and diff | open |
| Q6 | Is the mutant set representative of the hidden practice and final sets? | Validation is only as good as its distribution | Compare the kind mix and the failing-set sizes with the sample | open |

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
