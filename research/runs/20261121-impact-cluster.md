# Run report: impact cluster, 2026-11-21 (cloud session, in progress)

Status: **draft, updated as results arrive.** The combine experiment (E9) is still running.

## Starting point and environment

- Tag `cloud-base-1` (`544b5ba`), notes branch `cloud/20261121-impact-cluster`.
- Impact baseline: `strat/impact-base` (`6a6d766`) = `cloud-base-1` merged with `origin/strat/impact`
  (`4ca11e3`, v1b). `strat/impact` was cut before pack and triage were merged, so it had to be
  merged with `main` to be scored.
- Machine: 4 cores, 15 GB, Python 3.13.18, **no docker**. Every number in this report is
  `local` mode (host Python, wall time as the CPU stand-in). Nothing here has run under the
  scorer's container limits.
- Pushes were refused (HTTP 403) for the first part of the run and worked after the repository
  was added to the session.

## Reproduced baselines (sample, local)

| What | Simulated | Misses | Recorded before |
| --- | --- | --- | --- |
| `cloud-base-1`, no maps | 1104.510 s | 0 | 1104.5 s |
| `cloud-base-1`, triage maps | 717.423 s | 0 | 717.4 s |
| `strat/impact-base` (main + impact v1b) | **177.811 s** | 0 | 635.4 s for v1b with triage at its default |
| oracle | 94.351 s | – | 94.4 s |

The 635.4 s on record predates `4ca11e3` and had triage off; with triage and the last impact
commit, v1b is at 177.8 s on Linux. Impact build here: 71 s wall at 4 jobs, maps 6.7 MB.

## Discovery

