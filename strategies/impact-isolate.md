# strat/impact-isolate: coverage that is sound against cross-test state, plus an audit of the base selector

Branch `strat/impact-isolate`, cut from `strat/impact-base` (v1b). All numbers are **local**
(host Python 3.13, no Docker in this session). Held-out data (`validation/m1`): see the
"Held-out" section (status at time of writing).

## Hypothesis

**H1.** The base coverage map is recorded in one long-lived pytest process per xdist worker.
A test that only *consumes* state an earlier test of the same process computed (a per-class
cache, a lazily built table, a registry filled on first use) is not recorded as running the
code that computed it. If an edit breaks that code, every consumer fails in the labelling run
(order and process layout unknown), but coverage names only the first test per process. Each
such hole is a 300 s miss.

**H1b.** Several rules of `blast/impact.py` return fewer tests than "all users" on an
argument that can be wrong (source-text readers, import-time crashes, process launches,
whole-function semantics). Each is a miss class.

## Method

### 1. Fork isolation of the coverage pass (`BR_IMPACT_ISOLATE`, `--isolate`)

`build/impact_lib/br_cov_plugin.py`, `pytest_runtest_protocol` (tryfirst, only when
`BR_IMPACT_ISOLATE=fork`): every test runs in its own `os.fork()` of the post-collection
worker process: setup (including `setUpClass` and module fixtures), call and a *full*
teardown (`nextitem=None`), then the child pickles its coverage records, its outcomes
(failed subtests included) and its serialised reports through a pipe, and exits with
`os._exit`. The parent replays the reports (`pytest_runtest_logstart/logreport/logfinish`),
so xdist, the terminal and outcome bookkeeping work unchanged. A child that dies or exceeds
`BR_IMPACT_CHILD_TIMEOUT` (600 s) is reported failed, so the test becomes "unsure" and is
always selected. The in-process `_InlinePool` (pickling round trip for process pools) is
inherited by every child unchanged. `setUpClass` coverage keeps the base semantics
(setup-phase lines count for the whole class).

Import-time execution is **not** attributed to tests in fork mode
(`BR_IMPACT_SKIP_IMPORTS`, default on with fork): `importlib._bootstrap._find_and_load`
(which both `import` and `importlib.import_module` go through for a module not loaded yet)
is wrapped with a depth counter; while it is positive, LINE events are neither recorded nor
disabled. Without this every forked test re-imports the lazily loaded dialects and becomes a
"user" of every class their import touches (sample `ktz3`: 18 -> 1347 tests). Import-time
lines are mapped per module by the import probe, as before.

`BR_IMPACT_PREIMPORT=1` (off) would import every sqlglot module in the parent before forking;
it makes children cheaper but then no test runs lazy-loading code (`Dialect._try_load`), so it
stays off.

`build/impact.py --isolate none|fork|both` (default `both`, env `BR_IMPACT_ISOLATE`): `both`
runs the in-process pass and the forked pass and unions them. Why both and not fork alone:
the in-process pass also sees lines that only run in *polluted* state (below), and it keeps
one deliberately coarse rule at base precision (registries, below).

**Measured effect** (1-job maps, in-process vs forked, lines that never run at import):

| function | tests (in-process) | tests (forked) |
| --- | --- | --- |
| `generator._build_dispatch` (lines 77-89) | 15 | 1348 |
| `Generator.__init__` cache-miss branch (1007-1008) | 15 | 1348 |
| `_Dialect._try_load` (240-258) | 26 | 808 |
| `_Dialect.get` (302) | 30 | 810 |
| `Expression.__hash__` (845-868) | 341 | 342 |
| `_Dialect.__getitem__` (295) | 0 | 1 |

19,402 (line, test) pairs gained in 6 functions / 34 lines, all of them per-process caches:
`_DISPATCH_CACHE` (generator dispatch per generator class) and the dialect registry
(`Dialect._classes`, filled by the first lookup of each dialect). Everything else in the
suite is order-independent at test time: no `lru_cache`/`cached_property` in sqlglot, and
only 5 non-import function lines run during collection. With 4 xdist workers the base map
shows the same effect at 4x the "first user" count (`ktz3` 52 tests at 4 jobs vs 18 at 1
job), i.e. the base map depends on the worker layout.

