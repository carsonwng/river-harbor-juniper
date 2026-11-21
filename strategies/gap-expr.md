# strat/gap-expr: expression class attributes read per test, from the coverage pass

Branch `strat/gap-expr`, cut from `strat/gap-recall` (`9a1e1cd`: int/night-2120 + import check
+ cache widening). Impact layer only: `blast/impact.py`, `build/impact.py`,
`build/impact_lib/br_cov_plugin.py`, and `build/impact_lib/br_state.py`, taken unchanged from
`strat/impact-attrs` (credit: impact-attrs, the import-time state diff).

## Question (from gap-heavy)

Most unnecessary heavy selections come from class-level edits in `sqlglot/expressions/*.py`,
mostly `arg_types` entries. The base treats `arg_types` as "read at import", so every user of
the class runs, and through the registry rule every parser test uses every expression class:
m1 `bf82` (`AddMonths.arg_types["expression"]` True -> False) and `gu57` (`ArrayFirst`
key `this` -> `thi`) each select 1418 tests (36.8 s, the whole suite on 4 workers).
attrs fixes this with a separate attribute-read profile, but that pass does not fit the
build budget (the combo image skips it: `attrs: skipped (expected 397s, 93s left)`), so
in practice nothing is left of attrs for these edits.

## Hypothesis (H-expr)

An edit confined to class-level assignment statements changes the program only through the
values bound at import. If the import-time state diff of the edited sqlglot shows that the
*only* changes are plain data attributes of sqlglot `Expr` classes (`arg_types`,
`required_args` derived in `__init_subclass__`, `is_var_len_args`, `_sql_names`, the
`NAME_TO_PROPERTY` tables, ...), the tests that can fail are the tests that read one of
those attributes of one of those classes at run time. That read set can be recorded in the
existing coverage pass at negligible cost, with no extra suite pass.

## Mechanism

Build (`br_cov_plugin.py`, same coverage pass): after `import sqlglot`, every plain data value
(dict, set, list, tuple, bool, int, float, str, None; not dunders, not `key`) in the own
`__dict__` of every sqlglot `Expr` subclass is replaced by a non-data descriptor that returns
the same object and records `(class read through, class that defined it, attribute)` for the
current phase. Classes created later (lazy imports) are wrapped through
`Expr.__init_subclass__`. Phases are attributed like lines (setup/teardown -> test class,
collection -> test module). At session end, a descriptor no longer in place or a tracked-type
attribute added at run time is listed as unsafe; a duplicated `module:qualname` marks the class
unsafe. `build/impact.py` writes `exprattr.json` (attribute -> test set) and the pristine
state (`attrs/state.json`, `br_state.build_state`, 29 s).

Run (`blast/impact.py` `_expr_state_bits`): when the base selection is non-empty and every edited
line is in a class-level `Assign`/`AnnAssign`/`AugAssign` (not in a function, lambda, header or
class-level if/for), `br_state.py diff` imports the edited sqlglot. Import failure -> every test
(and the gap-recall import check is then redundant for a single-file commit, so it is skipped).
If every change is `["class", C, attr, ...]` with `C|attr` tracked and not unsafe, the selection
is the readers of those entries; anything else (module global, parser/generator tables, a new or
removed attribute or class, a volatile entry, a failed diff) keeps the base selection.

Rule check: the maps record which tests read which class attribute on the pinned commit (a
finer coverage); nothing about patches, edit kinds or outcomes. The selector derives the
changed attributes by importing the edited code.

## Results (maps built in the br-int-night-2120 image, selection run in that image)

Selection before (`BR_IMPACT_EXPRATTR=0`, same maps) -> after, simulated seconds:

| set | before | after | misses |
| --- | --- | --- | --- |
| sample (30) | 123.962 | 123.088 (`ktz3` 29 -> 3 tests) | 0 / 0 |
| m1 (70) | 686.266 | 621.697 (`bf82` 1418 -> 6, `gu57` 1418 -> 3, keeps #1295) | 0 / 0 |
| local-20261120 (16) | 170.630 | 170.630 (no such edit) | 0 / 0 |
| synE (40 code edits, generate.py `--only-area expressions`, seed 7101) | 734.042 | 300.524 | see below |

25 of the 40 synthetic expression-area edits hit the rule; 12 of them selected the whole suite
before (1360-1425 tests) and select 1-118 after.

## Verification (full suite on the patched tree, br-label image)

(filled in as labels finish)

## Costs

(filled in: coverage-pass overhead of the descriptors, run CPU of the diff)
