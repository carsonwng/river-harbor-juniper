# Run report: impact cluster, 2026-11-21 (cloud session)

Status: **finished.** One candidate: `strat/impact-combo-sound` (E9). All numbers are local
(host Python, wall time as CPU); nothing was run in Docker, which this environment lacks.

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

All cut from `strat/impact-base`. Local mode. Held-out = `validation/sets/m1` at `e257aca`
(70 commits, partial set). A miss adds 300 s, so the base's 1280.1 s is 680.1 s plus two misses.

| ID | Branch (head) | PR | Sample s | Misses | Held-out m1 s | Misses | Run CPU sample / m1 | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E5 | `strat/impact-base` (`6a6d766`) | – | 177.811 | 0 | 1280.145 | **2** | 0.8 / 3.1 s | baseline; **unsound** |
| E6 | `strat/impact-isolate` (`41c2043`) | [3](https://github.com/carsonwng/river-harbor-juniper/pull/3) | 176.882 | 0 | 1280.008 | **2** | 0.8 / 3.2 s | dead end on its own; its coverage is the base of E9 |
| E7 | `strat/impact-relprof` (`03afebd`) | [2](https://github.com/carsonwng/river-harbor-juniper/pull/2) | 123.726 | 0 | 1230.495 | **2** | 0.9 / 3.0 s | dead end on its own (inherits the base's misses); part of E9 |
| E8 | `strat/impact-attrs` (`50b290d`) | [1](https://github.com/carsonwng/river-harbor-juniper/pull/1) | 155.957 | 0 | 619.035 | 0 | 3.9 / 14.2 s | promising; superseded by E9 (has the pycache bug below) |
| E9 | `strat/impact-combo-sound` (`7a82a66`) | [6](https://github.com/carsonwng/river-harbor-juniper/pull/6) | **103.559** | 0 | **570.258** | 0 | 4.1 / 18.8 s | **candidate** |

Oracle: sample 94.351 s, m1 220.480 s.

The orchestrator re-ran every cell of this table from the branch's worktree. Sample numbers
match the agents' reports. The agents' m1 numbers were on the 56-commit state of the set.

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

### E9 `impact-combo`: the three combined (candidate)

Local branch `strat/impact-combo`, pushed as `strat/impact-combo-sound` because another session
already owns `origin/strat/impact-combo` ([PR 7](https://github.com/carsonwng/river-harbor-juniper/pull/7), not ours).

- **Design.** The isolate selection comes first. attrs records per test, so it moved into
  isolate's forked pass. relprof gives a global verdict, so it stays in the shared-process pass,
  with no verdict in state-dependent functions (the one site where forked and shared relation
  signatures differ is `dialect.py:232`). Every refinement keeps isolate's F1, F2, F4 and F5 sets.
- **Import check.** For every import-time edit the selector imports the edited sqlglot in a
  subprocess; a failed import selects every test. This is what closes the two held-out misses.
- **Switches.** `BR_IMPACT_RELPROF=0`, `BR_IMPACT_ATTRS=0`, `BR_IMPACT_IMPORT_CHECK=0`; all
  off gives the isolate selection. Agent-measured on the sample: 156.3 s without relprof,
  125.4 s without attrs.
- **Bug found in attrs.** On a tree without `__pycache__` (the image) every state diff showed a
  spurious change, because fingerprints came from source and the run-time import from bytecode
  (set iteration order differs after marshal). E8 would have fallen back everywhere in the image.
  Fixed in E9 only.
- **Build (agent-measured, 4 jobs, no `__pycache__`):** 281.8 s for the impact step (shared pass
  136 s, forked pass 129 s). `BR_IMPACT_BUDGET` (400 s) turns unfinished tests into "unsure",
  which loses precision, not recall. Maps 20 MB, built in the image, not committed.
- **Probes (agent-run, 26 mutants labelled by the full suite):** combo 0 misses; isolate alone 2.
- **Sandbox (agent-run):** same output under `unshare -m -n`, read-only tree, 64 MB `/tmp`,
  uid nobody.
- **Review by the orchestrator.** `blast/impact.py` reads only the maps and the pinned sqlglot
  source, never answers. The diff against `main` touches only impact files plus `STRATEGY.md`
  and `strategies/`. One weakness found: when the import-check subprocess times out or
  crashes, the selector falls back to the isolate selection, which is the selection that misses
  import failures. It should fall back to every test (see next steps).

## Held-out validation

`validation/sets/m1` appeared late in the run (42, then 56, then 70 labelled commits; 70 at
`e257aca` is what the table uses). Kinds: 41 code, 18 fixture, 6 multi, 5 comment.

| kind | n | base s | combo s |
| --- | --- | --- | --- |
| code | 41 | 488.9 (+600 for 2 misses) | 381.4 |
| fixture | 18 | 135.0 | 135.0 |
| multi | 6 | 53.5 | 51.1 |
| comment | 5 | 2.8 | 2.8 |

- **The two base misses**, `8owm` (1161 failing tests, 355 selected) and `doay` (17 failing, 13
  selected), are `*X` to `+X` edits in a class body that break dialect imports. Base, isolate
  and relprof miss both. The sample has no commit of this kind, so the sample alone would never
  have shown it.
- Combo is worse than base on exactly those two commits (36.8 s each, every test), and equal or
  better on the other 68.
- Combo against base without counting the miss penalty: 570.3 against 680.1 s, 16% lower. With
  the penalty: 55% lower. The stop condition (0 misses on both sets, more than 10% better) is met.
- The set is partial and comes from one mutant generator. It is not known how close it is to
  the hidden sets (Q6).

## Decisions made without asking

- Scored `strat/impact` merged with `main` as the baseline, on a new branch `strat/impact-base`.
- Used the brief's branch names (`cloud/...`, `strat/impact-*`), not the session's default branch.
- Spent one of three first-round agents on soundness, not on score.
- Closed H4 and H5 by argument, without experiments.
- Kept `relprof` despite its rule position, with the decision left to humans in the PR.
- Excluded the agents' `.scratch/` directories (368 MB of mutated sqlglot copies) locally.
- Cut the combine branch from `strat/impact-isolate`, so the sound coverage is its fallback.
- Accepted the combo agent's choice to push as `strat/impact-combo-sound` (name clash).
- Stopped after one combine round: the stop condition was met.
- Did not re-measure the 282 s build or re-run the 26 probes; both are agent-reported.

## Next steps

1. **Docker eval of `strat/impact-combo-sound`** (`br.py eval --docker`): confirms the build fits
   10 min on the scorer's 4 cores and that the run-time subprocess works as `nobody` on a
   read-only disk. This is the gate before any submission.
2. **Decide the relprof rule question** (see `open-questions.md` Q7). If the answer is no,
   submit with `BR_IMPACT_RELPROF=0` set in the Dockerfile.
3. Make an import-check failure or timeout select every test, not the isolate selection.
4. Re-validate on the full m1 set when the labelling session finishes.
5. The remaining gap is fixture commits (135 s on m1, untouched by impact) and code commits
   that still select every test (36.8 s each).

## Board (`br.py board --all`, end of run)

```
    total       sim     cpu miss mode   data     branch                     sha       note
  107.438   103.559   3.879    0 local  sample   strat/impact-combo         0058def   impact-combo: isolate + relprof(shared) + attrs(forked), local maps c7
  107.505   103.559   3.946    0 local  sample   strat/impact-combo         df6e9ce   impact-combo df6e9ce (maps c8, 4-job build 282 s)
  107.699   103.559   4.140    0 local  sample   strat/impact-combo         7a82a66   orchestrator re-run impact-combo sample
  124.467   123.726   0.741    0 local  sample   strat/impact-relprof       03afebd   relprof v2 clean harness build
  124.469   123.726   0.743    0 local  sample   strat/impact-relprof       03afebd   orchestrator re-run relprof
  124.500   123.726   0.774    0 local  sample   strat/impact-relprof       f2d66d1   relprof v2
  124.509   123.726   0.783    0 local  sample   strat/impact-relprof       b363244*  relprof v1 fast compare-edit diff
  124.519   123.726   0.793    0 local  sample   strat/impact-relprof       36783f6   relprof v1
  124.633   123.726   0.907    0 local  sample   strat/impact-relprof       03afebd   orchestrator re-run impact-relprof sample
  124.649   123.726   0.923    0 local  sample   strat/impact-relprof       6a6d766*  relprof v0
  124.723   123.726   0.997    0 local  sample   strat/impact-relprof       b363244   relprof v1
  124.754   123.726   1.028    0 local  sample   strat/impact-relprof       277569c*  relprof v1 (values, no literal guard)
  159.269   155.957   3.312    0 local  sample   strat/impact-attrs         620e1e0   attrs v1: state diff + attribute profile
  159.303   155.957   3.346    0 local  sample   strat/impact-attrs         50b290d   orchestrator re-run of attrs 50b290d
  159.327   155.957   3.370    0 local  sample   strat/impact-attrs         50b290d   attrs v2: state diff + profile + import-failure rule
  159.823   155.957   3.866    0 local  sample   strat/impact-attrs         50b290d   orchestrator re-run impact-attrs sample
  159.949   155.957   3.992    0 local  sample   strat/impact-attrs         6a6d766*  attrs v0 (dirty)
  160.145   155.957   4.188    0 local  sample   strat/impact-attrs         6a6d766*  attrs v0 (dirty)
  177.602   176.882   0.720    0 local  sample   strat/impact-isolate       8794ace   isolate 8794ace: fork+inproc union, state-dependent fn widening, audit F1-F6 (1-job maps)
  177.625   176.882   0.743    0 local  sample   strat/impact-isolate       41c2043   orchestrator re-run isolate
  177.649   176.882   0.767    0 local  sample   strat/impact-isolate       41c2043   isolate final (committed)
  177.650   176.882   0.768    0 local  sample   strat/impact-isolate       41c2043   orchestrator re-run impact-isolate sample
  177.694   176.882   0.812    0 local  sample   strat/impact-isolate       dabb42b   isolate: fork+inproc union (1-job maps), audit fixes F1-F6
  178.547   177.811   0.736    0 local  sample   strat/impact-attrs         6a6d766   base reproduce
  178.553   177.811   0.742    0 local  sample   strat/impact-relprof       6a6d766*  base sanity (no relprof map)
  178.569   177.811   0.758    0 local  sample   strat/impact-base          6a6d766   base = main + strat/impact v1b
  178.578   177.811   0.767    0 local  sample   strat/impact-isolate       6a6d766   base reproduce
  178.652   177.811   0.841    0 local  sample   HEAD                       6a6d766   orchestrator re-run impact-base sample
  178.660   177.811   0.849    0 local  sample   strat/impact-isolate       6a6d766*  audit fixes F1-F4 on base maps
  179.402   178.629   0.773    0 local  sample   strat/impact-isolate       6a6d766*  audit fixes F1-F4 on base maps (no line 0/-1 data)
  483.809   467.683  16.126    0 local  m1       strat/impact-combo         df6e9ce   impact-combo df6e9ce held-out m1 (56)
  517.480   502.132  15.348    0 local  m1       strat/impact-combo         0058def*  impact-combo 0058def held-out m1 (partial 56)
  589.092   570.258  18.834    0 local  m1       strat/impact-combo         7a82a66   orchestrator re-run impact-combo m1(70)
  633.192   619.035  14.157    0 local  m1       strat/impact-attrs         50b290d   orchestrator re-run impact-attrs m1(70)
  717.818   717.423   0.395    0 local  sample   cloud/20261121-impact-cluster 544b5ba   cloud baseline with maps
 1104.599  1104.510   0.089    0 local  sample   cloud/20261121-impact-cluster 544b5ba   cloud baseline reproduce
 1233.485  1230.495   2.990    2 local  m1       strat/impact-relprof       03afebd   orchestrator re-run impact-relprof m1(70)
 1283.173  1280.008   3.165    2 local  m1       strat/impact-isolate       41c2043   orchestrator re-run impact-isolate m1(70)
 1283.208  1280.145   3.063    2 local  m1       HEAD                       6a6d766   orchestrator re-run impact-base m1(70)
```