- Of the 83 s between the baseline and the oracle, 73 s is source mutants that break nothing:
  `2haz` 25.5 s and `66ks` 24.8 s (`is` -> `==`; statement coverage pulls in #1106, 21.5 s),
  `535z` 11.2 s (1 failing test, 171 selected, incl. #1224) and `4d63` 9.1 s (class-level constants
  through class users), `ktz3`, `gbav`, `2qn5`, `5y72`, `fryt` (2 to 4 s each).
- #1490 (`test_max_nodes`, 28.8 s) is never selected on the sample by the baseline. #1106 truly
  fails on `2jrq` and `3utz`; those two are at their floor.
- Sample source commits are single-line mutants. 9 of 16 break nothing.
- The baseline had never been checked beyond the sample, and one miss (300 s) outweighs the
  whole remaining gap. So one of the three first-round branches was given to soundness.
- Hypotheses H1 to H5 are in `research/open-questions.md`. H4 (run tests in the container to
  prune) was closed by the cost model without an experiment: CPU is the sum of test time while
  the scorer charges the busiest worker, so it only breaks even for sets under about 1 s.

## Experiments

All cut from `strat/impact-base`. Sample/local; held-out not available (see below).

| ID | Branch (head) | PR | Sample simulated | Misses | Run CPU (wall) | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| E5 | `strat/impact-base` (`6a6d766`) | – | 177.811 s | 0 | 0.76 s | baseline; **unsound**, see E6 |
| E6 | `strat/impact-isolate` (`41c2043`) | [3](https://github.com/carsonwng/river-harbor-juniper/pull/3) | 176.882 s | 0 | 0.74 s | promising (soundness) |
| E7 | `strat/impact-relprof` (`03afebd`) | [2](https://github.com/carsonwng/river-harbor-juniper/pull/2) | 123.726 s | 0 | 0.74 s | promising; falls back to the unsound baseline selection when it proves nothing |
| E8 | `strat/impact-attrs` (`50b290d`) | [1](https://github.com/carsonwng/river-harbor-juniper/pull/1) | 155.957 s | 0 | 3.3 s | promising; not isolation-sound on its own |
| E9 | `strat/impact-combo` | pending | pending | | | running |

The orchestrator re-ran each sample eval from the branch's worktree and got the same numbers.

### E6 `impact-isolate`: the baseline misses failing tests

- Coverage recorded in one process per xdist worker credits code behind per-process caches to
  the first test only. Forking every test after collection gains 19,402 (line, test) pairs in
  6 functions: `generator._build_dispatch` goes from 15 tests to 1348, `_Dialect._try_load`
  from 26 to 808. The caches are `_DISPATCH_CACHE` and the dialect registry.
- Five hand-made probe mutants each make the baseline miss failing tests (31, 780, 1, 263 and 9
  missed); the branch misses none. Two were labelled by the full suite, three on a subset of
  test files.
- Audit fixes: tests that read sqlglot source text (`tests/test_typing.py`), import crashes
  (users are importers, including through the lazy `sqlglot.dialects.__getattr__`), whole-function
  changes (generator-ness, shadowing), subprocess tests, the always-set on empty selections.
- Cost: build about 456 s at 1 job (estimated 190 s at 4 jobs, not measured), maps unchanged.
- The baseline's map also depends on the xdist layout: `ktz3` selects 52 tests with a 4-job map
  and 18 with a 1-job map.

### E7 `impact-relprof`: relation profile of comparison sites

- A second, AST-instrumented suite pass records per test which relations (identical, equal,
  less, greater) each comparison site saw. An edit confined to the operator, or to a literal in
  the comparison, keeps only tests where some recorded evaluation would give a different result.
- `2haz`, `66ks` and `2qn5` drop to `[]`. Build about 200 s at 4 jobs (estimated), map 0.7 MB.
- Rule position: the build records observations of the pinned commit; the verdict is derived at
  run time for whatever the patch contains. It is the closest of the three to "a kind of edit",
  and the PR says so. **Humans decide whether to ship it.**
- The verdict is global: an edit is inert only if every evaluation recorded in the whole suite
  agrees under the old and the new operator or literal. There is no per-test pruning, so the
  E6 hole (code behind a per-process cache credited to the first test only) does not apply
  directly. What remains open: evaluations the labelling run would make that our single run
  never makes, and the fallback, which is the unsound baseline selection until it is rebased
  on E6.
- Agent's probes (not re-run by the orchestrator): 31 seeded probe edits, 0 unsound verdicts.
  A second hash seed changed nothing at 1948 sites; reversed test order changed signatures at
  5 sites and no decision. On 300 random operator swaps 32% are proven inert; on 200 random
  literal changes 8.5%.
- Not done: `and`/`or` (needs the operand the old code short-circuits), chained comparisons.

### E8 `impact-attrs`: import-state diff plus attribute read profile

- At run time a subprocess imports the patched sqlglot and diffs class-level state against
  build-time fingerprints; a second instrumented pass records which test reads which
  (owner class, attribute) and which table key.
- `535z` 11.2 -> 2.7 s, `4d63` 9.1 -> 0, `ktz3` 3.7 -> 2.1, `gbav` 2.5 -> 0.
- Reported by the agent, not re-run by the orchestrator: 25 probe mutants with no failing test
  outside the selection; four `arg_types` probes make sqlglot fail to import, where the baseline
  misses 333 to 844 failing tests. The branch selects every test when the import fails.
- Cost: about 0.55 s CPU per eligible commit, maps 21 MB, about 80 s more build (estimated).
- The run-time subprocess is untested under the container's limits (read-only disk, `nobody`).

## Held-out validation

`origin/validation/m1` existed during the run but held only the generator and labeller, no
`validation/sets/m1/`. **No experiment has held-out numbers, so none is a candidate.**
The probe mutants above are the only evidence beyond the sample.

## Decisions made without asking

- Scored `strat/impact` merged with `main` as the baseline, on a new branch `strat/impact-base`.
- Used the brief's branch names (`cloud/...`, `strat/impact-*`), not the session's default branch.
- Spent one of three first-round agents on soundness, not on score.
- Closed H4 and H5 by argument, without experiments.
- Kept `relprof` despite its rule position, with the decision left to humans in the PR.
- Excluded the agents' `.scratch/` directories (368 MB of mutated sqlglot copies) locally.

## Board, next steps

Filled in when E9 finishes.
