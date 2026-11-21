# strat/impact-combo: isolate + relprof + attrs in one selector, one deadline-aware build

Branch `strat/impact-combo`, cut from `origin/strat/impact-base` (v1b), merging
`origin/strat/impact-isolate` (PR #3), `origin/strat/impact-relprof` (PR #2) and
`origin/strat/impact-attrs` (PR #1). The mechanisms are described in each branch's
`strategies/impact-<name>.md` (kept here); this file covers how they are combined, the
build budget, and the measurements.

## Selector: how the three combine (`blast/impact.py`)

Per changed sqlglot file:

1. **Hard bits (never narrowed):** strat/impact-isolate's audit rules that do not depend on
   where a line sits: tests that read the file's source text (line 0), whole-function
   semantics (generator flag, a new local shadowing a global), and process-starting tests
   for import-time edits of core modules. The relprof branch returned `bits & unsure` and
   the attrs branch returned its own set: both dropped these, for example `test_typing`
   on a comparison edit in `sqlglot/typing/*.py`. They now always stay.
2. **Coverage/name bits:** base rules with isolate's crash users (class-body bindings read
   later at class creation, import-time function lines -> importers).
3. **Precision, only when the patch has exactly one deferred source file** (each proof
   assumes the rest of the program is the pinned one):
   - relprof: an edit made only of operator/literal changes of single-operator comparisons
     that no recorded evaluation tells apart keeps `bits & unsure_profile`.
   - otherwise attrs: an edit confined to class-level assignments runs the import-time state
     diff. Each changed (class, attr[, key]) selects its recorded readers.
4. **attrs import-failure check, for every edit confined to import-time code** (multi-file
   patches too, and even when the coverage bits are empty): if the edited sqlglot no longer
   imports some module, every test is selected. This is a soundness rule.

Result = hard | (narrowed or plain coverage bits), plus the tests the map cannot vouch for.

## Build: priorities and the deadline (`build/impact.py`)

- The Dockerfile's first `RUN` writes `/opt/br_build_start`. A stamp older than 600 s cannot
  belong to this build (it comes from a cached layer), so it is ignored, and the start is
  taken as 90 s before the impact step.
- Internal deadline: 510 s from build start (`BR_BUILD_DEADLINE`). A pass may run until
  510 - 75 s (reserve for postprocessing, triage and the image export). A pass is started
  only if its expected length (core pass x measured ratio: relprof 1.9, fork 1.8, attrs 1.6)
  fits. It is killed with its whole process group if it runs past the deadline anyway.
  A dropped pass only makes the maps coarser. `meta.json["passes"]` records each pass's
  status and seconds.
- Order: import probe -> **in-process coverage (core, always)** -> test-module import probe
  and pristine import-time state (side by side, cheap; the attrs import-failure check needs
  only the state) -> **relprof** -> **fork coverage** -> **attrs profile**
  (`BR_IMPACT_ORDER`). Why relprof before fork: relprof is worth about -54 s on the sample.
  The fork pass closes the cross-test cache holes of 6 functions (`_build_dispatch`,
  `Generator.__init__` cache-miss branch, `_Dialect._try_load`, `_Dialect.get`, ...).
  Without it the map is the in-process one of strat/impact-base, which is the map behind
  every practice submission so far (0 misses). isolate's other fixes (line 0/-1 pseudo
  lines, crash users, dynamic test imports, semantics) do not need the fork pass.
