# strat/triage: parse + triage layer

## Hypothesis

Edits under `tests/` can be answered exactly from the pinned test suite without
running anything. Python edits that leave the AST unchanged cannot change behaviour.
Both cases cost the impact layer nothing.

## Mechanism

Build step `build/triage.py` (stdlib only, ~15 s, needs no tests.json):

- Enumerates every test id statically (`file::Class::method`, following base classes
  across modules). That gives 1621 ids, all 1620 tests.json ids plus the skipped one.
- For each test it collects every string the test can reach: its body, the unittest
  lifecycle methods and class bodies along the MRO, module-level calls, and every
  function or method it calls in `tests/`. Constant call arguments are substituted
  into f-string parameters, so `self.check_file("simplify")` becomes
  `"optimizer/simplify.sql"`. Module constants such as `DIR_TPCH` are resolved too.
- A string names a fixture (any non-.py file under `tests/`) when it equals one of
  the fixture's path suffixes, with or without the extension, or names one of its
  directories. A bare word that is also some fixture's name (`"optimizer"`) names the
  directory only when the code lists directories (`listdir`/`glob`/`os.walk`...). The
  fixture root (`FIXTURES_DIR`) on its own names nothing unless a listing is involved.
  F-strings that cannot be resolved become glob patterns. Only path-like ones are
  kept: no whitespace, and a fixture extension or fixture directory in the literal part.
- Writes `triage.json` (fixture -> test ids, patterns, fallback modules, test id ->
  defining function and line span, the transitive importer graph of `tests/`) and
  copies `tests/**/*.py` and `sqlglot/**/*.py` to `src/` so the runtime can rebuild
  post-patch files.

Runtime `blast/triage.py`:

| change | decision |
| --- | --- |
| fixture modified or deleted | the tests that name it (+ unknown tests.json ids in the modules that name it) |
| fixture added | tests whose path patterns or listed directories cover it, else every module that reads fixtures |
| fixture renamed | old readers + new as added |
| test .py modified | rebuild the file from `src/` and the hunks (a mismatch selects the whole file). AST diff per function: only `test*` functions changed means those tests (including subclasses elsewhere that inherit them). A new `test*` method means tests.json ids with that name in the module or its importers. A helper, setUp, class body, import or module code changed, or the file does not parse, means the whole file plus every module that imports it. Same AST means the tests whose span holds a touched line (line 1-2 touched means the whole file) |
| test .py deleted or renamed | whole file + importers |
| new conftest.py / `__init__.py` under tests/ | every test under that directory |
| other .py (sqlglot/...) | **skip** if the post-patch AST equals the pinned AST, docstrings included, and lines 1-2 are not touched. Otherwise defer |
| anything else | defer |

No maps means defer everything, the safe default.

## Measurements (local, sample)

- `eval -v`: **785.2 s total, 0 misses** (impact layer still the default: every test on
  any deferred file). Main baseline is 1210.6 s.
- Every fixture-only commit (8) selects exactly 1 test, the failing one: precision
  1.0, 0 misses. Cost is about 2.0 to 3.0 s each, against 40.3 s before.
- The 3 comment commits skip and cost 0. In the multi commits, the comment-only files
  `rszg sqlglot/optimizer/scope.py` and `986c sqlglot/typing/snowflake.py` skip too,
  so if impact improves, those commits only need impact for their real code file.
- Runtime CPU for triage is negligible (<0.5 s for 30 commits, mostly JSON load).

## Validation beyond the sample

- **Dynamic ground truth for the fixture map.** A pytest plugin with an audit hook on
  `open` ran the full suite (`SKIP_INTEGRATION=0`, requirements.lock venv) and recorded
  which test opened which file under `tests/` (setup/call/teardown/collection). For
  every fixture, the static readers are a superset of the dynamic readers: 0 missing.
  The static map over-selects only where it should: tpc-h/tpc-ds `.sql` also map to
  all 60 TestExecutor tests through `setUpClass`, and two `test_sqlglotc` tests pick up
  the optimizer dir.
- **apply_hunks**: the rebuilt post-patch text equals `git apply` output for every .py
  in the 30 sample patches.
- **Synthetic test-module edits** (git diff in a scratch copy): a test body edit selects
  1 test. A `check_file` helper edit selects all 167 test_optimizer tests. A
  `tests/helpers.py` edit selects its 1377 importers. A new test method selects
  nothing. An added import selects the whole file. A syntax error selects the whole
  file. A deleted fixture selects its reader. A new tpc-h csv selects TestExecutor.
- **Comment injection**: comments were inserted at ~6 random same-AST places in every
  sqlglot module and the full suite ran (see the result below).

## Risks / dead ends

- First version matched the literal pieces of f-strings (`"optimizer/"`) and the
  `FIXTURES_DIR` root as directory references, so 24 tests read every fixture. Fixed by
  skipping f-string pieces and treating the root as a reference only with a listing.
- `expr.walk()` was taken for `os.walk` (directory listing). Now only `os.walk` counts.
- Same-AST skip assumes no test depends on sqlglot source line numbers or source text.
  grep finds no `inspect.getsource`, `linecache` or `f_lineno` in sqlglot, and
  `test_typing` reads source only through `ast.parse`.
- New, unreferenced fixtures fall back to all fixture-reading modules (~417 tests),
  which is expensive but rare.
