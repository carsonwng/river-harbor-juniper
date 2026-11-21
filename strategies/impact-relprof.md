# strat/impact-relprof: relation profiles of comparison sites (H2)

Branch `strat/impact-relprof`, cut from `strat/impact-base` (cloud-base-1 + strat/impact v1b).
All numbers are **local** (host Python 3.13, no Docker in this session).

## Hypothesis

An edit that only changes the operator of a single-operator comparison (`is`/`==`,
`is not`/`!=`, `<`/`<=`, `>`/`>=`, ...) can change a test run only at an evaluation of that
comparison where the old and the new operator give different results. Until that first
divergent evaluation the patched program executes exactly like the pinned one. So if, over a
whole suite run of the pinned commit, *every* evaluation of the site gives the same result
under both operators, no evaluation can be the first divergence, the patched run is the
pinned run, and no test can change (given determinism; see Risks).

Second, same argument: a comparison with a **literal** on one side (`x == "abc"`, `n > 2`,
`kind in ("A", "B")`) whose literal is changed. If every value the non-literal side ever took
at that site gives the same result against the old and the new literal, the edit is inert.

## Method (mechanism, not tuning)

### Build (`build/impact.py` `run_relprof` + `relprof_post`, `build/impact_lib/br_relprof_plugin.py`)
- A second suite pass, separate from the coverage pass (coverage is unchanged), on a throwaway
  copy of the checkout. An import hook compiles every `sqlglot.*` module from its pinned
  source with each `Compare` that has one operator in {is, is not, ==, !=, <, <=, >, >=, in,
  not in} rewritten (AST level, original line/column kept, source file untouched) into
  `_br_rp_<op>(site, left, right)`. Call arguments are evaluated left to right like the
  comparison's operands, so evaluation order is unchanged; the helper evaluates the original
  operator once, returns its result unchanged and re-raises its exception unchanged.
- Per evaluation it records a 16-bit **relation signature**: for each of the 8 operators a
  2-bit code false / true / raises / unknown. Operators other than the original one are only
  evaluated when that is provably side-effect free:
  - both operands builtin value types (None, bool, int, float, complex, str, bytes, or a
    subclass overriding no rich comparison, e.g. `str`/`int` enums), or tuples/frozensets of
    those (depth <= 3, <= 64 items): all 8 computed (cached by (types, values); identity always
    computed per evaluation, so interning is recorded as it really happened);
  - object-default comparison types (all six rich comparisons inherited from `object`, e.g.
    `executor.Table`, enum members without mixins, classes as operands with a plain
    metaclass): the result depends only on identity and types, computed and cached;
  - sqlglot `Expression` (its `__eq__` is `self is other or (type(self) is type(other) and
    hash(self) == hash(other))`, `__ne__` is `not __eq__`; checked against the pinned source
    text at plugin start): `==`/`!=` inferred without calling anything when the operands are
    identical or of different types; otherwise unknown (hashing has a cache side effect);
  - anything else (custom `__eq__` such as `Dialect`/`_Dialect`, which can lazy-load
    dialects): unknown, except the original operator's own result and identity.
  A raising evaluation records "all unknown".
- Literal sites also record the distinct values of the non-literal side (exact `str`, `int`,
  `float`, `bool`, `None`, tuples of those; at most 256 per site, otherwise the site is
  "opaque"; any other type makes it opaque). `in`/`not in` sites are only recorded when the
  right side is a literal container.
- Attribution per test (index*4+phase, as `br_cov_plugin.py`: setup/teardown -> whole class,
  collection -> whole module), with evaluations outside any test phase kept as well, so
  nothing evaluated is dropped. Process pools are replaced in-process like the coverage
  plugin. Tests that start a child process (`subprocess.Popen`, `multiprocessing` start)
  inside a test are listed "unsure" (their child runs uninstrumented); a child started outside
  any test from sqlglot or test code disables the whole profile (`complete: false`);
  pytest/xdist's own spawns (worker start, `platform.uname`) are ignored. Tests that fail in
  the profile pass, or whose outcome differs from the coverage pass, are "unsure".
- `relprof.json` (0.7 MB): per site `rel:line:col` -> [sorted signature union over the whole
  pass, index of the set of tests that evaluated it]; `values`: per literal site the value
  list or null; `unsure`; `complete`.

