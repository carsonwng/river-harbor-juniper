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
- Build: the plugin overhead is in the noise and the metadata snapshot takes 1.3 s. A cold
  4-CPU / 8 GB build of 249c558 took 425.5 s, against 374.0 s for 4ef5e24 on a quieter
  machine. Most of the difference is the relprof pass (237 s vs 203 s), which is machine
  load and not this cut.
- Run: one child import of the edited sqlglot, about 1 s of CPU. It runs only for a typing
  edit outside functions, and only when the base selection's cost lower bound beats the
  cheapest selection by more than 2 s (`_worth_a_diff`, a CPU trade-off only). The relation
  profile now runs first, so an edit it already proves inert never pays for the diff.

### Docker totals (e1c0ab2, `br.py eval --docker`, cgroup CPU, host builder)

| set | base f89594d | e1c0ab2 |
|---|---|---|
| sample | 125.407 (123.009 + 2.398) | 125.684 (123.009 + 2.675) |
| m1 | 425.693 (420.921 + 4.772) | 393.978 (388.078 + 5.900) |
| m1-extra | 205.320 (203.124 + 2.196) | 205.776 (203.124 + 2.652; at 249c558, same selection) |
| local-20261120 | 327.910 (326.371 + 1.539) | 328.586 (326.283 + 2.303) |

The CPU differences on sample and m1-extra are noise: no typing diff runs there. On
local, one diff runs (i0ru, whose base is tpcds, read by Max/Min), so that is about 1 s
with no saving.

### Notes for integration
- `strat/r2-tables` (per-key family tables) also records table-key reads. If both land,
  the `_MetaDict` recorder and theirs should share one mechanism.
- This cut never touches tpcds. Max/Min/Add/Sub readers include tpcds, so those edits
  stay at about 25 s.

## 3. Investigation (not implemented): tpc-h records -> #1106 through `TestExecutor.cache`

- **The memo** (tests/test_executor.py:117-130): `cls.cache = {}` is set in setUpClass.
  `cached_execute(sql, tpch)` does `if sql not in self.cache: self.cache[sql] =
  conn.execute(transpile(sql))...`.
  - It is keyed **only by the SQL text** (field 1 of a record). The tpch flag and the
    connection are not part of the key.
  - Writers and readers are test_optimized_tpch (tpc-h field 1), test_execute_tpch (tpc-h
    field 1) and test_execute_tpcds (tpc-ds field 1). Nothing else touches it.
- **The coupling is real but key-local.** A tpc-h record edit can change what tpcds reads
  only if one of the edited tpc-h SQL texts, old or new, equals a tpc-ds SQL text. That is
  the one way a tpc-h execution can fill an entry tpcds later reads, or stop filling it.
  Test order makes it worse, since the tpc-h result would be served to tpcds on the wrong
  connection.
  - At the pin, the 22 tpc-h and 99 tpc-ds SQL texts share **0** strings.
  - An edit to field 2 (the optimized SQL) never reaches the cache.
  - A loader failure is already covered: setUpClass failure selects every test of the
    class.
- **So narrowing is sound under one run-time check.** The rule: drop the cache-mediated
  propagation for `cache` when no edited field-1 value (old or new) of the edited fixture
  is a field-1 value of the other fixture stored on the class.
  - The current fixpoint in `blast/triage.py:413` propagates on "mutates a shared data
    attribute", which is what pulls #1106 in.
  - Implementing it needs build/triage_records.py to recognise the keyed-memo pattern:
    an attribute mutated only as `A[k] = ...`, guarded by `k not in A`, and read only as
    `A[k]`, with `k` flowing from a record field. It records `(attr, fixture, field)` for
    each user.
  - The runtime then propagates only on key collisions.
- **Value:** 0.53 s per practice-like commit (1 commit per set). Handed back to the lead
  when I switched to integration; not built.