The forked pass *loses* 157 pairs: `_try_load` lines 249-257 and 261-288 run in the shared
process only after `TestDialectEntryPoints.setUp` calls `Dialect._classes.clear()`, i.e. in
whichever tests happen to follow it. A function with such a line (ran for some test in the
shared process but not when that test ran alone; import-time lines excluded) is
**state-dependent**: which tests take its polluted path in the labelling run depends on that
run's order and layout. Every line of such a function is widened to every test that ran it
in any pass **or ran a function that references it by name** (its callers take the polluted
branch). Found: `_Dialect._try_load` (-> 1236 tests) and `_Dialect.get` (-> 1596 tests).
Line-level widening alone was not enough: on the full-suite serial label of probe
`h1_tryload` it still missed 88 tests (`test_executor`, `test_expressions`, ... which run
after the entry-point tests and re-register every dialect through `_try_load`); the
function + callers rule misses 0.

**Registry rule kept at base precision.** The base treats any test that reads a module-level
container of classes (`EXPR_CLASSES`, `ALL_FUNCTIONS`, ...) as a user of every class in it.
Forked, every generating test builds the dispatch and reads `EXPR_CLASSES`, which would make
every generating test a user of every expression class (`ktz3`, an `arg_types` entry: 18 ->
1347 tests, 27.6 s) although the dispatch stores only generator methods. That one rule uses
the in-process pass only; lines, instances, methods, attribute and name reads use the union.

### 2. Audit fixes in `blast/impact.py` (H1b)

| # | Gap in the base | Fix | Evidence |
| --- | --- | --- | --- |
| F1 | Tests that read sqlglot source **text** (`tests/test_typing.py` `ast.parse`s every `sqlglot/typing/*.py` and compares `co_code` of annotators) fail on edits to lines they never execute | plugin audit hook on `open` outside `importlib` records "test read file F" as pseudo line 0 of F; the selector adds line 0's tests on any AST-changing edit of F | probe `typing_hive` (hive `DOUBLE`->`INT` makes an entry redundant): base misses `test_no_redundant_expression_metadata`; ours selects it |
| F3 | A class-level binding that a later statement of the same class body reads at class creation (28 such single-line statements, e.g. `Snowflake.JSONPathTokenizer.SINGLE_TOKENS`, `CopyBudget.MAX_COPY_FACTOR` read by defaults): removing it makes the module's **import raise**, failing every test whose file imports the module (collection error), not just the class's users | when the old statement's names are read later in the class body and the new class body no longer binds them: `crash_users(module)` = module users + importers. Importers = static import graph of `tests/` plus a dynamic probe (`br_test_imports.py`: import each test module in a fork, record sqlglot modules loaded; catches `from sqlglot.dialects import Snowflake` through the lazy `__getattr__`) | probe `f3_snowflake_single`: 267 failing (subset), base selects 22 and misses 263; ours 1087, 0 missed. `f3_merge_maxcopy`: base misses 9 (executor tests), ours 0 |
| F3b | Function lines that run at import used `module_users`; an exception there also fails importers | `import_bits` uses `crash_users` | reasoning |
| F2 | An edit that changes a function as a whole: it becomes/stops being a generator or coroutine (calls that never iterate run no body line), or a new local shadows a global/free name (earlier reads raise) | compare old/new code objects per qualname (`co_flags` generator bits, locals vs `co_names`): whole-function coverage, plus readers of its name for the generator case | reasoning; no sample/probe hit |
| F4 | Tests that start processes (`test_lazy_load` runs `python -c "import sqlglot; ..."`) execute sqlglot unseen | audit events `subprocess.Popen`/`os.exec`/`os.posix_spawn` record pseudo line -1 of `sqlglot/__init__.py`; added for import-time edits of core modules | reasoning |
| F5 | `select` returned `[]` when no test covered the edit, dropping the "always" tests (unsure under coverage, unknown to the map) | they are selected whenever a change has a real AST diff | currently empty set, so no cost |
| F6 | Lambda shortcut used test-time coverage only | adds import-time lines of the lambda body (excluding its first line, which also holds the table's import-time code) | reasoning |

Checked and found fine (no change):
- lines "between statements" returning 0: only blank/comment lines reach it; multi-line
  strings, decorators, continuations, `else:`/`finally:` headers are inside a statement span
  (header edits of compound statements take the whole statement); `global`/`nonlocal` have
  no code and fall back to the previous sibling or the enclosing function, which every test
  reaching the declaration ran; `match_case` has no `lineno` -> exception -> all tests.
- previous-sibling fallback when the sibling always returns/raises: the new statement is
  then dead code; selecting the sibling's tests is a superset (generator/locality effects are
  F2).
- signature/default/decorator edits: body coverage plus name readers; sqlglot decorators
  (`unsupported_args`, `null_if_any`) always call the body. Factories called in class bodies
  (`_show_parser`, `build_*`): inner bodies are covered per test, outer bodies run at import
  and go through `import_bits` (module users; core -> all).
