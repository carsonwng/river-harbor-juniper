# strat/gap-mix: practice-mix forensics and one cut

Branch `strat/gap-mix`, cut from `origin/int/night-2120` (`53eff6e`). Impact layer only.

## 1. Forensics: what practice looks like

Costs in ms are exact (SCORING.md): every deferred source edit cost 36 817 in submission 1
(`a9e62c0`, all tests on 4 workers). Write `k` for the number of such commits and `r` for the
rest (fixtures, test files and comments; triage did not change between submissions 1 and 2).

    867 471 = 36 817 k + r        244 047 = S2(k) + r   (submission 2, 1a5f961 = impact-base)

| k | r (s) | S2 (s) | S2 / k (s per source edit) | what r has to be |
| --- | --- | --- | --- | --- |
| 23 | 20.680 | 223.367 | 9.71 | about 7–9 small fixture or test edits at 2.0–3.0 s (sample: 8 fixtures = 17.9 s) |
| 22 | 57.497 | 186.550 | 8.48 | 2 TPC fixture edits (25.454 each, the TestExecutor setUpClass readers) + 3 small |
| 21 | 94.314 | 149.733 | 7.13 | 3 TPC-type edits + about 8 small |
| 20 | 131.131 | 112.916 | 5.65 | 4–5 heavy fixture or test edits: unlikely |
| ≤19 | ≥167.9 | ≤76 | ≤4.0 | needs most non-source commits to be heavy: implausible |

- The feasible range is **k = 21–23 source-edit commits** (sample: 19). With N = 30 like the
  sample, k = 22 or 23.
- The sample's source average is (177.739 − 17.9 fixtures − 0 comments) / 19 = **8.41 s**. Practice
  is 7.1–9.7 s. **Practice source edits are not unusually expensive.** The 1.37× ratio comes from
  more source commits (22–23 vs 19, ×1.16–1.21) plus either a few more expensive non-source commits
  (k = 22: +39.6 s of TPC fixture edits) or about 15% dearer source edits (k = 23).