- Fewer suite runs: merging relprof into the coverage pass was measured and rejected. The
  combined pass took 367 s, against 100 s + 252 s run separately: instrumentation overheads
  add up and only collection is shared. attrs cannot share the coverage process at all,
  because it imports all of sqlglot at configure time, which removes lazy dialect loading
  from coverage (see isolate's PREIMPORT note).
- Per-test cutoff in the profile passes: `test_max_nodes` takes 39 s plain and 173 s under
  relprof, so it alone set the pass length. A phase that runs past 1.5 x its coverage-pass
  time + 10 s stops recording, and the test becomes unsure (always kept). relprof pass:
  252 -> 180 s (dev container), 160 s on the cold builder.

## Fork supplement (keeps isolate's cache-hole fix without the fork pass)

What the forked pass adds to the map is small and a property of the pinned commit: the
forked test sets of 5 functions / 70 lines where it sees more tests than the shared
process (per-process caches: `generator._build_dispatch`, the `Generator.__init__`
cache-miss branch, the dialect registry), and 2 state-dependent functions
(`_Dialect._try_load`, `_Dialect.get`). `build/impact.py --reuse --work W
--make-fork-supplement F` writes them from a build that ran both passes. Two builds gave
identical output; the union is committed as `build/impact_lib/fork_supplement.json`
(134 KB). A build without the forked pass merges it (lines -> union, then the
state-dependent widening). This is a coverage map of the pinned commit, the same kind as
`lines/`. It is not keyed by any patch.

## Run CPU

- The attrs import-failure check imports the edited sqlglot once per import-time edit.
  With the full state diff that cost about 2 s CPU each on the loaded laptop (sample: about
  6 such edits). With no read profile in use it now runs `br_state.py diff --import-only`
  (compares the modules that fail to import with `attrs/failed.json`, no fingerprints):
  about 1 s, same verdict.
- The full diff (profile in use, class-level assignment edits only) stays at about 2 s.

## Measurements

All docker numbers come from `br.py eval --docker --scorer-limits --cold`: a cold build on
the 4-CPU / 8 GB buildx builder, on a laptop shared with labelling containers. The load
varied a lot between runs (the core pass took 87 s to 229 s), which is why the deadline
logic matters.

| run | sha | core cov | relprof | attrs | fork | build | sample sim | CPU | total | misses |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 (fork > relprof > attrs) | 0ce7a3e | 99.8 | timeout at deadline | skipped | 161.3 | 485.9 | 178.220 | 1.213 | 179.433 | 0 |
| v2 (relprof > fork > attrs) | 980f598 | 86.4 | 160.2 | 133.1 (diff broken, see dead ends) | skipped | 478.2 | 131.994 | 5.391 | 137.385 | 0 |
| v3 (deterministic diff) | 185cd2b | 87.7 | 184.5 | skipped | skipped | 390.0 | 125.468 | 2.367 | 127.835 | 0 |
| v4 (supplement, heavy load) | 669c22b | 228.8 | skipped | skipped | skipped (supplement merged) | 463.3 | 199.539 | 12.430 | 211.969 | 0 |

Full maps (dev container, no deadline, every pass): sample **103.559 s** simulated, 0 misses
(oracle floor 94.4). Per commit vs base: 2haz 25.45 -> 0, 66ks 24.79 -> 0, 2qn5 2.54 -> 0
(relprof); 535z 11.24 -> 2.67, 4d63 9.12 -> 0, ktz3 4.1 -> 2.4, gbav 2.5 -> 0 (attrs).

v4 is worse than base on simulated time because, under heavy load, the in-process layout
put `test_execute_tpcds` into ktz3's registry readers (25.45 s). That is base
nondeterminism (the registry rule uses the in-process pass), not this branch. Its CPU
included a full state diff for every import-time edit, which is now import-only.

## Dead ends

- relprof inside the coverage pass: 367 s vs 100 + 252 s separately.
- Slowest-first test ordering (`br_order.py`) alone did not shorten the passes (101 s / 171 s
  vs 100 / 161 s). The tail was a single slow test, which the cutoff fixes. Kept, because it
  makes the cutoff's test start first.
- attrs run-time diff with bytecode compiled under a random hash seed: every diff reported
  `ClickHouseParser.AGG_FUNC_MAPPING` changed (a dict built from a set literal, whose
  marshalled frozenset order depends on the compiling process's seed), so attrs never
  narrowed anything in the image. Fixed: seed-0 bytecode, snapshots load it, and the edited
  module goes through a marshal round trip. An unedited diff of all 184 files reports no
  change.

## Verify and held-out data

`br.py verify strat/impact-combo` at 760d8bd (host builder, laptop saturated by labelling:
core pass 248 s, so every optional pass was skipped and only the core map, the supplement
and the import check were in the image):

| set | simulated | CPU | total | misses |
| --- | --- | --- | --- | --- |
| sample | 184.807 | 7.834 | 192.641 | 0 |
| local-20261120 (16) | 170.771 | 4.134 | 174.905 | 0 |
| m1 (70) | 629.162 | 27.901 | 657.063 | **0** |

On m1, int/night-2120 (main + base + relprof) misses `8owm`: `*exp.DataType.NUMERIC_TYPES`
becomes `+exp.DataType.NUMERIC_TYPES` in a Hive class body, a TypeError at import, so 1161
tests fail. The attrs import-failure check selects every test there. CPU on m1 is mostly that
check (about 1 s per import-time edit under load).

## Probes (mutants from the three branches' docs, rebuilt by hand; labels in br-label)

| probe | edit | label | failing | v3 image (core+relprof, no supplement) | v5 image (core+supplement) |
| --- | --- | --- | --- | --- | --- |
| h1dispatch | generator.py:86 `not in` -> `in` | subset (athena, transpile, drill) | 41 | 47 sel, **37 missed** | 1347 sel, 0 missed |
| agsets | query.py:647 `grouping_sets` False -> True | full | 1142 (import errors) | 151 sel (bug, fixed in 865d252) | all, 0 missed |
| aaliaspost | dialect.py:445 False -> True | full | 5 | 1590, 0 missed | 1590, 0 missed |
| rgen3104 | generator.py:3104 `is not None` -> `!= None` | the 51 base-selected tests | 0 | relprof prunes to 0 | 51 (no relprof) |

Selections only (not labelled, compared with the branches' own probe tables): h1tryload 1235
(isolate: 1235), typinghive 607 (599), f3snowflake / f3maxcopy all tests (the import check
finds the import error; isolate selected 1087 / 150, which covered the failing ones), rparser
2 with relprof (relprof's own run: all 1436 base-selected tests pass).

## Risks

- Builder load: on a loaded builder only the core map is built (plus the supplement and the
  import check), which is about base precision. relprof needs about 1.9x the core pass.
- Run CPU of the import check: about 1 s per import-time edit under load. It could be
  narrowed to the modules whose import loads the edited one (probe data). That is not done,
  because a module's import could depend on registry state without loading the edited module.
- The in-process registry rule is layout dependent (base behaviour): ktz3 sometimes pulls in
  `test_execute_tpcds`.

## For other strategies

- The hard/soft split in `_change_bits`: precision layers must never drop line-0 readers,
  semantics or subprocess bits.
- `run_pass` (process-group kill) plus `Budget` in `build/impact.py` for any optional build pass.
- The fork supplement pattern: commit the small, stable delta of an expensive pass as a
  pinned-commit map.
- The marshal round trip in `br_state._Override`: any run-time import of edited text that is
  compared with bytecode-loaded state needs it.