- dynamic reads of class-level names: the only `getattr(obj, computed)` sites read
  `*_sql`/`_parse_*` methods or classes, never class constants; there are no class-level
  `*_sql = ...` assignments; metaclass reads (`klass.__dict__.get("Tokenizer")`, ...) use
  string constants, which the build counts as import-time loads (propagating).
- `__doc__`: `test_docs.py` has no pytest-collectable tests (`load_tests` only); docstring
  edits use `__doc__` readers plus scope users (the Expression metaclass reads `__doc__`).
- `__slots__`, `__all__`, dunders: `_bound` returns None -> scope users (core -> all).
- C/Rust accelerators: `requirements.lock` has neither `sqlglotc` nor `sqlglotrs`; the
  labelling environment runs pure Python.
- `test_sqlglotc` reads `sqlglotc/setup.py` (outside `sqlglot/`: any patch there falls back
  to all tests).

Residual risks (not fixed): class-level edits that raise at import for reasons other than a
removed binding (a changed name in an annotation of a module without
`from __future__ import annotations`, `{**X}` of a non-mapping); a lambda called at import
whose whole body sits on its first line; coverage of the pristine state assumes the
labelling run collected the whole suite in every process (true for `pytest tests` and
`pytest -n N tests`; a per-file labelling run would leave fewer dialects pre-imported and
reach `_try_load` in more tests).

## Results

All local (no Docker in this session). Maps built at 1 job (CPU cap of this run); the
image builds them at 4 jobs.

| set | simulated s | misses | run CPU s (wall) | vs base (177.811) |
| --- | --- | --- | --- | --- |
| sample, base code + base maps (4-job) | 177.811 | 0 | 0.77 | - |
| sample, base code + in-process 1-job maps | 176.514 | 0 | - | -1.3 (layout effect only) |
| sample, this branch (`8794ace`, `--isolate both`) | **176.882** | **0** | 0.72 | -0.9 |
| held-out m1 | not available (branch `validation/m1` has the generator, no `validation/sets/m1` yet) | | | |

Per-commit changes vs base (sample): `ktz3` 52 -> 19 tests (3.70 -> 3.08 s; the 4-job base
map had 4x the "first dispatch builder" tests as registry readers, +1 for `test_lazy_load`
since the edit is an import-time line of a core module), `gbav` 35 -> 33 (2.54 -> 2.22 s),
`rszg` 3 -> 4 (+0.01 s, the triage-added test plus one). Everything else identical.

**Probe mutants** (hand-made, `.scratch/probes`, labelled with one pytest worker,
`SKIP_INTEGRATION=0`; "subset" = only the named test files, "full" = whole suite):

| probe | edit | label | failing | base selects / misses | ours selects / misses |
| --- | --- | --- | --- | --- | --- |
| `h1_dispatch` | `generator.py:86` `expr_cls not in dispatch` -> `in` (`_build_dispatch`) | subset (athena, transpile, drill) | 34 | 49 / **31** | 1347 / 0 |
| `h1_tryload` | `dialect.py:245` `f"sqlglot.dialects.{key}"` -> `"sqlglot.dialect.{key}"` | full | 833 | 56 / **780** | 1235 / 0 |
| `typing_hive` | `typing/hive.py:34` `DOUBLE` -> `INT` (entry becomes redundant) | full | 2 | 606 / **1** (`test_typing`) | 599 / 0 |
| `f3_snowflake_single` | delete `snowflake.py:126` (`SINGLE_TOKENS = ...`, read on the next line) | subset (snowflake, lineage, optimizer, dialect) | 267 | 22 / **263** | 1087 / 0 |
| `f3_merge_maxcopy` | delete `merge_subqueries.py:26` (`MAX_COPY_FACTOR`, read by defaults) | subset (executor, optimizer, parser, lineage, transpile) | 86 | 140 / **9** | 150 / 0 |

Every probe is a base miss (300 s each) that this branch selects.

**Build.** 1-job timings: probe 2 s, in-process pass 156 s, forked pass 284 s, test-import
probe 7 s, postprocess 7 s: 456 s. The base at 4 jobs runs its single pass in about 60 s
(143-156 s at 1 job, so about 2.5x). Estimate at 4 jobs: 60 + 115 + 15 = **about 190 s**
(budget about 240 s). The forked pass costs about 1.9x the in-process one, mostly from
re-running `setUpClass` (executor TPC-H/TPC-DS loading, optimizer duckdb setup) in every child.
`--isolate fork` alone (about 130 s at 4 jobs) loses the state-dependent widening and the
base-precision registry rule; `--isolate none` is the base. Maps 6.6 MB (same as base),
world-readable. Run CPU unchanged (0.72 s wall for 30 commits; the selector does one extra
code-object comparison per changed file).

