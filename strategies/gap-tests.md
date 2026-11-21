# strat/gap-tests: triage on fixture, test-file and multi-file commits

Branch `strat/gap-tests`, cut from `origin/int/night-2120`. Triage layer only
(`blast/triage.py`, `build/triage.py`, new `build/triage_records.py`).

## Where the triage cost actually is

Measured with the int/night-2120 docker maps (impact on, `BR_SQLGLOT` set):

| question | finding |
| --- | --- |
| (a) one expensive test reads a whole fixture | Every fixture except tpc-h/tpc-ds selects 1-3 tests costing 2.0-4.0 s, which is the failing test plus 2 s. That is the floor: subtests are still one test, so line attribution cannot cut it. |
| tpc-h.sql / tpc-ds.sql | **25.45 s each** (61 tests: all 60 TestExecutor tests through `setUpClass`, plus test_tpch/test_tpcds). The failing set is 1-3 tests. This is the only real slack. m1 has 3 such commits, which is 76 s of m1's 455 s non-miss cost. |
| (b) new or renamed fixture | falls back to 417 tests (34.8 s). Neither the sample nor the generator ever adds or renames a file. Left alone. |
| (c) `tests/*.py` helper edit | helpers.py selects 1377 tests (36.8 s) and test_optimizer.py 167 tests (12.6 s). Neither the sample nor the generator ever edits `tests/*.py`. Left alone. |
| (d) multi-file | The fixture part adds one cheap test. The code part (impact) dominates every m1 multi commit. |

The sample's 8 fixture commits are all small optimizer fixtures and already at the floor.

## Hypothesis (validated)

A fixture that `setUpClass` parses into a class attribute only matters to the tests
that read that attribute, and only through the record *fields* they read. In
`TestExecutor`, `test_execute_tpcds` (21.5 s) unpacks `args, sql, _` and never reads
the expected (optimized) half of each pair. 58 % of tpc-ds.sql's lines are in that half.

## Mechanism

Build (`build/triage_records.py`, static, about 1 s):
- It finds `cls.A = [list(]loader("const")[)]` in setUpClass/setUp, where the loader is a
  stdlib-only function in tests/helpers.py and the constant names exactly one fixture.
  A must be stored nowhere else and the class must not be subclassed.
- It re-runs the string analysis with that statement removed and the `self.subTest(...)`
  labels blanked, to find tests that still name the file. Those keep `use: all`. A bare
  directory string (`DIR_TPCDS`) does not count unless the code lists a directory. To
  open a file, code must add a name to the directory, and that name shows up as a
  constant, a rendered f-string pattern, or a listing.
- It follows each test through self-methods and test-suite functions, with the list
  bound to parameters (aliases), and collects:
  - `use`: the record fields read. Tuple-unpack targets that are never loaded do not
    count. `for`/comprehension/`enumerate`/`L[i]` (through `a if c else b`) are
    understood. Any other use is `all`.
  - `touch` and `mutate`: the class data attributes the test reads, and those it may
    mutate (store, subscript store, method call, or handing the attribute to a call).
  - Dynamic access (`getattr(self…)`, `__dict__`, `self` escaping, `super()`) gives
    `all` and touches everything.
- setUp/tearDown touches count for every test. A setUpClass/tearDownClass use of the
  records makes every test a user.

Runtime (`blast/triage.py::_record_narrow`):
- It rebuilds the post-patch fixture and runs the suite's own loader (pinned
  tests/helpers.py, with `open` swapped for the text) on the old and the new text.
- If either raises, setUpClass would fail, so every class test stays.
- Otherwise it compares records: changed field indices, or `all` when the count or
  shape changes.
- It selects the tests that use a changed field, then a fixpoint: any test touching an
  attribute that a selected test mutates. This is why a tpc-h `sql` edit still selects
  test_execute_tpcds, which shares `cls.cache` with the consumers.
- Readers outside the class (test_tpch, test_tpcds) are unchanged.

## Results (local, host python, int/night-2120 docker maps)

| set | before | after | misses |
| --- | --- | --- | --- |
| sample | 131.728 | 131.728 (no tpc commits) | 0 |
| m1 (42) | 1055.483 | 1034.261 (4qrg 25.45 -> 4.23) | 2 before, the same 2 after (8owm, doay: impact holes owned by others) |
| synthetic tpc set (20, `validation/sets/gap-tests-tpc`) | 509.080 | 360.526 (7 of 20 drop to 4.23) | 0 / 0 |

Runtime CPU: about +0.5 s wall over the 20 tpc commits (two loader runs per commit, about 25 ms each).

## Verification (run in the br-label image, SKIP_INTEGRATION=0)

For each of the 20 synthetic cases, all of tests/test_executor.py (60 tests, in normal
order, so the shared `cls.cache` and connections behave as in a full run) plus
test_tpch and test_tpcds were run on a patched scratch copy. Every failing test was
selected in all 20 cases (0 misses). Highlights:
- tpc-ds edits in the expected half (t000, t002, t003, t006, t008, t009): only
  test_tpcds fails, and only test_tpcds is selected. The 59 to 60 dropped TestExecutor
  tests, including test_execute_tpcds at 21.5 s, all passed.
- tpc-ds `sql` edits (t001, t007): test_execute_tpcds fails, and it is selected.
- t016 (meta `# execute: false`): nothing fails. Field 0 is selected, which is conservative.
- t017 (a `--` comment line): records are identical, so only test_tpcds is selected. Nothing fails.
- t018 (a meta line without `:`): the loader raises, so 60 errors from setUpClass plus
  test_tpcds. The guard selects all 61.
- t019 (`;` inside an expected statement): the records shift, so `all` is selected.
  test_execute_tpcds fails and is selected.
- tpc-h (t010 to t015): failing sets range from test_tpch alone up to execute_tpch,
  optimized_tpch and tpch, and all are selected.

## Dead ends / limits

- tpc-h `sql`-field edits: test_execute_tpcds is reached through `cached_execute`
  (shared `cls.cache` memo) and through the `tpch if … else` branch. Splitting it off
  would need key-collision reasoning about the memo. Not done, and the cost stays 25.45.
- Path-sensitive branch pruning gains nothing for the same reason.
- Expected value is small: tpc-* is 2 of the ~29 .sql fixtures. On a generator-like mix,
  about 1 % of commits are tpc-ds, and 58 % of those save 21.2 s.