### Run (`blast/impact.py` `_relprof_refine`)
After the base computes its coverage selection for a changed file:
1. Pair statements of old and new file (`_differing`: equal source text => equal AST; descend
   into compound statements whose blocks line up), collect the `Compare` nodes of differing
   parts, and require that each differing part is exactly the old part with only some
   single-operator comparisons changed (operator, or one literal operand facing a
   non-literal). Anything else -> base behaviour.
2. Per edited site: operator-only change -> every recorded signature must give the same known
   result (true/false) under old and new operator. Literal change -> every recorded value must
   give the same `bool` under the old comparison with the old literal and the new comparison
   with the new literal (identity against a literal only for None/True/False/Ellipsis).
   A site not in the profile (module never imported), an opaque site, `in` <-> `not in`, or
   any disagreement -> base behaviour for the whole file.
3. Consistency with the coverage pass: if the comparison is evaluated whenever its statement
   runs (`_unconditional`: not behind a BoolOp tail, IfExp branch, lambda, comprehension, ...),
   every test the coverage map says ran the statement must have evaluated the site in the
   profile pass, else base behaviour.
4. If all sites are proven inert, the change keeps only base-selected tests that are "unsure"
   in the profile, plus tests the map does not know. On the sample that is nothing.

The decision is a **global** one (all evaluations in the suite), not per test. Per-test
pruning is unsound here: a cached function (lru_cache, lazily built tables) evaluated with a
divergent value in test A hands the divergent result to test B, which may also have its own,
non-divergent evaluations of the site; B would be pruned wrongly. The global union does not
have this problem, because whoever evaluates the cached call, it is evaluated with the same
operands.

## Results (local)

| set | simulated s | misses | run CPU s (wall) | vs base |
| --- | --- | --- | --- | --- |
| sample (30) | **123.726** | 0 | 0.77 | 177.811 -> 123.726 (-54.1 s, -30%) |
| held-out m1 | not available yet | | | |

Oracle floor 94.351 s. Selector CPU stays at the base level (0.74 -> 0.77 s wall for 30
commits) after localising the AST comparison to differing statements.

## Per-commit changes (sample)

| commit | edit | base (s, tests) | relprof (s, tests) | truly failing |
| --- | --- | --- | --- | --- |
| 2haz | `normalization_strategy is NormalizationStrategy.UPPERCASE` -> `==` (`Dialect`) | 25.454, 86 | 0, 0 | 0 |
| 66ks | `self._table is None` -> `== None` (executor `Context.table`) | 24.787, 51 | 0, 0 | 0 |
| 2qn5 | `self.dialect.version < (23, 12)` -> `<=` (ClickHouse) | 2.536, 6 | 0, 0 | 0 |
| fryt | `len(args) == 2` -> `!=` (Dremio) | unchanged (diverges, kept) | | 0 |
| 8y7j | `scale == MICROS` -> `!=` (ClickHouse) | base already selects nothing | | 0 |

Profiles behind them: 2haz site saw only (enum member, same enum member) and (different
member) pairs, so `is` and `==` agree; 66ks saw `Table is None` false and `None is None` true,
identity-only types; 2qn5 saw only `(version tuple) > (23, 12)`, never equal.

## Soundness probes (independent of labelled data)

`probe.py` (scratch, not committed) applies one edit to a copy of pinned sqlglot and runs the
**base-selected** tests with one worker, `SKIP_INTEGRATION=0`. Sites were picked by a seeded
random sample (`pick.py`, `pickv.py`), not by hand, plus the three sample commits.

Operator swaps the profile calls inert (all base-selected tests pass):

| site | swap | base tests | relprof | pytest |
| --- | --- | --- | --- | --- |
| dialect.py:1280 (2haz) | is -> == | 86 | 0 | 86 passed |
| executor/context.py:53 (66ks) | is -> == | 51 | 0 | 51 passed |
| generators/clickhouse.py:811 (2qn5) | < -> <= | 6 | 0 | 6 passed |
| generator.py:4946 | -> == | 28 | 0 | 28 passed |
| executor/table.py:34 | -> is | 60 | 0 | 60 passed |
| jsonpath.py:128 | -> == | 17 | 0 | 17 passed |
| generator.py:3104 | -> != | 51 | 0 | 51 passed |
| parsers/postgres.py:333 | -> is | 2 | 0 | 2 passed |
| parser.py:2129 | -> is | 1436 | 0 | 1436 passed |
| tokenizer_core.py:832, parser.py:8554, parsers/clickhouse.py:72, generators/postgres.py:331 | `== "lit"` -> `is "lit"` | 1577 / 1 / 150 / 39 | kept (v0 had a SyntaxWarning guard) | all passed, 1 SyntaxWarning each |