**Pseudo lines** in the maps (for siblings): line `0` of a file = tests that read its source
text; line `-1` of `sqlglot/__init__.py` = tests that start processes (today only
`test_lazy_load`). `modules.json["importers"]` = module -> users + tests whose file imports
it (crash users).

## Dead ends and measurements that did not pan out

- Preimporting all sqlglot modules in the parent before forking: children are cheaper, but
  no test then runs `Dialect._try_load` (lost the H1 gain there). Off (`BR_IMPACT_PREIMPORT`).
- Fork without skipping import-time execution: every test that lazily imports a dialect
  becomes a user of every class its import touches (sample `ktz3` 18 -> 1347 tests, +25 s).
- Fork-only registry rule: the same blow-up through `EXPR_CLASSES` (`_build_dispatch`).
- Static test-file import graph alone for crash users: misses `from sqlglot.dialects import
  Snowflake` (lazy `__getattr__`), e.g. all of `test_dialect.py` (58 misses on
  `f3_snowflake_single`); the dynamic per-test-module import probe fixes it.
- Merging importers into `module_users` everywhere: 606 -> 1103 tests on module-level edits;
  kept only for import-crash paths.
- Collection-time caches: only 5 non-import function lines run during collection: negligible.
- Class key collision found in the base (not fixed, conservative): dialects without their own
  Tokenizer get a dynamic `type("Tokenizer", ...)` whose key `sqlglot.dialects.dialect:Tokenizer`
  is shared by all of them, so its instances are merged; when the build's subclass walk sees
  one (nondeterministic between builds), `Hive.Tokenizer` users grow by about 800 tests.

## For sibling branches (impact-relprof, impact-attrs)

- Isolation is switchable: `BR_IMPACT_ISOLATE=none|fork|both` (env) or `build/impact.py
  --isolate`. A plugin that records its own per-test profile can reuse the fork machinery in
  `br_cov_plugin.py` (`pytest_runtest_protocol`, `_child_records`, `_record`): add your data to
  what the child pickles. Keep `_skip_imports` on, or import-time work pollutes per-test data.
- Profiles recorded in one process have the same H1 hole: a value profile at a compare site
  inside `_build_dispatch`/`_try_load` sees 15/26 tests, not 1348/808.
- `crash_users` and the line-0/-1 pseudo lines are independent of the rest and cheap.

## PR body

```
## Hypothesis
Coverage recorded in one long-lived pytest process misses tests that only consume state an
earlier test computed (per-class caches, lazily filled registries). Several base rules also
return fewer tests than "all users" on wrong arguments (source-text readers, import crashes,
process launches). Each is a 300 s miss class.

## Method (mechanism, not tuning)
- Coverage pass with every test in its own fork of the post-collection process (setUpClass
  included), import-time execution not attributed (the probe maps it), unioned with the
  in-process pass. Functions whose lines run only in polluted state are widened to their
  testers and by-name callers. The coarse registry rule stays on the in-process pass.
- Audit hook: tests that read sqlglot source text (line 0) or start processes (line -1).
- Import-crash users: class-body bindings read later at class creation, and function lines
  run at import, select the module's users plus every test whose file imports it (static
  graph + per-test-module import probe in forks).
- Whole-function semantics (generator flag, new local shadowing a global).

## Results
| set | simulated s | misses | run CPU s | vs baseline |
| sample (local) | 176.882 | 0 | 0.72 | -0.9 s vs 177.811 |
| held-out m1 | not available yet | | | |
Probe mutants: 5 hand-made mutants that the base misses (31, 780, 1, 263, 9 failing tests
left out); this branch selects every failing test in all 5.

## Per-commit changes (sample)
ktz3 3.70 -> 3.08 s, gbav 2.54 -> 2.22 s, rszg +0.01 s; all others identical.

## Rule check (why this is not a patch->tests table)
All maps come from running the pinned commit's suite (coverage in two process layouts,
source-file opens, process launches, test-module imports). The selector reasons from where
an edit sits in the AST and what the edit does to bindings and code objects. Nothing is
keyed by patch content or edit kind, and no labelled answers are read.

## Risks and what would break it
Build time (about 190 s estimated at 4 jobs vs about 70 s for the base); a forked child that
deadlocks is killed after 600 s and its test is always selected. The pristine state assumes
the labelling run collected the whole suite in each process. Import crashes from causes other
than a removed binding are not detected.

## Submission candidate? (yes/no, expected practice score and why)
Promising, not a candidate: no held-out numbers yet and no docker run. Expected practice
score about the base's (sample -0.9 s); the value is the closed miss classes.
```