- The held-out sets agree. Under the impact-base maps (the scorer's image `br-int-night-1948`),
  the average source-edit cost is m1 10.86 s (19 source commits) and the synthetic set 10.94 s
  (526 source commits, below). **The sample is the cheap outlier.** Expect practice and final
  to look like the generator's distribution, not the sample's.
- relprof (`int/night-2120`) **was not dropped on practice.** Its docker image has a complete
  `relprof.json` (2038 sites). It saves 46.1 s on the sample (2haz, 66ks, 2qn5 drop to 0, and
  ktz3 goes up from 3.7 s to 10.4 s), 0 on m1, and 117 s over 526 synthetic source edits (0.22 s
  each). The 2.007 s it saved on practice (244.047 → 242.04) is the synthetic rate times about
  22 edits. Its sample gain comes from the sample having several `is`/`==` swaps.

## 2. Where the cost is (impact-base maps, per commit)

Maps were copied out of the docker images (`docker cp <container>:/app/maps`), so the numbers
are the scorer's maps. Local selector, `BR_MAPS`/`BR_SQLGLOT` set. Synthetic set:
`validation/generate.py --areas -n 240 --seed 4242` produced 640 patches (unlabelled, because
cost needs no labels). Tool scripts were kept out of the repo (job scratch).

The per-commit cost is the heaviest selected test plus 2 s per worker. Five tests decide almost
everything:

| test | s | marginal cost per source edit (synthetic) |
| --- | --- | --- |
| test_executor::test_execute_tpcds | 21.454 | **2.95 s** (27% of the average) |
| test_generator::test_generator_memory | 6.686 | 0.76 s |
| test_parser::test_max_nodes | 28.817 | 0.65 s (but 27% of cost when it is the max) |
| test_execute_tpch / test_optimized_tpch | 3.9 / 3.5 | 0.02 s |

Cost by edit class (source commits only; share of the source cost):

| class | sample n / avg / share | m1 n / avg / share | synthetic n / avg / share |
| --- | --- | --- | --- |
| selects tpcds | 4 / 25.3 / 63% | 5 / 25.5 / 62% | 89 / 25.7 / 40% |
| selects max_nodes | 0 | 0 | 43 / 36.7 / 27% |
| selects genmem (not max_nodes) | 1 / 11.2 / 7% | 4 / 12.0 / 23% | 76 / ~11 / ~14% |
| function body | 11 / 11.0 / 76% | 7 / 9.2 / 31% | 229 / 11.2 / 44% |
| class level | 5 / 5.8 / 18% | 5 / 8.7 / 21% | 167 / 10.3 / 30% |
| module level | 0 | 2 / 25.5 / 25% | 34 / 18.8 / 11% |
| multi-file | 3 / 3.4 / 6% | 5 / 9.5 / 23% | 59 / 14.3 / 15% |
| optimizer | 2 / 14.0 | 3 / 17.6 | 61 / 20.0 (75% tpcds) |
| executor (+planner) | 1 / 24.8 | 1 / 25.5 | 29 / 16.2 (61–67% tpcds) |
| expressions | 1 / 3.7 | 1 / 25.5 | 34 / 16.9 (32% max_nodes) |
| dialects | 3 / 15.3 | 4 / 9.2 | 91 / 6.4 |
| generators | 5 / 1.2 | 2 / 2.4 | 94 / 5.9 |
| parsers | 5 / 3.2 | 2 / 5.6 | 92 / 4.2 |
| tokens / trie / tokenizer_core | 0 | 0 | 15 / 30.7–34.9 (max_nodes) |
| typing | 0 | 0 | 27 / 11.2 |

Ranking by expected practice cost (generator weights, consistent across m1 and synthetic):
1. **Optimizer and executor function-body edits that select tpcds** (statement coverage includes it;
   it rarely fails: 3 of 9 selections in sample + m1). No sound structural cut exists in the current
   maps. Every covered statement really runs under tpcds. Cutting it needs value-level evidence
   (relprof-style) or running it.
2. **Expressions class-level edits** (`arg_types` entries): class users of a leaf Func class are about
   1418 tests, because `EXPR_CLASSES`/`ALL_FUNCTIONS` registry readers count as users. That pulls
   in max_nodes, so 36.8 s each.
3. **Dialect/generator class-level table edits** selecting genmem (it instantiates every dialect
   and generator), at about 11–13 s instead of 2–5 s.
4. Core tokenizer/trie/tokens edits: max_nodes legitimately.

## 3. Recall findings (more important than the cut)

- **m1 miss on impact-base and int/night-2120: `8owm`.** It is a `hive.py` class-level `for` over
  `{*NUMERIC_TYPES, ...}` changed to `+NUMERIC_TYPES`. That is a TypeError at import, so 1161 tests
  fail. Impact selects the 355 Hive class users. A class- or module-level edit that raises at import
  breaks every importer. An import check (import every sqlglot module with the patched sources, in
  a child) catches it: see `impcheck` below.
- **Cross-test cache hole (verified).** Per-test coverage only credits the first test in each
  worker that fills a process-wide cache. `Generator.__init__` caches `_build_dispatch(cls)` in
  `_DISPATCH_CACHE`, so `_build_dispatch`'s body is covered by 47 tests while 1347 run the cache
  lookup. Mutant `generator.py:86 if expr_cls and ...` → `or`: impact selects 47 tests. On the
  edited sqlglot, `pytest tests/dialects/test_mysql.py` fails 15 tests (stopped at -x), and 1 of
  them is selected. That is a miss. The same hole makes `readers(TRANSFORMS)` (1056 tests) an
  under-approximation. Today it is masked only because TRANSFORMS counts as "read at import".
  Other process-wide caches: lineage `_cache`, `executor/python.py` `cache[args]`, and per-instance
  caches on long-lived objects. Fix belongs in the coverage build: reset runtime caches per test, or
  treat cache-filling lines as covering every cache reader.

## 4. The cut: import-time lambda bodies

**Claim.** If a class- or module-level statement's old and new ASTs are identical except inside
lambda bodies, the import runs the same code and binds the same values, except that those lambdas
carry new code. Creating a lambda does not evaluate its body. Until an edited lambda is called,
the patched run is the pinned run, and calling it runs its body. So the tests that can change
are those that ran the old body's lines. This holds under the same assumptions as the
function-body coverage rule. One more condition: the lambda must not run during import. A new
build probe (`build/impact_lib/br_lambda_probe.py` → `maps/impact/lambdas.json`) records which
lambdas start while every sqlglot module is imported. Without that map, the cut is off.

**Why it was expensive.** `_old_line_bits` already had a lambda rule for removed lines. The added
line fell through to `_new_line_bits` → `_name_bits(_bound(stmt))`. For metaclass-read tables
(`TRANSFORMS`, `FUNCTIONS`, `EXPRESSION_METADATA`) that is every user of the class or module. For
`sqlglot/typing/__init__.py` (core) it is every test.

**Change.** `blast/impact.py` `_lambda_only_bits`: pair module and class bodies (class headers
must match, so any function difference falls back), compare each differing statement with all
lambda bodies blanked, and take `cov(old body lines)` of each edited lambda. Otherwise the old
per-line path runs unchanged. The cut can only shrink a selection. No commit selected more tests.

**Numbers** (simulated s, local selector on docker-built maps plus the probe):

| set | before (int/night-2120) | after | commits changed |
| --- | --- | --- | --- |
| sample (30) | 131.728 | 131.351 | 1 (6ze8 31 → 5 tests) |
| m1 (28) | 573.012 (1 miss: 8owm, inherited) | 573.012 | 0 |
| m1 (42, later) | 1055.483 (2 misses: 8owm, doay, inherited) | 1044.649 | 2 (each 296 → 17, h3rw 1 → 0; both still cover their failures) |
| synthetic (640) | 6114.702 | 5959.172 (−155.5, −2.5%) | 23 |

Examples: `typing/__init__.py` Max/Min annotator lambda `is not` → `is` went from 1620 tests
(36.8 s) to 18 (25.5 s, tpcds legitimately). `parsers/starrocks.py` went from 607 tests (27.5 s)
to 20 (4.1 s). `generators/oracle.py` went from 118 tests (10.7 s) to 6 (2.5 s).

**Verification (full suite, docker labelling image, `-n 2`).** I took 8 synthetic commits the
cut narrowed, applied each to a private scratch clone, ran the whole suite in `br-label`
(`SKIP_INTEGRATION=0`), and compared the failing tests with the narrowed selection. The pinned
baseline is 1620 passed. **Every dropped test passed, and 0 failing tests were missed:**

| commit | edit | selected before → after | failing (all selected) |
| --- | --- | --- | --- |
| 1c08 | typing/__init__ Max/Min annotator `is not` → `is` | 1620 → 18 | 8 |
| ghue | parsers/starrocks `"{"` → `"{_"` | 607 → 20 | 2 |
| 333p | parsers/hive `":"` → `":s"` | 244 → 17 | 1 |
| surq | typing/presto `"this"` → `"tis"` | 233 → 4 | 0 |
| hkau | generators/mysql `0` → `1` | 317 → 180 | 3 |
| ghjn | generators/oracle `"SYSTIMESTAMP"` → lower | 118 → 6 | 2 |
| 6aq2 | parsers/spark2 `or` → `and` | 212 → 17 | 2 |
| piab | parsers/snowflake `or` → `and` | 296 → 17 | 1 |

The sample's one changed commit, 6ze8 (31 → 5 tests), still covers its labelled failure, and so
do m1's `each` (296 → 17) and `h3rw` (1 → 0, no failures).

## 5. Second commit: import check (recall)

Both inherited m1 misses are import failures. In 8owm, `hive.py` has `{*NUMERIC_TYPES, ...}` →
`{+NUMERIC_TYPES, ...}` in a class-level `for`. In doay, the risingwave parser has the same
mutation. Either way the module cannot be imported, and every importer fails (1161 and 17
failing tests). No coverage or names rule sees this. `blast/impact_import.py` runs a child
that imports every sqlglot module (sorted) with the edited files' sources swapped in through a
meta-path finder. Only edits that run at import trigger it: anything outside function and
lambda bodies, or a removed line that ran during import. A module that fails, and did not fail
at the pin (the lambda probe's `errors`), selects every test. So does a child crash or timeout.
The build now byte-compiles `/opt/sqlglot` after the suite passes (only the image's copy, never a
shared clone), so the child compiles only the edited files.

| set | before | after |
| --- | --- | --- |
| m1 (42) | 1044.649 s sim, 2 misses | **500.332 s, 0 misses** (8owm and doay → all tests, 36.8 s each) |
| sample | 131.351 | 131.351 (no sample commit fails to import) |
| CPU | | about 0.75 s per triggering commit in the Linux image (16 of 42 m1 commits trigger) |

On the synthetic set (unlabelled), **16 of 640 commits (2.5%) leave some module unable to
import.** Some are function-body edits that run at import: trie/tokens edits break duckdb's
import-time `parse_one` with a ParseError. int/night-2120 under-selects 9 of the 16: fjz1 35
tests, fpqj 33, k10s 60, 2u5m 566, tacd 771, 4yyt 1508, 56pg 1574, invg and yfjq 1564. With the
check, all 16 select every test. That is about 1% of commits being a probable 300 s miss on the
base, worth roughly 100 s per 30-commit final.
