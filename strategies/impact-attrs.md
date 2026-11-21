# strat/impact-attrs: import-time state diff + per-test attribute/table-key read profile (H3)

Branch `strat/impact-attrs`, cut from `strat/impact-base` (v1b + pack + triage).
All numbers are **local** (no Docker in this session; "CPU" is the harness's wall-time stand-in).

## Hypothesis

Import-time code (class bodies, metaclasses, `__init_subclass__`, module tables) has no
per-test line coverage, so the base handles an edit to a class-level statement by *name*
(tests that ran code loading the name, intersected with the class's users; "propagates" means
all class users). That over-selects badly for constants and table entries: `535z`
(`TSQL.WEEK_OFFSET`) 171 tests incl. the 6.7 s #1224, `4d63` (`Snowflake.Tokenizer.NUMERIC_ESCAPES["u"]`)
288 tests, `ktz3` (`TriggerProperties.arg_types["constraint"]`) 52, `gbav`
(`SingleStoreParser.SHOW_PARSERS["GROUPS FOR USER"]`) 35. A value bound at import can only change
a test's outcome through (a) what import itself derives from it and (b) run-time reads of the
value or of what was derived. Both can be measured exactly:

* (a) **at run time, by importing the edited sqlglot** and diffing the whole import-time state
  against pristine fingerprints made at build time. This follows every propagation for free
  (merged subclass tables, tries, `required_args`, reverse maps, metaclass products), and an
  import that now fails is detected.
* (b) **at build time, by a second instrumented suite pass** that records, per test, every read
  of a class attribute with the class that supplied the value, and every key looked up in every
  class-level dict.

## Method (mechanism, not tuning)

### Build (`build/impact_lib/br_attr_build.py`, called from `build/impact.py`)
1. `br_state.py build_state`: import every sqlglot module (sorted order, `PYTHONHASHSEED=0`)
   and fingerprint every attribute of every sqlglot class (canonical names
   `module:qualname`, or `...@path` for dynamically created classes such as the
   `type("Tokenizer", ...)` ones made by `_Dialect`), every entry of class-level dicts, and every
   module global. Fingerprints are structural hashes (code objects without positions, closure
   cells, defaults, containers, instance state; expression nodes by type+args+comments).
   It runs 6 times (also through the text-loading path the run-time diff uses); entries whose
   container *order* differs between processes (built by iterating sets of classes, e.g.
   `EXPRESSION_METADATA`, `_CANONICALIZE_TYPES`) are fingerprinted order-free, and any entry
   still not reproducible is "volatile" (a change there is never trusted). Result: 47
   order-free entries, 0 volatile.
2. Tracked names: every class-dict name not bound to a `def` of the same name and not a
   `__slots__` member (1167 names, e.g. `WEEK_OFFSET`, `arg_types`, `required_args`, `key`).
3. Profile pass (`br_attr_plugin.py`, `br_attr_instr.py`, `br_attr_rt.py`): pytest with an
   import hook that compiles every module under `sqlglot/` and `tests/` from a rewritten AST
   (positions kept; `--assert=plain`):
   `obj.NAME` (tracked) -> `_br_rd(obj, "NAME")`, `obj.__dict__`, `getattr(...)` (any name,
   computed ones too), `vars(...)`, `dict(x)`, `o.NAME op= v`; doctest examples are compiled
   through the same rewriter. `_br_rd` records `(owner, NAME)` where owner is the class in the
   MRO whose `__dict__` supplied the value (an instance attribute shadowing it records nothing,
   a data descriptor wins as in Python; `super()` handled). After import, every class-level dict
   is replaced by `TD`, a dict subclass recording per test: keys looked up (getitem, get, in,
   setdefault, pop, hit or miss), bulk value reads (items, values, ==, repr, pickling, deepcopy,
   popitem) and key-set reads (iter, keys, len). Overriding `__iter__` takes CPython's merge fast
   path off (`{**d}`, `dict(d)`, `update`), so copies are recorded. `dict(td)`, `td.copy()`,
   `td | x` return a TD that records lookups against its source tables, so the generator's
   cached dispatch dict (`dict(cls.TRANSFORMS)`) attributes each `TRANSFORMS` lookup to the test
   making it. Aliases of a dict in other class/module namespaces get the same TD; a gc scan
   checks that nothing else still holds an original dict (0 leaks). Phases are attributed like
   the coverage plugin (setup/teardown -> test class, collection -> test module, outside -> all
   tests). Process pools are replaced in-process as in the coverage plugin.
4. Cross-test caches: before every phase the plugin empties the pure memo caches
   (`sqlglot.generator._DISPATCH_CACHE`, and the memo dicts of the global `sqlglot.schema`).
   The whole import-time state is fingerprinted at session start and end; every run-time change
   must be on a short list of benign kinds (loggers, PEP 562 lazily cached module attributes of
   `sqlglot.dialects`/`sqlglot.optimizer`, the `_Dialect._classes` registry, `sqlglot.schema`
   rebinding, `__slotnames__`) or the profile is marked unusable and the selector uses the base.
   Shared expression nodes stored as class attributes get new parents when used; the
   fingerprint ignores tree position, so that is not a change.
5. `compileall` of the pinned sqlglot into `attrs/pyc` (`PYTHONPYCACHEPREFIX`), so the run-time
   import uses bytecode on the read-only disk (0.17 s instead of 0.6 s).

### Run (`blast/impact.py`)
The base computes its bits first. Then, if every edited line of the (single) file is
* in a class-level `Assign`/`AnnAssign`/`AugAssign` (not in a function, lambda body, class
  header, or under if/for in a class body) -> **"assign"**,
* else in other import-time code (module level, other class-level statements) -> **"import"**,

the edited file text is piped to `python build/impact_lib/br_state.py diff`, which loads that one
module from the text (meta-path loader), imports everything, fingerprints and diffs.
* Some module no longer imports -> **every test** (found by a probe: the base would have
  missed 21 collection errors; applies to "import" edits too).
* "assign": each change `(class, attr, key)` (a dict entry's value) selects the tests that looked
  that key up in that table (or a copy of it) or read the table in bulk; `(class, attr)` (any
  other change) selects the tests that read that attribute from that class (+ all ops on it when
  it is a table); `(class, "*")` readers (`vars`, `__dict__`) are always added, as are tests the
  profile did not see pass. Any change to a module global, a new/removed class or attribute, a
  dunder, an enum/namedtuple/abc/other-metaclass class ("opaque": their attributes are read by
  uninstrumented machinery) or an untracked name -> the base result.
* Anything unexpected (subprocess failure, volatile entry, profile unusable) -> the base result.

## Results (local)

| set | simulated s | misses | run CPU s (wall) | vs base |
| --- | --- | --- | --- | --- |
| sample (base `strat/impact-base`) | 177.811 | 0 | 0.74 | - |
| sample (this branch) | 155.957 | 0 | 3.3 | -21.9 s simulated, -19.3 s total |
| held-out m1 | not available yet (`validation/m1` has the generator only) | | | |

Per-commit changes on the sample (everything else is identical to the base):

| commit | edit | base sel / cost | this sel / cost | truly failing |
| --- | --- | --- | --- | --- |
| `535z` | `TSQL.WEEK_OFFSET = -1` -> `-2` | 171 / 11.238 s | 3 / 2.671 s | 1 (selected) |
| `4d63` | `Snowflake.Tokenizer.NUMERIC_ESCAPES["u"]` | 288 / 9.122 s | 0 / 0 | 0 |
| `ktz3` | `TriggerProperties.arg_types["constraint"]` | 52 / 3.701 s | 3 / 2.078 s | 0 |
| `gbav` | `SingleStoreParser.SHOW_PARSERS["GROUPS FOR USER"]` value | 35 / 2.542 s | 0 / 0 | 0 |

The diff reports, for these four: `[TSQL.WEEK_OFFSET]`, `[Snowflake.Tokenizer.NUMERIC_ESCAPES key "u"]`,
`[TriggerProperties.arg_types key "constraint", TriggerProperties.required_args]` (the
`__init_subclass__` derivation is found without being modelled), `[SingleStoreParser.SHOW_PARSERS
key]` (the `SHOW_TRIE` built from the keys is unchanged, so not reported). `4d63`: Snowflake
tests look up only `'`, `\\` and `n` after a backslash in `NUMERIC_ESCAPES`; no test tokenizes a
`\u` escape in Snowflake.

Run CPU: each diff costs about 0.55 s (startup 0.03, state JSON 0.03, import 0.17, canonical
names 0.06, fingerprints 0.2), so 4 diffs on the sample add about 2.5 s.

### Soundness probes (mechanical)
`.scratch/probe.py` (untracked) picks class-level constant assignments and constant dict-entry
values with a seeded RNG over 21-30 files (dialects, generators, parsers, tokenizer, expression
classes) and mutates them (`int+1`, `not bool`, `str+"x"`); three tokenizer probes were added by
hand. For each, the base-selected tests run on the mutated copy (one worker,
`SKIP_INTEGRATION=0`) and every failing test must be in this branch's selection.

| probe | file | edit (old -> new) | base sel | this sel | failing (of base sel) | missed |
| --- | --- | --- | --- | --- | --- | --- |
| 1-p00 | `dialects/tsql.py:114` | `"21": "%Y-%m-%d %H:%M:%S.%f",` (`"%Y-%m-%d %H:%M:%S.%f"` -> `'%Y-%m-%d %H:%M:%S.%fx'`) | 171 | 0 | 0 | 0 |
| 1-p01 | `generators/tsql.py:203` | `exp.DType.DECIMAL: "NUMERIC",` (`"NUMERIC"` -> `'NUMERICx'`) | 153 | 4 | 4 | 0 |
| 1-p02 | `expressions/query.py:2013` | `arg_types = {"kind": True, "sample": True}` (`True` -> `False`) | 16 | 1 | 0 | 0 |
| 1-p03 | `expressions/query.py:2008` | `"update_options": False,` (`False` -> `True`) | 17 | 2 | 2 | 0 |
| 1-p04 | `expressions/query.py:2069` | `arg_types = {"this": False, "with_count": True}` (`True` -> `False`) | 18 | 3 | 0 | 0 |
| 1-p05 | `expressions/query.py:441` | `arg_types = {"this": True, "scope": False, "update": False}` (`False` -> `True`) | 16 | 1 | 0 | 0 |
| 1-p06 | `expressions/query.py:647` | `"grouping_sets": False,` (`False` -> `True`) | 123 | 1620 | 0 + 21 test modules fail to import | 0 |
| 1-p07 | `generator.py:435` | `UNNEST_WITH_ORDINALITY = True` (`True` -> `False`) | 1363 | 50 | 11 | 0 |
| 1-p08 | `dialects/tsql.py:81` | `"YY": "%y",` (`"%y"` -> `'%yx'`) | 171 | 7 | 0 | 0 |
| 1-p09 | `parser.py:1978` | `ALTER_TABLE_MIXED_ACTIONS: t.ClassVar = False` (`False` -> `True`) | 1522 | 33 | 1 | 0 |
| 2-p00 | `dialects/dialect.py:445` | `ALIAS_POST_TABLESAMPLE = False` (`False` -> `True`) | 1589 | 755 | 5 | 0 |
| 2-p01 | `expressions/query.py:1005` | `"system_time": False,` (`False` -> `True`) | 1071 | 1620 | 0 + 33 test modules fail to import | 0 |
| 2-p02 | `generator.py:382` | `MATCH_CONDITION_IN_ON = False` (`False` -> `True`) | 1363 | 1 | 1 | 0 |
| 2-p03 | `expressions/query.py:1006` | `"version": False,` (`False` -> `True`) | 1071 | 1620 | 0 + 33 test modules fail to import | 0 |
| 2-p04 | `expressions/query.py:1841` | `"unpivot": False,` (`False` -> `True`) | 68 | 54 | 3 | 0 |
| 2-p05 | `expressions/temporal.py:207` | `arg_types = {"this": True, "expression": True, "unit": True,` (`False` -> `True`) | 1416 | 5 | 5 | 0 |
| 2-p06 | `generators/bigquery.py:567` | `exp.DType.BLOB: "BYTES",` (`"BYTES"` -> `'BYTESx'`) | 241 | 1 | 1 | 0 |
| 2-p07 | `dialects/bigquery.py:38` | `SUPPORTS_STRUCT_STAR_EXPANSION = True` (`True` -> `False`) | 319 | 39 | 8 | 0 |
| 2-p08 | `dialects/hive.py:68` | `"H": "%-H",` (`"%-H"` -> `'%-Hx'`) | 355 | 20 | 5 | 0 |
| 2-p09 | `expressions/query.py:707` | `"expressions": False,` (`False` -> `True`) | 1082 | 1620 | 0 + 30 test modules fail to import | 0 |
| 2-p10 | `dialects/snowflake.py:111` | `"EPOCH_SECONDS": "EPOCH_SECOND",` (`"EPOCH_SECOND"` -> `'EPOCH_SECONDx'`) | 373 | 2 | 0 | 0 |
| 2-p11 | `expressions/functions.py:490` | `arg_types = {"this": True, "stddev": True, "gen": True}` (`True` -> `False`) | 1416 | 2 | 0 | 0 |
| 2-p12 | `tokens.py:207` | `NESTED_COMMENTS = False` (`NESTED_COMMENTS = True` -> `NESTED_COMMENTS = False`) | 1574 | 1202 | 3 | 0 |
| 2-p13 | `dialects/clickhouse.py:86` | `NUMERIC_ESCAPES = {"x": (16, 2, 3, 0xFF)}` (`{"x": (16, 2, 2, 0xFF)}` -> `{"x": (16, 2, 3, 0xFF)}`) | 108 | 0 | 0 | 0 |
| 2-p14 | `dialects/postgres.py:87` | `"x": (16, 1, 3, 0xFF),` (`"x": (16, 1, 2, 0xFF)` -> `"x": (16, 1, 3, 0xFF)`) | 222 | 1 | 0 | 0 |

**25 probes, 0 misses.** Selections shrink from a median of 319 base tests to 7. Four probes
(`arg_types` entries turned required: `1-p06`, `2-p01`, `2-p03`, `2-p09`) make
`sqlglot.dialects.*` fail to import (a dialect class body builds an expression that now fails
validation), so 21 to 33 test modules fail at collection: **the base selection (123 to 1082
tests) would miss 333 to 844 failing tests on each of them**; this branch selects every test
there (import-failure rule). Note that "failing" counts only the base-selected tests, which is
what the probe can check; a test outside the base selection that fails would be a base miss too.


## Build time and size
* coverage pass (base): 144 s at 1 job; profile pass: 222 s at 1 job (1.5x the plain suite);
  state 8 s; tracked names 1 s; pyc 1 s. Estimated at 4 jobs: base 71 s + about 80 s.
* maps: impact total 21 MB (base 6.7 MB): `attrs/pyc` 5.9 MB, `attrs/state.json` 3.4 MB,
  `attrs/attrs.json` 2.4 MB, `attrs/sets.json` 1.2 MB (own sparse bitsets, so the base
  `sets.json` is unchanged).

## Soundness notes (traps from the brief)
* **Cross-test caches**: the only run-time caches of class data found are `_DISPATCH_CACHE`
  (emptied per phase; the dispatch dict is a TD copy, so per-key lookups survive) and the global
  schema's memo dicts (emptied). The start/end state diff checks there is nothing else. Not
  covered: data a test leaves in the global schema for a later test (order dependence that
  would also make the pristine suite flaky).
* **Tokenizer**: no `sqlglotc` in `requirements.lock`; `tokenizer_core.py` is pure Python and
  `TokenizerCore` keeps references to the class dicts, so per-key lookups are recorded.
* **`arg_types`**: `__init_subclass__` derives `required_args` at import; the diff reports both,
  and run-time readers of each are recorded wherever they read (validation, generators,
  `simplify`, parsers).
* **`self.dialect.X`, `cls.X`, `type(self).X`, properties, copies in `__init__`**: all go
  through `_br_rd` (a copy made in `__init__` is a read at that time; instances are not shared
  across tests except via the class-level fixtures attributed to the whole class).
* **Reads invisible to the profile**: C code (none found for class-level tables except
  `str.maketrans`, used only on module-level tables), `inspect.getmembers`/stdlib code reading
  class attributes (enum, namedtuple, abc -> those classes are "opaque"), code compiled at run
  time other than doctests (the executor's generated Python reads only its env).
* **Side effects of class-level calls** outside sqlglot state (e.g. a class body calling
  something that changes `sys` or `warnings`): not fingerprinted. Class-level assignments in
  sqlglot call only pure factories (`_show_parser`, `rename_func`, `build_*`).
* **Import order**: the diff imports in one fixed order; an effect visible only under another
  order would be missed.
* Determinism: hash seed fixed to 0 in build, profile and diff.

## Dead ends / notes
* First fingerprints used `hash((type, value))` for ints: `hash(-1) == hash(-2)` hid `535z`.
  Numbers are fingerprinted by their text now.
* gc-found classes not reachable by attribute paths are garbage from `_Dialect.__new__`
  (default `type("Tokenizer", ...)` arguments); `gc.collect()` first.
* Sharing one pass with `strat/impact-relprof`: both are AST import hooks with positions kept;
  `br_attr_instr._Loader.source_to_code` can apply several `NodeTransformer`s in turn, and the
  coverage plugin's `sys.monitoring` LINE events work on the rewritten code (same positions), so
  coverage + attribute profile + relation profile could be one suite pass.

## PR body

## Hypothesis
Edits confined to import-time code (class-level constants and table entries, the bulk of the
non-function source mutants) change behaviour only through the values import binds. The full
import-time effect can be computed exactly at run time by importing the edited sqlglot and
diffing all class/module state against pristine fingerprints; the tests that can observe each
changed (class, attribute[, dict key]) are known from a build-time per-test read profile.

## Method (mechanism, not tuning)
- Build: pristine import-time fingerprints (`br_state.py`), an AST-instrumented second suite pass
  recording per test the (owner class, attribute) of every class-attribute read and every key
  looked up in every class-level dict (dict subclass), with memo caches emptied per phase and a
  start/end state check that no other cross-test cache exists; bytecode for the run-time import.
- Run: for an edit confined to class-level assignments, a subprocess imports the edited sqlglot
  and reports what changed; each change selects its recorded readers. An edit confined to any
  import-time code whose edited sqlglot no longer imports selects every test. Anything the
  profile cannot vouch for (module globals, new attributes, enums/abc/namedtuple, dunders,
  volatile entries, failures) falls back to the base result.

## Results
| set | simulated s | misses | run CPU s | vs baseline |
| --- | --- | --- | --- | --- |
| sample (local) | 155.957 | 0 | 3.3 (wall) | -21.9 s simulated vs 177.811 (base), -19.3 s total |
| held-out m1 | n/a (not published yet) | | | |
| 25 mechanical probes | n/a | 0 | | base misses 4 of them (import failures) |

## Per-commit changes (sample)
`535z` 171 -> 3 tests (11.24 -> 2.67 s, the 1 failing test kept), `4d63` 288 -> 0 (9.12 -> 0),
`ktz3` 52 -> 3 (3.70 -> 2.08), `gbav` 35 -> 0 (2.54 -> 0). Others unchanged.

## Rule check (why this is not a patch->tests table)
Nothing is keyed by patch or edit kind. The selector executes the patched code's import and
compares the resulting program state with fingerprints of the pinned commit; the tests come from
a read profile of the pinned commit's suite (which test reads which attribute / table key), the
same kind of map as line coverage. No labels or answers are read.

## Risks and what would break it
- Reads invisible to the instrumentation (C code reading class tables, stdlib reflection,
  code compiled at run time) -> enums/abc/namedtuple fall back; none known for sqlglot tables.
- Cross-test state the start/end check does not see, or data a test leaves in the global schema.
- Side effects of class-level calls outside sqlglot state; import-order dependent effects.
- Run CPU: about 0.55 s per eligible commit (sample: 4).
- Maps grow from 6.7 MB to 21 MB (pyc 5.9 MB, fingerprints 3.4 MB); build +80 s at 4 jobs (est.).

## Submission candidate? (yes/no, expected practice score and why)
Promising, not a candidate yet: no held-out numbers and no docker run. The import-failure rule
alone fixes a base miss class (4 of 25 probes) and should be adopted regardless. Expected practice
gain vs the base: about 20 s per 30 commits from class-level constants/tables, plus avoided
300 s misses on `arg_types` mutants that break import.