The last row is why the SyntaxWarning guard was dropped: `x is <literal>` only adds a compile
time warning, and the pinned suite has no warning filters or pytest config.

Swaps the profile calls divergent (kept): core.py:884 (-> is not), parser.py:72 (-> is not),
dialects/snowflake.py:122 (-> !=), optimizer/simplify.py:1729 (-> is), parser.py:10435 (-> !=):
all have failing tests, every one inside the kept (= base) selection.

Literal changes the values call inert (all base-selected tests pass): dialect.py:2066 (1),
generators/presto.py:537 ('EPOCH_MICROSECOND_x'), executor/env.py:138 (1), dialect.py:1719
(1), transforms.py:981 (2), generators/duckdb.py:4532 (2); qualify_columns.py:738 and
schema.py:789 had an empty base selection already. Literal changes called not inert:
parsers/spark2.py:153, parsers/hive.py:171, generators/snowflake.py:1174,
parsers/bigquery.py:802: all have failing tests, all kept.

**0 unsound verdicts in 31 probes.**

## Coverage of the mechanism (selection only, no labels)

- 300 random natural operator swaps over all profiled sites (seed 3): 97 proven inert (32%),
  195 divergent, 6 base-empty; packed cost 3405 s -> 2286 s (-33%).
- 200 random literal changes at value sites in the style of the mutant generator (+-1,
  appended/dropped character; seed 5): 17 proven (8.5%), cost 1935 s -> 1846 s (-5%). Most
  literal sites do see the old literal, so changing it really diverges.
- The coverage-consistency check (step 3) never fired on operator sites: the evaluating-test
  sets match statement coverage exactly (e.g. 66ks: 51 covering, 51 evaluating). It caught
  the `in` sites before they had per-test records (fixed in v2).

## Determinism checks

- Second profile pass with `PYTHONHASHSEED=12345` (first: random): identical signature sets
  and identical evaluating-test sets at all 1948 sites.
- Reversed test order (`BR_RELPROF_REVERSE=1`, seed 777): the suite itself is order dependent
  (4 failures in reverse order: `test_dialect::test_enum` KeyError, StarRocks subtests).
  5 sites get different signature sets, 48 sites different evaluating-test sets (mostly the
  failed tests cut short, and lazy dialect loading at `dialect.py:232`), 3 value sites differ
  (`id()` values compared in `diff.py:217`, `simplify.py:230`). **None of 3896 operator-swap
  decisions and 1418 literal decisions changes** between the forward profile and the union of
  forward and reverse. A build could union two orders for robustness (+~2 min at 4 jobs); the
  current build runs one.
- Threads: only the inline pool thread; recording is a GIL-atomic set add. No `__del__` in
  sqlglot. Time/date: sites comparing current time would show both outcomes in the profile and
  so be divergent; none were special-cased.

## Build time, map size, run CPU

- 1 job: coverage pass 143 s, relprof pass 275-295 s (about 2x the coverage pass). At 4 jobs
  (coverage 60 s in the base build) the relprof pass is estimated at about 125 s, so the whole
  impact build goes from about 71 s to about 200 s wall. Under the 4 min budget, not by much.
- `relprof.json` 0.70 MB (base impact maps 6.7 MB). World-readable via the existing chmod.
- Run CPU: 0.77 s wall for the sample (base 0.74 s).

## Risks and what would break it

- **Determinism / environment**: the proof is about the pinned run in *our* build. The labelling
  environment's test order, xdist split and hash seed may differ. Evidence above says decisions
  are stable under seed and order changes, but a site evaluated only in order-dependent code
  could in principle see different values in the labeller.
- **Profile completeness**: evaluations in child processes are invisible (tests that spawn are
  kept). Code run through `exec` of generated Python (executor) is not instrumented, but such
  code has no sqlglot comparison sites of its own.
- **Literal identity**: `x is <new literal>` is only reasoned about for None/True/False/Ellipsis.
- **Introspection**: anything reading the edited code object or its source (column positions in
  tracebacks, `inspect.getsource`) would see the change; nothing in the suite asserts on that.
