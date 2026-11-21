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

## Measurements

(filled in below as runs complete)

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
