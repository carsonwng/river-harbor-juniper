# strat/r2-tables: per-key reads of Dialect/Generator/Parser/Tokenizer class tables

Branch `strat/r2-tables`, cut from `origin/strat/runcpu2` (`f89594d`, practice submission 8).
Impact layer only: `blast/impact.py`, `build/impact.py`, `build/impact_lib/br_cov_plugin.py`,
`build/impact_lib/br_state.py`, new `build/impact_lib/br_tables.py`.

## Question (from the round-2 brief)

After tpcds, the next most expensive cost classes on practice-like data are class-level edits in
`dialects/`, `generators/`, `parsers/` and `tokens.py`. The base selects "every user of the class"
for them, because the bound name (`TRANSFORMS`, `FUNCTIONS`, `TIME_MAPPING`, `KEYWORDS`, flags) is
read at import by the `_Dialect` metaclass or by `_TokenizerBase.__init_subclass__`. That set
includes `test_generator_memory` (#1224, 6.7 s), which instantiates every dialect's generator.

Measured target: these are commits whose every edited line sits in a class-level
assignment of a family file. The numbers are runcpu2 image selections.

| set | commits | their cost | set total |
| --- | --- | --- | --- |
| syn640 | 148 | 1270.8 s | 5578.3 s |
| m1 | 12 | 111.2 s | 420.9 s |
| m1-extra | 4 | 31.4 s | 203.1 s |
| local-20261120 | 5 | 66.7 s | 326.4 s |
| sample | 4 | 24.9 s | 123.0 s |

## Hypothesis

An edit confined to class-level assignments changes the program only through the values import
binds. The import-time state diff (`br_state.py`, from strat/impact-attrs) gives every changed
(class, attribute, dict key) after all propagation, including subclass merges, tries, the metaclass,
`__init_subclass__` and reverse maps. A test can only observe such a change by reading that
attribute. For a dict, it must look up that key, or read the key set or the whole table. Those reads
can be recorded per test in the **existing coverage pass**, with no extra suite pass, as gap-expr
does for Expr classes.

## Mechanism

### Build: `br_tables.py`, loaded by `br_cov_plugin.py` in the coverage pass

* After `import sqlglot`, every plain data value in the own `__dict__` of every class of the
  families (subclasses of `Dialect`, `Generator`, `Parser` and `_TokenizerBase`) is replaced by a
  non-data descriptor. Plain values are dict, set, frozenset, list, tuple, scalars and enum members.
  The descriptor records that this test obtained `<class>.<attr>`, once per phase through a
  generation stamp. Reads through subclasses and instances hit the defining class's descriptor, so
  `home` is the class whose value is used.
* Every exact `dict` value is also replaced by `_TD`, a dict subclass holding the same items. Per
  test, it records:
  * keys looked up, hit or miss: `[]`, `get`, `in`, `pop`, `setdefault`, set, del;
  * key-set reads: `iter`, `len`, `bool`, `keys`, `reversed`;
  * whole-value reads: `values`, `items`, `copy`, `==`, `repr`, `|`, pickling and deepcopy.

  Because `__iter__` is overridden, CPython's dict-merge fast path is off, so `dict(td)`,
  `{**td}`, `d.update(td)` and `f(**td)` go through `keys()` plus `[]` and are recorded. `plain | td`
  and `plain == td` reach the subclass's reflected method first. Pickle, copy and deepcopy return a
  plain dict after recording a whole read.

  These paths were checked one by one on Python 3.13: `dict()`, `{**}`, `update`, `|` both ways,
  `==`, json, pickle, copy, deepcopy, list, sorted items, len, bool, `**`, `set()`, `keys() - x`,
  `fromkeys`, `str`, f-string, `%`, `format_map`, ChainMap, OrderedDict, Counter, max, tuple and
  `values()`.
* A dict also held anywhere other than its class slots keeps its plain object and gets
  attribute-level recording only. The check is the refcount, calibrated on a probe class. An
  alias in two slots shares one `_TD`. 19 of 1273 dicts stay plain once every dialect is loaded,
  for example the `AFTER_HAVING_MODIFIER_TRANSFORMS` module-level dicts.
* Classes created later get the same treatment through hooks: lazy dialect imports, and the
  `type("Tokenizer", ...)` classes made by `_Dialect.__new__`. The hooks are `_Dialect.__new__`,
  `_TokenizerBase.__init_subclass__`, and an added `__init_subclass__` on `Parser` and
  `Generator`. A new class is installed at the next flush, which happens at the end of a sqlglot
  module execution or at a phase boundary, once its creation namespace is gone.
* **Reads during a sqlglot module execution are not recorded.** Module execution is tracked with
  a depth counter around `_LoaderBasics.exec_module`. What import derives from a table is covered by
  the run-time state diff instead. Without this, `test_generator_memory`, which lazily imports
  every dialect, would read every merged parent table in bulk. Views and iterators taken during an
  import and still alive at the end mark that table unsafe.
* **Generator dispatch cache:** `_build_dispatch(cls)` (`dict(cls.TRANSFORMS)`, cached in
  `_DISPATCH_CACHE` across tests) is built with recording off. It returns a `_Dispatch` dict, and
  every lookup in it records the same key of the `TRANSFORMS` table it was built from (or an
  attribute-level read when that table is plain). So a later test that dispatches `exp.Foo`
  through the cache is a reader of `TRANSFORMS[exp.Foo]`, even though the cache was built by an
  earlier test.
* Key names are portable (`br_state.portable_key`) and equal keys share a name: `True` matches
  `1`, a TokenType (IntEnum) matches its int, and a str-enum matches its str. A class key whose
  metaclass is not `type` (a `_Dialect` class compares equal to strings) gets `?`, and so does
  any key without a stable name. A `?` lookup counts as a reader of every key of that table.
* Unsafe entries:
  * a descriptor no longer in place, for example after `mock.patch.object` or a test assigning
    a class attribute;
  * a plain attribute added at run time;
  * a table whose content a test left changed. Each phase compares the content against a copy
    taken before the first test-time mutation.
* `build/impact.py` merges the workers into `tables.json`, which holds attribute readers, per-key
  readers, key-set readers, whole readers, tracked entries, per-key tables and unsafe entries. A
  table that is plain in any worker is attribute-level only.
* `br_state.canonical_classes` names a class whose `module:qualname` is not unique (the
  dynamic Tokenizers) after the uniquely named class whose attribute holds it, for example
  `sqlglot.dialects.dialect:Tokenizer@sqlglot.dialects.athena:Athena.tokenizer_class`. This
  does not depend on which modules are loaded, so the plugin and the diff agree. The snapshot
  stores portable key names, and the diff reports key-level detail
  (`[class, attr, value|add|del|order, key]`).

### Run: `blast/impact.py`

* The trigger is the same as gap-expr: the base selection is non-empty, every edited line is a
  class-level assignment in a tracked class, and the edit is not lambda-only. gap-mix's lambda
  coverage is used for lambda-only edits.
* The state diff imports **the whole commit**: every edited sqlglot file is overridden
  (`--rel -`), not only the file at hand.
* For each changed `(class, attr)`:
  * an Expr entry uses gap-expr's readers;
  * a family entry uses `_table_readers`. With key detail, that is the readers of each changed,
    added or removed key, plus `?` lookups, plus whole readers, plus key-set readers when keys
    were added, removed or reordered. Without detail, it is every reader of the attribute.
  * Anything else keeps the base selection: module globals, new or removed attributes or
    classes, untracked or unsafe entries, volatile entries, a failed diff.
* An import failure selects every test. Otherwise the result is **intersected with the base
  selection**: both are supersets of the tests that can fail. `maps.always`, the tests the profile
  does not know, still run.
* The import check (gap-mix/runcpu2) is skipped when the commit-wide diff already imported every
  module with these sources and found the pin's failures. This was ported from gap-expr e26d85f.
* The diff runs in a fork server that has done a fresh diff child's setup and its
  `import sqlglot`. When the pristine core import looked up an edited module, or on any error,
  a fresh child runs instead. `BR_STATE_SERVER=0` turns the server off.

Rule check: the maps record which tests read which class attribute or key on the pinned commit,
which is a finer coverage. Nothing in them is about patches, edit kinds or outcomes. The selector
finds the changed entries by importing the edited code.

## Blind spots (documented, by design or accepted)

* **C fast paths:** dict subclass paths are covered as listed above. Concrete-API calls on a dict
  passed in as an argument (`PyDict_GetItem` or `PyDict_Next` in C code) bypass recording. sqlglot
  calls `str.maketrans` only on module-level tables, and a grep finds no `dict.get(td, ...)`-style
  unbound calls. Sets, lists and tuples are attribute-level only. Set C operations with the
  set as an argument (`a.union(s)`, `set(s)`, `{*s}`, `a |= s`, `dict.keys() - s`) read the
  table directly, so per-element tracking of sets would be unsound.
* **`cls.__dict__[...]`** returns the descriptor. sqlglot only does this in `_Dialect.__new__` and
  `_TokenizerBase.__init_subclass__` on the class being created, before it is installed, or for
  membership checks.
* **Cross-test state:** one cache was found and handled, the generator dispatch cache. Data a test
  leaves in the global `sqlglot.schema` for later tests, and instance copies made at import, are
  not handled. Instance copies made at import are module or class state, so the diff
  fingerprints them.
* **Import order:** the diff imports in one fixed order (all modules, sorted). An effect visible
  only under another order would be missed. The skipped import check uses that same order.
* **Pickling:** a table pickled or deep-copied is recorded as a whole read, and the copy is a plain
  dict.
* **Spurious diff changes:** the memoised fingerprints of cyclic structures depend on which path
  reaches them first. A real change elsewhere can therefore flip the fingerprint of an unrelated
  function, for example `RisingWaveGenerator.datatype_sql` on a tableau edit. This falls back to
  the base selection, so it is safe; it affected about 6 of 163 diffs on syn640.
* Reads outside a test phase are discarded, as in the coverage plugin.

## Results

(filled in below)

## Verification

(filled in below)

## Costs

(filled in below)