- **SyntaxWarning**: `x is "lit"` emits a compile-time warning; harmless unless the labeller
  ran with `-W error` (README says pytest with `SKIP_INTEGRATION=0` only).
- The `Expression.__eq__` inference is pinned to its exact source text; if that check fails the
  plugin treats Expression comparisons as unknown (safe).

## Rule check (why this is not a patch->tests table)

The build never sees a patch, never mutates sqlglot, and never records which edits are safe or
which tests fail. It records, for the pinned commit, a value profile: for each comparison site,
which relations held between the operands each time the suite evaluated it (and, for literal
sites, which values reached it). This is a strict generalisation of line coverage (coverage
records *that* a line ran; this records *with which operand relations*). At run time the
selector derives, for whatever operator or literal the patch introduces, whether any recorded
evaluation would have come out differently, by evaluating the new operator on the recorded
relation codes or the new literal on the recorded values. There is no key "kind of edit" in the
map and no test outcome in it.

How close to the line it is, honestly: the 8-operator signature is precomputed for operators
other than the one in the source, i.e. the map anticipates that *some* operator might be
substituted. That is still a property of the operands (the full relation between two values),
not of an edit, and the map holds no mutant and no answer, but a reviewer could argue that
"relation of operands under all 8 operators" is designed with operator mutations in mind. The
literal-value records are a plain value profile. Humans should decide; I believe both are on
the "maps built from the pinned commit" side.

## Dead ends / not done

- `and` <-> `or`: needs the truthiness of the second operand when the old code short-circuits
  it, i.e. an extra evaluation of arbitrary code (not side-effect free in general). Not done.
- `not` insertion/removal, `in` <-> `not in`: never equivalent when evaluated; nothing to prove.
- Per-test pruning (keep only tests whose own evaluations diverge): unsound with cross-test
  caches (see Method), not used.
- Chained comparisons: not instrumented (fall back).

## What other strategies should adopt

- The import-hook AST rewrite (`_Finder`/`_Loader` in `br_relprof_plugin.py`) is a cheap,
  general way to get value-level profiles (overhead about 2x a coverage pass); `strat/impact-attrs`
  could record attribute reads the same way.
- The localised statement diff (`_differing`) is a fast, sound way to find the changed AST parts.
- From `strat/impact-isolate`: if its isolated coverage changes which tests "cover" a site, the
  consistency check here should use the same coverage; the profile itself needs no isolation
  because the decision is global.

## PR body

```
## Hypothesis
A comparison whose operator or literal operand changed can only alter a test run at an
evaluation where the old and new comparison disagree. If no evaluation in a whole suite run
of the pinned commit disagrees, the patched run equals the pinned run.

## Method (mechanism, not tuning)
Second build-time suite pass with every single-operator comparison in sqlglot rewritten at
import into a recording helper (original operator evaluated once, result/exception unchanged).
Per evaluation: a relation signature (each of 8 operators: false/true/raises/unknown, only
computed when provably pure) and, at literal sites, the other operand's value. The selector
proves an operator swap or literal change inert when every recorded evaluation agrees, and
cross-checks the site's evaluating tests against statement coverage. Else: base behaviour.

## Results
| set | simulated s | misses | run CPU s | vs baseline |
| sample (local) | 123.726 | 0 | 0.77 | 177.811 -> 123.726 (-30%) |
| held-out m1 | not available yet | | | |

## Per-commit changes (sample)
2haz 25.454 -> 0, 66ks 24.787 -> 0, 2qn5 2.536 -> 0 (all break nothing). fryt/8y7j unchanged.

## Rule check (why this is not a patch->tests table)
The map is a value profile of the pinned commit (operand relations and values per comparison
site), a generalisation of coverage; no patch, mutant, edit kind or test outcome is in it. The
selector evaluates the patch's new operator/literal on the recorded relations. Closest point to
the line: signatures are precomputed for all 8 operators (a property of the operands).

## Risks and what would break it
Labeller determinism (order/seed checked: 0 of 5314 decisions change), child processes
(spawning tests kept), SyntaxWarning under -W error (not used by the suite).

## Submission candidate? (yes/no, expected practice score and why)
Promising, not candidate until held-out numbers exist. Expected practice gain: operator swaps
are about a quarter of code mutants and a third of them are proven inert in simulation.
```
