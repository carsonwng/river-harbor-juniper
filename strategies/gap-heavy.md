# strat/gap-heavy: why heavy tests get selected, and an instruction-coverage gate

Branch `strat/gap-heavy`, cut from `origin/int/night-2120` (main + relprof). Only the
impact layer changes: `blast/impact.py`, `build/impact.py`,
`build/impact_lib/br_instr_plugin.py`.

## Question

A commit costs at least its slowest selected test plus 2 s. Practice cost 1.37x the
sample. Hypothesis: heavy tests get selected on many commits where they cannot fail.

## 1. Measurement: selection vs failure (selector `int/night-1948`)

Tests over 1 s (tests.json): #1490 `test_max_nodes` 28.8, #1106 `test_execute_tpcds` 21.5,
#1224 `test_generator_memory` 6.7, #1107 `test_execute_tpch` 3.9, #1123
`test_optimized_tpch` 3.5, #1428 `test_scope` 2.5, #1440 `test_tpcds` 2.2, #1295
`test_annotate_funcs` 2.0, #1382 `test_qualify_columns` 1.4, #1082 `test_agg_order` 1.4,
#1361 `test_optimize_in_nulls` 1.3, #1541 `test_cache_does_not_retain_queries` 1.3,
#1343 `test_merge_subqueries` 1.0, #1313 `test_canonicalize` 1.0.

Selected / failing, per set. syn = 400 unlabelled patches from `validation/generate.py`
(seed 4242; selection needs no labels).

| test | sample (30) | m1 (28) | syn (400) selected |
| --- | --- | --- | --- |
| #1490 max_nodes | 0 / 0 | 0 / 0 | 26 |
| #1106 execute_tpcds | 4 / 2 | 7 / 2 | 75 |
| #1224 generator_memory | 1 / 0 | 4 / 1 | 64 |
| #1107 execute_tpch | 1 / 1 | 4 / 1 | 40 |
| #1123 optimized_tpch | 0 / 0 | 4 / 1 | 45 |
| #1428 scope | 1 / 0 | 2 / 1 | 48 |
| #1440 tpcds | 3 / 2 | 5 / 3 | 56 |

The upper bound if every selected but non-failing test over 20 s were dropped (repacked
with `blast/pack.py`): sample 177.8 -> 139.6 s, m1 273.0 -> 177.2 s. On syn, assuming
none fail, 3381 -> 2151 s. So the hypothesis holds: tpcds alone is about a third of the
cost.

**Which rule selects them** (unnecessary selections; script instruments `_change_bits`'s
producers: line coverage, `import_bits`, class users, `_name_bits`, all-tests):

- #1490 on syn (26): class-level names that propagate ("read at import time", so all class
  users) 11, mostly `arg_types` entries and strings in `expressions/*.py`, whose class users
  include every parser test through the registry rule; the all-tests fallback 11
  (`tokens.py`, `trie.py`, `typing/__init__.py`, `optimizer/__init__.py`, `__init__.py`,
  `__main__.py`); coverage of the edited statement 4 (`errors.py`, `tokenizer_core.py`).
- #1106 on syn (75): coverage of the edited statement 28 (optimizer, executor, planner);
  propagating class-level names 12; fixture readers (triage: `tpc-ds.sql` and
  `tpc-h.sql`, both loaded in `TestExecutor.setUpClass`) 8; all-tests 6; import-time
  function lines (`import_bits`) 3.
- #1224 on syn (64): propagating class-level names 46. Generator class constants
  (`TRANSFORMS`, `TYPE_MAPPING` entries) and the test builds every dialect's generator.
- m1: #1106 fixture 2, coverage 3, names 1; #1224 names 3.

The dialect angle does not pay: of the 75 tpcds selections only 3 come from
dialect-specific files (`duckdb.py`, which tpcds does use, `starrocks`, `typing/hive`).

After `int/night-2120` (relprof), for the selections that remain on syn
(`blast/impact.py` internals, `why.py`):

| | function-body expression edit, evaluated by the test | function-body edit, **never evaluated** | class/module-level edit | fixture/test file |
| --- | --- | --- | --- | --- |
| #1490 (28) | 8 | 2 | 17 | 1 |
| #1106 (77) | 35 | 8 | 25 | 9 |

