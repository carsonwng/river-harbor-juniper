# strat/mutants: synthetic held-out data sets (validation layer)

The sample has 30 commits and the scored sets are hidden. This branch builds
labelled synthetic sets so every layer can check that it generalises:

    python <main>/harness/br.py eval --data D:/blast-radius-cwng/.cache/mutants/m1 -m "..."

Data lives in `.cache/mutants/<set>/` (not committed): `commits/<id>.patch`,
`tests.json` (copy of sample's), `answers.json` (`failing`, `kind`, and
`unknown` for failing node ids missing from tests.json). `pending/` and
`labels/` hold the generator's descriptions and raw pytest reports.

**Validation only.** Nothing here may feed a patch-to-tests table into the selector.

## Generator (`validation/generate.py`)

Edits are picked from the pinned tree (a scratch clone), never from test outcomes.

- `code`: one site in one `sqlglot/**/*.py` file. A category is drawn first
  (boolop and/or, compare ==/!=/</>/is/is not, drop `not`/`not in`, int ±1,
  string literal tweak, drop one single-line dict/set/list element,
  True/False, binary +/-), then a site; the result must compile.
- `fixture`: one line of a `tests/fixtures/**/*.sql` file: insert a token
  (`NULL`, `42`, `x`, ...), delete/swap a char, split a word, swap case;
  8% hit a `#`/`--` metadata line (e.g. `# dialect:`).
- `comment`: inserted `# ...` line at a statement start, a trailing comment,
  or a one-word docstring edit.
- `multi`: 2 or 3 distinct files, each code/comment/fixture.
- Kind weights code .56, fixture .27, comment .10, multi .07 (sample's mix).
- Patches come from `git diff` with `core.abbrev=7`, the sample's format.

## Labeller (`validation/label.py`, `validation/br_report.py`)

Organiser recipe: venv from `requirements.lock`, sqlglot on `PYTHONPATH`,
`SKIP_INTEGRATION=0`, `pytest tests`. Plugin `br_report` (controller only)
records failing node ids; a SubtestReport failure fails its parent; a
collection error fails every test of that file; xdist worker crash counts as
failure. Timeouts/crashes are rerun alone with 3x budget (machine load makes
`test_max_nodes` (29 s) and `test_execute_tpcds` (21 s) flaky otherwise).
Slowest tests are scheduled first. `PYTHONDONTWRITEBYTECODE=1` so a
same-size edit can't hide behind stale bytecode. K scratch clones label in parallel.

## Measurements

(see below, updated as runs finish)
