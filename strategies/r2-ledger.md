# strat/r2-ledger: cost ledger, then the typing-metadata cut

Branch `strat/r2-ledger`, cut from `origin/strat/runcpu2` (`f89594d` code, notes at `ab9e4d5`).

## 1. Ledger

`research/r2-ledger.md` holds the ranked opportunities, with tools and tables under
`research/r2-ledger/`. In short, practice-like commits (mean of m1, m1-extra and
local-20261120) cost 8.67 s against a 3.63 s oracle. The excess breaks down as tpcds 2.27,
class-level tables (users of the class) 1.35, non-tpcds fn-body 0.59, the tpc-h records
rule 0.53, and the core-file fallback 0.19 (all of it `typing/__init__.py`).
`tokens.py`, `trie.py`, `tokenizer_core.py` and `__main__.py` never occur in the
practice-like sets.

## 2. Cut: typing metadata tables (`sqlglot/typing/*.py`)

### Claim
`sqlglot.typing.*` modules build `EXPRESSION_METADATA` tables that map an expression class
to its annotation spec. Each dialect class copies one, and `TypeAnnotator` reads it at run
time with `.get(expr.__class__)` (annotate_types.py:543). The base rules give an edit to
these tables every user of the module. For `typing/__init__.py`, which the core import
loads, that means every test.

An edit outside every function body changes the program only through the values the
modules bind at import. If the only import-time differences are table entries, a test can
observe the edit in three ways:
- by looking up a changed key in a changed table;
- by reading a table as a whole (iteration, keys/items/values, comparison, copy into a
  plain dict, len, repr, pickling);
- by reading code objects or source (`code_readers.json`, as for lambda-only).

### Mechanism
- **Build, coverage pass** (`build/impact_lib/br_cov_plugin.py`): after `import sqlglot`,
  every `sqlglot.typing.*` module is imported. Every table binding (each module global or
  Dialect class attribute bound to a table object, which includes
  `HIVE_EXPRESSION_METADATA`) is then replaced by `_MetaDict`, a dict subclass with a
  serial number. It records `(table serial, key)` for `get`, `[]` and `in`, and records the
  key `*` for any whole-table read. Mutations also record `*` and mark the profile as having
  a problem. `copy()` returns another recording table, which is how every lazily imported
  dialect class body gets its own. At the end, serials are mapped to binding names
  (`c|module:qualname|attr`, `m|module|attr`). Tables bound nowhere (copies a test made) are
  named `?` and count for every table. The profile is dropped if any binding is still a
  plain dict, any table was mutated, or any worker lacks the record. Result:
  `maps/impact/metaread.json`, with 6139 entries and 1 whole-table reader
  (tests/test_typing.py).
- **Build, snapshot** (`build/impact_lib/br_meta.py snapshot`, 1 s): a per-entry
  fingerprint of every table, using `br_state.Fingerprinter`, so lambdas compare by code,
  constants and closures. It runs twice in separate processes. Any difference drops the map
  (the two runs were identical: 46 bindings, 0 unstable entries).
- **Run** (`blast/impact.py:_typing_meta_bits`): the trigger is a file under
  `sqlglot/typing/`, a non-empty base selection, and every removed or added line outside
  function bodies (lambdas are fine). One child, `br_meta.py diff`, imports the edited
  sqlglot. It runs the full `br_state` diff against the pristine state and requires that
  every difference is a table binding (otherwise it keeps the base selection). It then
  diffs the tables entry by entry. The selection is the code readers, plus whole-table
  readers, plus readers of each changed `(table, key)`, plus `?` readers of the key. That
  result is **intersected** with the base selection: both are supersets of the tests the
  edit can change, and the intersection keeps "never more than base". Then the relation
  profile may narrow it further, as before. If the edit breaks an import, every test is
  selected. Any failure keeps the base selection.

### Results (`br-strat-r2-ledger` maps, `--cpus 2`, simulated s; base is f89594d's image)

| set | base | new | per commit | misses |
|---|---|---|---|---|
| sample (30) | 123.0 | 123.0 | 0 | 0 -> 0 |
| m1 (64) | 420.9 | 388.1 | -0.51 | 0 -> 0 |
| m1-extra (22) | 203.1 | 203.1 | 0 | 0 -> 0 |
| local-20261120 (32) | 326.4 | 326.3 | -0.003 | 0 -> 0 |
| syn640 (640, unlabelled) | 5578.3 | 5432.0 | -0.23 | n/a |
| syntyping (40 typing-only mutants, new) | 331.3 | 132.3 | -4.98 | n/a, run-verified |

Practice-like mean: -0.17 s per commit. Every changed selection is a subset of the base's.
m1 `hpym` (drop `exp.Skewness` from a typing/__init__ group) goes from 1620 tests
(36.8 s) to 2 (3.97 s). Its oracle is 3.96 s and its failing tests are kept.

### Run-verification
`research/r2-ledger/runverify.sh`, in the image, takes a fresh copy of pinned sqlglot,
applies the patch, and runs every test the base selected and the cut dropped with
`SKIP_INTEGRATION=0`. It covers the 21 changed syntyping cases, 19 changed syn640 cases, m1
hpym and local o0d4. **All 42 cases: every dropped test passes** (`research/r2-ledger/verify.log`;
e.g. hpym 1618 dropped, 1618 passed; mwpb 1595/1595; 1wpp 1619/1619).

### Cost
- Build: plugin overhead is in the noise. The metadata snapshot takes 1 s. Cold 4-CPU
  build: see below.
- Run: one child import of the edited sqlglot (about 0.8 s CPU) per commit whose typing
  edit is outside functions. That is 2 of 118 practice-like commits.

### Notes for integration
- `strat/r2-tables` (per-key family tables) also records table-key reads. If both land,
  the `_MetaDict` recorder and theirs should share one mechanism.
- This cut never touches tpcds. Max/Min/Add/Sub readers include tpcds, so those edits
  stay at about 25 s.