## 2. Hypothesis tested (H-instr)

**Claim.** Line coverage is per statement. A profiled test that ran the statement but
never evaluated the edited *expression* cannot see an edit that only replaces
expressions. Until the old expression is first evaluated, the new program runs the same
instructions as the old one, and it is never evaluated. This holds whichever rule
selected the test: coverage, class users, names or import lines.

**Build** (`build/impact.py` `run_instr`, `br_instr_plugin.py`). It profiles every test
that took 1.5 s or more in the coverage pass (junit times; `BR_INSTR_MIN_SECONDS`). Each
runs alone in a fresh pytest process. That process collects the whole suite, as the
labelling run does, and deselects the rest. `sys.monitoring` INSTRUCTION events go on
every sqlglot *function* code object, through PY_START and `set_local_events`, with
DISABLE after the first hit. Imports, collection, setUpClass and in-process pools are all
recorded. Module and class bodies are not, because the gate never prunes import-time
code. `instr.json` stores the source positions (line, col, end line, end col) of the
instructions that ran, per test and file: 2.4 MB, 56k distinct spans. A profile counts
only if exactly that test ran and passed, and it never read sqlglot `.py` source outside
the import system (an audit hook on `open`). The budget is 150 s (`BR_INSTR_BUDGET`). A
test that has not finished by then has no profile and is always kept.

**Run** (`blast/impact.py` `_instr_refine`). This runs after the base bits and relprof,
per changed file, and only when a profiled test is in the bits.
1. Statement pairing (`_differing`). The minimal differing node pairs (`_min_diff`) must
   all be expressions.
2. Each old expression `x` and its replacement `y` must meet every condition below, or
   nothing is dropped:
   - no walrus, `yield`, `yield from`, `await` or `__debug__` in either;
   - it lies in a function or lambda *body* (defaults and decorators of a module or
     class level def run at import);
   - it is not a docstring and not inside a `match` pattern;
   - a constant does not reach a conditional jump through and/or/not/if-else. CPython
     compiles those into control flow without an instruction of their own.
3. Every `x` must overlap at least one instruction of the compiled old file. Otherwise it
   has no instruction of its own (dead code, folded), and nothing is dropped.
4. A profiled test is dropped if no executed instruction of it overlaps any `x`. It is
   kept if line coverage says it ran the statement but its profile shows no instruction
   of that statement (an inconsistent profile).

**Rule check.** The map records which instructions of the pinned commit ran under each
profiled test. It is line coverage at a finer grain. It contains no patch, no edit kind
and no outcome. The selector compares the patch's AST change with those positions.

## 3. Results

| set | before (`int/night-2120`) | after | misses |
| --- | --- | --- | --- |
| sample, local (docker-built maps + instr map) | 131.728 s sim | 130.322 s sim (132.565 total) | 0 |
| m1, local | 273.0 s sim + 1 miss | 273.0 s sim + 1 miss | 1, `8owm`, present before (hive.py `* -> +`, 806 failing) |
| syn 400, selection only | 3280.4 s | 3119.8 s (-160.6, -4.9%) | unlabelled |
| sample, docker (`516de02`) | 184.5 s sim with the same image's maps and no instr map | **183.132 s sim, 186.959 total** (3.8 s CPU) | 0 |

In the docker build the relprof pass ran past its 420 s limit on the loaded host (coverage
took 218 s instead of about 60 s), so the image has no relprof map. The baseline for that
row is the same image's maps with `instr.json` removed, re-run locally: 184.5 s, which
gives -1.4 s from the gate, as in the local run. That build took 832 s. Since then the instr
pass also stops at `BR_IMPACT_DEADLINE` (450 s into the impact step, default), after relprof.
On this host it would have been skipped. On an unloaded 4-core build (coverage about 60 s,
relprof about 125 s) it has about 250 s left.

On top of `strat/gap-recall` `cd65792` (scratch merge; only `build/impact.py` conflicted,
both passes kept), local, docker-built maps plus the instr map: sample 130.322 s sim,
0 misses; **m1 296.670 s sim, 0 misses**. The recall import check closes `8owm`, and the
gate's decisions are unchanged.

