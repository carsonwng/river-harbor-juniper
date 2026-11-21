# strat/impact-combo: one sound impact layer from isolate + relprof + attrs

Branch `strat/impact-combo`, cut from `strat/impact-isolate`, with `strat/impact-relprof` and
`strat/impact-attrs` merged in (credited in the merge commits). All numbers are **local**
(host Python 3.13, no Docker in this session). Maps built with `BR_IMPACT_JOBS=4` on a copy of
pinned sqlglot **without `__pycache__`** (as `/opt/sqlglot` is in the image).

## Hypothesis

The three siblings save on different commits (relprof: comparison operator/literal edits;
attrs: class-level constants and table entries; isolate: closes per-process-cache holes and
audit gaps). Combined they should land near 102 s on the sample. But relprof and attrs record
their profiles in one shared pytest process per xdist worker, so they could inherit the hole
isolate found for coverage. The combination must keep every isolate fix and make each profile
sound against cross-test state, inside the build budget.

## What each profile needs (measured, not assumed)

* **attrs is per test** (which tests read attribute X / key K): a reader that consumes a value
  an earlier test cached is invisible in a shared process. It is now recorded in the **forked
  pass** (every test in its own fork of the post-collection process, isolate's machinery).
* **relprof is global** (an edit is inert only if *every* evaluation in the whole pass agrees
  under the old and new comparison; see `strategies/impact-relprof.md`). A site behind a cache
  is still evaluated by whichever test fills it. What can go wrong is (a) evaluations that a
  labelling run makes but no test of our pass makes, (b) the "every covering test evaluated
  the site" check using shared-process coverage. Checked offline, once:
  - a forked relation profile (preimported, every test isolated) vs the shared-process one:
    over 1977 sites, every forked signature is subsumed by a shared-process signature
    (same known codes) **except one site**, `dialect.py:232`
    `len(DIALECT_MODULE_NAMES) != len(cls._classes)`: its operands depend on how many
    dialects are registered, i.e. on which tests ran before (`TestDialectEntryPoints` clears
    `_Dialect._classes`; test-defined dialects join it).
  - the other way round, the shared-process pass sees the polluted branch of
    `_Dialect._try_load` (line 254) that no forked test reaches.
  So relprof stays in the **shared-process pass** (cheaper, and it sees polluted-state
  evaluations), with two generic rules for what (a) showed: no verdict at sites in a
  **state-dependent function** (isolate's set: lines that ran in the shared process but not
  forked: `_try_load`, `get`), and no verdict at sites in a function that **loads a class
  attribute the run-time state check found changed** at the end of the shared pass
  (`_classes`; 4 functions). (b) uses the union coverage (forked + in-process + widening):
  e.g. `_build_dispatch` lines are covered by 1348 tests but evaluated by 15 in the shared
  process, so an unconditional site there never gets a verdict.

## Method

### Build (`build/impact.py`, `build/impact_lib/br_prof.py`, `br_cov_plugin.py`)
1. Import probe, `attrs/pyc` (compileall), pristine fingerprints `attrs/state.json` (built
   **from that bytecode**, see fixes), tracked names, a shallow pristine snapshot for the
   run-time state check.
2. **Shared-process pass** = isolate's in-process coverage pass + relation instrumentation
   (`BR_IMPACT_PROFILE=1 BR_PROF_RELPROF=1`): `br_prof` installs one import hook that rewrites
   comparisons in `sqlglot/` (positions kept; test modules keep pytest's own rewriting).
   Measured: line coverage identical to the plain in-process pass (0 differing (line, test)
   pairs on executor+tsql+optimizer, single process). At its end the plugin imports
   everything, empties the pure memo caches, re-registers the standard dialects and diffs
   the state against the shallow pristine snapshot (`state_changes`).
3. **Forked pass** = isolate's fork-per-test coverage + attribute instrumentation
   (`BR_PROF_RELPROF=0`, `BR_ATTR_TRACKED`): the parent imports every sqlglot module once,
   wraps class-level dicts (attrs' `TD`), and each child ships coverage *and* its attribute /
   key records through isolate's pipe (`_child_records`). Memo caches are emptied once per
   child (the child rebuilds what it uses). Measured: line coverage ⊇ an uninstrumented forked
   pass (0 lost, 136 gained pairs); every (line, test) pair isolate's own forked pass had and
   this build lacks (fork ∪ in-process) is an import-time line (mapped by the probe) or in a
   state-dependent function (widened): 0 others.
4. **Profile levels**: a forked test over its budget (`BR_IMPACT_PROFILE_BUDGETS`, 240 s) is
   killed and re-run with every live function's code object swapped for an uninstrumented
   compile of the same source (same lines, so same coverage); the test is then "unprofiled"
   and the selector adds it to every attrs verdict about a class it uses. With the sample
   suite no test needs it (test_max_nodes takes about 90 s instrumented).
5. Time limit: `BR_IMPACT_BUDGET` (400 s): the shared pass must end by 50 %, the forked pass
   by 85 % of it; tests not started by then are reported failed (unsure, always selected).
   A pass that produces nothing leaves the profile maps out (selector: isolate behaviour).
6. Postprocess: isolate's maps, `dependent.json` (state-dependent functions with span, loads,
   dynamic-read flag, widened testers; plus "state" functions for relprof), `relprof.json`
   (shared-process records), `attrs/*` (forked records; `ok` only if the state check found
   nothing but benign changes).

### Run (`blast/impact.py`)
The isolate selection is computed first (all F1-F6 rules). Then, per changed file:
* `keep` = source-text readers (F1), whole-function changes (F2), process-starting tests (F4);
  `select()` adds the always-set (F5) whatever happens.
* **Import check** (`BR_IMPACT_IMPORT_CHECK`, default on) for edits confined to import-time
  code: the edited sqlglot is imported in a subprocess (`br_state.py diff`, bytecode from
  `attrs/pyc`, edited module from text); a module that no longer imports → every test. Where
  it does not run (function-body edits) isolate's F3 `crash_users` rules stay in force; where
  it runs and the import succeeds, the F3 crash users are not needed (the import is the
  stronger test).
* **attrs** (`BR_IMPACT_ATTRS`): for edits confined to class-level assignments, every changed
  (class, attr[, key]) selects its forked-profile readers, plus the widened testers of every
  state-dependent function that loads the attribute name (or reads attributes dynamically),
  plus unprofiled users of the class. Anything the profile cannot vouch for → isolate.
* **relprof** (`BR_IMPACT_RELPROF`): an edit confined to operators/literals of single-operator
  comparisons is inert only if no site is in a state-dependent or "state" function, every
  recorded evaluation agrees, every covering test (union coverage) evaluated an unconditional
  site, and no unprofiled test reaches it; then only unsure tests (+ keep) stay.
* Both refinements also run when the isolate rules select every test (core import-time
  lines): the diff is a measurement and the relation verdict is global.
* Any failure (subprocess error, 20 s timeout, 60 s diff budget per run) → isolate selection.
* All switches off (`BR_IMPACT_RELPROF=0 BR_IMPACT_ATTRS=0 BR_IMPACT_IMPORT_CHECK=0`) gives
  the isolate selection.

### Fixes found while combining
* **pyc vs source set order** (attrs, would have hit the image): the image's `/opt/sqlglot` has
  no `__pycache__`, so `state.json` was fingerprinted from source while the run-time diff
  imports `attrs/pyc`; constant sets iterate differently after marshal (e.g.
  `ClickHouseParser.AGG_FUNC_MAPPING`, built by iterating a set literal), so every diff
  reported a spurious change and attrs always fell back. Pristine fingerprints are now built
  from the same bytecode, and the edited module is compiled through a marshal round trip.
  (Locally it was hidden because the shared `.cache/sqlglot` has `__pycache__`.)
* **Canonical class names** depended on registry insertion order and lazily cached module
  attributes, so a run-time state check reported renamed dynamic classes. Names now come from
  the defining module first, registries sorted by key.
* attrs' TD wrapping and start/end check moved into the forked parent; the shared pass checks
  against a *shallow* pristine snapshot (functions by name; it runs instrumented code).
  Benign additions: `UNITTEST` (bound at import from `"pytest" in sys.modules`), class-level
  `defaultdict`s growing on reads of missing keys (`Hive.COERCES_TO`), classes reachable only
  through gc (test-defined dialects' dynamic tokenizers).

### Build-time work (to get under 5 min)
relprof's hot loops cost most: `test_max_nodes` (28.8 s labelled) took ~220 s with both
instrumentations in one forked child. Changes: relation helper fast paths (exact str/number
pairs keyed on `(types, ==, <, >)`, which determine all eight results; other pairs keyed on
operator, types, identity and own result, exactly what `_signature` uses; checked against
`_signature` on mixed values: 0 differences), attribute owner cache (re-validated, cleared per
phase), no recording of reads on opaque classes (enums: `TokenType.X` was most reads), memo
caches emptied once per child, `gc.freeze()` before fork, LPT order for the forked pass
(in-process durations, `--maxschedchunk 1`), the test-module import probe in parallel with the
passes, lower profile levels compiled on demand, uninstrumented xdist controllers.

## Results (local)

Build (4 jobs, measured, pycache-free sqlglot): **281.8 s** wall (probe 2.1, pyc 0.8, state
3.6, tracked + check snapshot 2.4, shared pass 136.0, forked pass 128.8, test-import probe 8.5
in parallel, postprocess 8.0, profile post 0.8). CPU 12.5 min. For comparison isolate alone at
4 jobs on the same tree: 183 s. Maps **20 MB** (`attrs/` 13 MB: pyc 5.9, state 3.4,
attrs.json 2.3, sets 1.1; `relprof.json` 0.7), world-readable.

| set | variant | simulated s | misses | run CPU s (wall) |
| --- | --- | --- | --- | --- |
| sample (30) | base (`strat/impact-base`, base maps) | 177.811 | 0 | 0.76 |
| sample | combo, all on | **103.559** | **0** | 4.1 |
| sample | `BR_IMPACT_RELPROF=0` | 156.336 | 0 | 4.0 |
| sample | `BR_IMPACT_ATTRS=0` | 125.428 | 0 | 3.9 |
| sample | `BR_IMPACT_ATTRS=0 BR_IMPACT_IMPORT_CHECK=0` | 125.428 | 0 | 0.8 |
| sample | all three off (isolate) | 178.205 | 0 | 0.7 |
| held-out m1 (56, partial) | base | 573.479 | **2** (8owm, doay) | - |
| held-out m1 | combo, all on | **467.683** | **0** | 16.2 |
| held-out m1 | `BR_IMPACT_RELPROF=0` | 515.150 | 0 | 16.1 |
| held-out m1 | `BR_IMPACT_ATTRS=0` | 581.695 | 0 | 15.9 |
| held-out m1 | `BR_IMPACT_ATTRS=0 BR_IMPACT_IMPORT_CHECK=0` | 526.012 | 2 | 2.7 |
| held-out m1 | all three off (isolate) | 573.479 | 2 | 2.7 |

(The isolate figure here is with 4-job maps; isolate's own 176.882 used 1-job maps. The
difference is `ktz3`, whose registry rule follows the "first dispatch builder" per worker:
53 tests at 4 jobs vs 19 at 1 job, as isolate's notes predict.)

With misses at 300 s: held-out base 1173.5 s, combo 467.7 s (+16 s CPU).

### Per-commit changes (sample; others identical to base)

| commit | base s (tests) | isolate (notes) | relprof (notes) | attrs (notes) | combo s (tests) | failing |
| --- | --- | --- | --- | --- | --- | --- |
| 2haz | 25.454 (86) | = | 0 | = | **0 (0)** | 0 |
| 66ks | 24.787 (51) | = | 0 | = | **0 (0)** | 0 |
| 535z | 11.238 (171) | = | = | 2.671 (3) | **2.671 (3)** | 1 (kept) |
| 4d63 | 9.122 (288) | = | = | 0 | **0 (0)** | 0 |
| ktz3 | 3.701 (52) | 3.084 (19) | = | 2.078 (3) | 2.446 (4: +test_lazy_load, F4) | 0 |
| gbav | 2.542 (35) | 2.219 (33) | = | 0 | **0 (0)** | 0 |
| 2qn5 | 2.536 (6) | = | 0 | = | **0 (0)** | 0 |
| rszg | 5.764 (3) | +0.01 | = | = | 5.775 (4) | 3 |

### Held-out m1, where combo differs from base
Gains: `bf82` 36.8 -> 5.0, `gu57` 36.8 -> 4.8, `h3rw` 36.8 -> 2.4 (an `is not None` -> `!= None`
in a core import-time table: isolate says every test, relprof proves it inert), `flbx` 12.7 ->
2.4, `0xw8` 11.4 -> 2.9, `3nvz` 10.2 -> 0, `c9om` 9.2 -> 0, `frjh` 9.2 -> 0, `hxee` 8.0 -> 0,
`lgov` 5.1 -> 0, `1tbb` 13.2 -> 10.9. Costs: `8owm` and `doay` 13.2/4.8 -> 36.8: both edits
(`*X` -> `+X` in a class body) make the dialect modules fail to import; base and isolate
**miss** 1161 and 17 failing tests there (the F3 rule only covers removed bindings), the
import check selects every test. No miss in any combo variant with the import check on.

### Probe mutants (26, each labelled with the **full suite**, 4 workers, `SKIP_INTEGRATION=0`)

`.scratch/` (untracked): `mkprobe.py`, `label.py`, `check.py`. "isolate" = all switches off.

| probe | edit | failing | combo selects / misses | isolate selects / misses |
| --- | --- | --- | --- | --- |
| h1_dispatch (isolate) | `_build_dispatch` `not in` -> `in` | 1321 | 1347 / 0 | 1347 / 0 |
| h1_tryload (isolate) | `_try_load` module path | 801 | 1235 / 0 | 1235 / 0 |
| typing_hive (isolate) | hive typing entry redundant | 2 | 607 / 0 | 607 / 0 |
| f3_snowflake_single (isolate) | delete `SINGLE_TOKENS` | 1087 | 1620 / 0 | 1087 / **1** |
| f3_merge_maxcopy (isolate) | delete `MAX_COPY_FACTOR` | 150 | 1620 / 0 | 150 / 0 |
| **c_tryload_ne** (new) | `_try_load` polluted branch `== key` -> `!=` | 68 | 1235 / 0 | 1235 / 0 |
| **c_classes_gt / _lt** (new) | `_Dialect.classes` `!=` -> `>` / `<` (state-valued operands) | 0 / 0 | 1 / 0 | 1 / 0 |
| **c_init_isnone_eq** (new) | `Generator.__init__` cache-miss `is None` -> `== None` | 0 | 1347 / 0 | 1347 / 0 |
| **c_transforms_duckdb** (new) | DuckDB `TRANSFORMS` entry (read through the dispatch cache) | 9 | 10 / 0 | 392 / 0 |
| r_gen4946, r_gen3104, r_jsonpath128, r_pg333, r_table34 (relprof) | `is`/`==` swaps | 0 each | 0 each | 2-60 / 0 |
| r_parser2129 (relprof) | `== token_type_a` -> `is` | 0 | 1 / 0 | 1505 / 0 |
| r_core884, r_parser72, r_snow122, r_simplify1729 (relprof) | divergent swaps | 313, 2, 9, 16 | 326, 2, 227, 51 / 0 | same / 0 |
| a_tsql203, a_bq38, a_gen435, a_dialect445, a_tokens207 (attrs) | class constants / table values | 4, 8, 10, 5, 3 | 4, 39, 51, 756, 1203 / 0 | 153-1590 / 0 |
| a_query647 (attrs) | `arg_types` entry made required (dialects fail to import) | 1137 | 1620 / 0 | 153 / **992** |

**26 probes, 0 misses** for the combination; the isolate-only selection misses 2 (an import
failure, and one test of isolate's own `f3_snowflake_single`, `test_dialect_attribute_access_is_cached`,
which its subset label did not include).

### Run-time checks
Simulated in `unshare -m -n` (`.scratch/rt1/run.sh`): no network, a 64 MB tmpfs `/tmp`, the
worktree (code, maps, sqlglot copy) bind-mounted read-only, the selector as uid/gid 65534
with `env -i HOME=/tmp`: 30 lines, same selection, 103.559 s, 0 misses, 4.1 s CPU (4
import diffs). Not tested here: the real image (no Docker), cgroup CPU accounting.

## Rule check (why this is not a patch->tests table)
Every map is an observation of the pinned commit's own test suite (line coverage in two
process layouts, comparison operand relations and literal-side values, attribute and table
key reads, import-time fingerprints, which state tests change). No map is keyed by a patch,
a mutant or an edit kind, and nothing reads labelled answers. At run time the selector
derives a verdict: it imports the edited code and compares the resulting state, or evaluates
the new comparison on recorded operand relations. **Closest to the line**: relprof records,
per evaluation, the results of all eight comparison operators (a property of the two
operands, but designed with operator substitution in mind); the literal-value records are a
plain value profile. relprof's author and I both read this as a profile of the pinned code;
the humans should decide. `BR_IMPACT_RELPROF=0` drops it (sample 156.3, m1 515.2, 0 misses).

## Risks and what would break it
* Labelling-order effects beyond what one shared-process order and the forked pass show:
  checked only for relprof (one offline comparison) and only through the end-of-pass state
  check for attrs. A registry that tests change and restore (not left changed at the end)
  is not seen by the check.
* `_Dialect._classes`-like state: handled by name (functions that load a changed class
  attribute); a registry reached through another name would not be.
* Build time 282 s of a ~4.5 min share (10 min image incl. pip and triage): a slower builder
  hits the deadlines, which degrade to "unsure" tests (sound, less precise), not to failure.
* Run CPU: about 0.6-0.9 s per import-time edit (held-out: 16 s for 56 commits); the diff has
  a 20 s timeout and a 60 s budget per run, after which it falls back to isolate.
* The `test_lazy_load` subprocess writes `__pycache__` into the sqlglot tree during the build;
  harmless here (the run-time diff uses its own `PYTHONPYCACHEPREFIX`), noted for others.

## Dead ends
* One forked pass with both instrumentations: 305 s for that pass alone (test_max_nodes
  ~220 s in one child). Relation records do not need isolation (global verdict, checked), so
  they moved to the shared pass, which isolate runs anyway.
* Relation profile marked "unprofiled" for tests over a short budget (40 s): sound, but it
  dropped the 2haz/66ks verdicts (test_max_nodes and test_execute_tpcds reach those sites).
* Uninstrumented in-process pass plus instrumented passes: one pass too many for the budget.

## PR body

```
## Hypothesis
relprof (comparison verdicts) and attrs (class-attribute readers) save on different commits
than isolate; combined they should approach 100 s on the sample, but both profiles were
recorded in one shared process per worker, the hole isolate found for coverage.

## Method (mechanism, not tuning)
- attrs' per-test read profile now comes from isolate's forked pass (one import hook,
  records shipped with the coverage from each child); relprof's global verdict stays in the
  shared-process pass, which isolate runs anyway (offline check: a forked relation profile
  adds one signature, at a registry-valued site; such sites get no verdict).
- No relation verdict in state-dependent functions or functions reading registries that the
  run-time state check finds changed; attrs adds the testers of state-dependent functions.
- Refinements apply after the isolate selection and keep source readers, subprocess tests,
  whole-function changes and the always-set; the edited-sqlglot import check runs for every
  import-time edit (every test if it fails). Switches: BR_IMPACT_RELPROF, BR_IMPACT_ATTRS,
  BR_IMPACT_IMPORT_CHECK; all off = isolate.
- Fixed: pristine fingerprints now from the same bytecode as the run-time diff (in the image
  every diff fell back); canonical class names independent of registry order.

## Results
| set | simulated s | misses | run CPU s | vs baseline |
| sample (local) | 103.559 | 0 | 4.1 | 177.811 -> 103.559 |
| held-out m1 (56, partial) | 467.683 | 0 | 16.2 | base 573.479 + 2 misses |
Build 281.8 s wall at 4 jobs (measured), maps 20 MB. 26 full-suite probe mutants: 0 misses.

## Per-commit changes (sample)
2haz, 66ks, 2qn5, 4d63, gbav -> 0; 535z 11.24 -> 2.67; ktz3 3.70 -> 2.45; rszg +0.01.

## Rule check (why this is not a patch->tests table)
All maps observe the pinned commit's suite; verdicts are derived at run time by importing the
edited code or evaluating the new comparison on recorded operand relations. Closest point:
relprof's eight-operator relation per evaluation (switchable).

## Risks and what would break it
Order effects not visible in one shared order plus the forked pass; build time near its
share of the 10 min; run CPU of the import diff (about 0.7 s per import-time edit).

## Submission candidate? (yes/no, expected practice score and why)
Promising, not yet a candidate: no docker run, held-out set partial (56 of the planned
commits). Expected practice score about 40 % below base on source edits, plus avoided
import-failure misses.
```
