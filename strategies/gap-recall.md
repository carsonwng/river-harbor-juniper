# strat/gap-recall: adversarial miss hunting (recall first)

Branch `strat/gap-recall`, cut from `origin/int/night-2120` (base v1b + pack + triage + relprof).
Impact layer only (`blast/impact.py`, `blast/impact_import.py`, `build/impact.py`).

## Question

One miss costs 300 s. Which kinds of edit make the selector leave out a failing test, how often
do they occur, and what is a sound fix?

## Miss class 1: an edit that makes some module fail to import (confirmed)

**Evidence.** Held-out m1 has two: `8owm` (`dialects/hive.py` class body, a TypeError at import;
1161 failing, base selects 355) and `doay` (`parsers/risingwave.py` class-level set, a TypeError at
import; 17 failing: the tests that load *every* dialect, such as `test_enum`, `test_generator_memory`
and `test_qualify_columns`; base selects 13 and misses 4).

**Why the base misses it.** Coverage attributes no import-time line to a test. For a class- or
module-level edit, the base picks the scope's users or the tests that read the bound names. That is
right only if the module still imports. When it does not, every test that imports it fails,
including tests that load it lazily (`Dialect.get`, the dialects package's `__getattr__`), and
every module that imports it fails too (hive -> spark2 -> spark -> databricks). The same happens
for a function that runs at import (`_build_coerces_to` in `optimizer/annotate_types.py`, whose
table `dialects/bigquery.py` indexes at class creation) and for defaults or decorators.

**The combo is affected too.** `strat/impact-combo` at `185cd2b` has an import-failure rule inside
`_diff_bits`, but it returns before the check when the attrs profile is unusable
(`if use_profile and (profile is None ...): return None`). In the docker image
`br-strat-impact-combo`, the build deadline skipped the attrs and coverage-fork passes
(`meta.json` passes: `attrs: skipped (expected 140s, 97s left)`), so every class-level
assignment edit skips the import check. m1 `doay` is a miss there (13 selected, 4 missed).
The rule also only covers edits made entirely of import-time code, so a function body that runs at
import (`rkd9` below) is not checked.

**Fix (this branch).** `blast/impact.py` `_touches_import`: an edited line (old or new) is
import-time code when it is at module or class level, in the header (decorators, signature,
defaults) of a top-level function or method, or in the body of a function the import probe saw run
during some import (`import` lines of the coverage map). For each such commit, one fresh
interpreter (`python -I -B blast/impact_import.py`) serves the edited files from their new text
(compiled directly, never from a stale .pyc), then imports `sqlglot`, the edited modules, and every
module under `sqlglot/` in sorted order. If a module that imports on the pinned commit
(`importable.json`, written by the build; computed at run time if it is missing) no longer imports,
or the check times out or cannot run, **every test** is selected. The build also byte-compiles
`/opt/sqlglot`, so the run-time check reads .pyc on the read-only disk.

Why every test, not the importers: which tests import a module is not in the base maps
(import-time lines are not attributed), lazy loaders reach it from tests that never name it
(`doay`), and the failure spreads to subclass modules. An import failure is rare (see the
frequency below), so precision there is worth little.

## Results

(filled in below as runs finish)