The synthetic set is `validation/generate.py --sqlglot <pinned> -n 400 --seed 4242`,
regenerated in a private scratch dir. An earlier copy in the shared job tmp dir had been
contaminated by another agent's patches, and no number here comes from it.

Selections on syn, before -> after: #1490 26->24, #1106 71->65, #1224 75->68, #1107 39->34,
#1123 43->38, #1428 46->40, #1440 54->51. Sample: `bj7g` drops #1382 (3.6 -> 2.2 s).

**Soundness evidence.** The dropped tests were run on the edited sqlglot (fresh copy of
the pinned tree, patch applied, `pytest -p no:xdist`, `SKIP_INTEGRATION=0`, docker image
of `int/night-2120`). That is 14 synthetic commits and 69 dropped (commit, test) pairs.
They include `test_execute_tpcds` 6x, `test_execute_tpch` 5x, `test_optimized_tpch` 5x,
`test_generator_memory` 7x, `test_max_nodes` 2x and `test_scope` 6x: **all 69 pass**.
After the constant-folding rule was refined, two more commits (d1ju, hkau: 4 drops of
`test_annotate_funcs`, `test_qualify_columns`, `test_canonicalize`) also pass. That makes
73 of 73.
Positive control: `test_execute_tpcds` on sample `2jrq` (labelled failing) fails in the
same harness (5 failed subtests). Commits: 1c08, 6bqd (`typing/__init__.py` lambda),
9d0u, kg58 (`qualify_tables.py`), dwjr (`generators/python.py` string), ghjn, ghue,
gmqg, piab, psxa, psxk (`executor/env.py`), pw9e, surq, tdz2 (`scope.py` drop not).

## Why the gain is small, and where the rest is

Most of the remaining heavy selections are either real evaluations, where the test runs
the edited expression (35 of 77 for tpcds, and often a real failure), or **class-level
edits**. Class-level edits need an attribute-read profile plus an import-state diff;
that is `strat/impact-attrs`, already in `strat/impact-combo`. This gate does not touch
those. Fixture selections of tpcds come from `TestExecutor.setUpClass` loading both TPC
fixtures. `_extract_meta` can raise on any `#` line, so a tpc-h edit can break tpcds;
that is not provable without evaluating the loader on the new text (triage layer).

## Risks

- State: the profile is a fresh process with the whole suite collected. The labelling
  run executes other tests first. Pure caches are safe: an entry another test computed by
  evaluating `x` would be computed the same way by the fresh test. Global state that
  tests mutate (the dialect registry after `Dialect._classes.clear()`) can send a test
  down another path. The base line coverage map has the same hole (see
  `strat/impact-isolate`). The consistency check (step 4) catches some cases.
- Introspection: a profiled test reading source or code objects of sqlglot would see a
  never-evaluated edit. Source reads disable the profile. `co_code` comparison is not
  guarded (no profiled test does it; `test_typing` is not profiled).
- Build time: about 100-220 s for 14 tests at 2 CPUs on a loaded host; it should be under
  the 150 s budget at 4 jobs. Everything else is dropped first; tests past the deadline
  are only unprofiled.

## Dead ends

- Global INSTRUCTION events (all code): about 45 s per test, mostly sqlglot module
  bodies (each DISABLE re-instruments the code object) and post-processing. Limiting
  events to function code objects through PY_START: 0.3 s import overhead.
- The first profile under tracemalloc made `test_generator_memory` fail (recording
  allocates). With function-only events it passes, and failing profiles are unusable
  anyway.
- `strat/impact-combo` on Windows: the attrs state diff reports spurious `__orig_bases__`
  changes, so local combo numbers are not comparable (Linux only).

## For others

- Adopt the instruction map for any per-test question below statement level. With all
  tests profiled in-process instead of the 14 heaviest, it would refine every coverage
  selection.
- `int/night-2120` misses m1 `8owm`. Hand it to whoever owns import-crash rules
  (`strat/impact-isolate` F3).
