# strat/gap-branch: finer-than-statement placement (sub-expressions + arm probes)

Branch `strat/gap-branch`, cut from `origin/int/night-2120` (impact layer only:
`blast/impact.py`, `build/impact.py`, `build/impact_lib/br_cov_plugin.py`,
`build/impact_lib/br_import_probe.py`).

## Question

The base maps an edited line in a function body to the tests that ran its enclosing
statement (its header for compound statements). How much of the selection comes from
that coarseness, and what sound refinement removes it?

## First finding: if/elif arms are already separate

Line coverage already separates the arms of `if`/`elif`/`else` chains and loop bodies: they
are different statements on different lines, and `_chain` descends into the arm. So a
BRANCH-event map of statement arms would add nothing. The coarseness that is left is
*inside* one statement:

1. a multi-line statement: the statement rule takes the union over every line of the
   statement, even when the edit sits in one operand only some tests evaluate
   (`x if c else y` over three lines, one argument of a long call, a non-first `and` operand);
2. a one-line expression: `a and b`, `x if c else y`, comprehension bodies and filters,
   lambda bodies, assert messages share the line with code every test runs, so no line map
   can split them;
3. class-level lambda tables (`TRANSFORMS`, typing `EXPRESSION_METADATA` annotators): a
   one-line replacement inside a lambda body goes through `_lambda_bits`, but the *added*
   line then goes through the name rule and re-adds every reader of the table, so the
   lambda rule never narrowed anything in practice.

Cost of function-body edits on the 400-commit synthetic set `syn1` (all-code edits,
`generate.py --seed 7 --kinds code=1`), by where the minimal differing sub-expression sits
(measured with `condpos.py`, after refinement 1):

| position of the edit | commits | cost s |
| --- | --- | --- |
| if/while condition, operand always evaluated | 84 | 572 |
| unconditional operand of a simple statement | 57 | 447 |
| if/while condition, non-first and/or operand | 41 | 309 |
| if-else arm / and-or operand / comprehension / lambda (not a condition) | 15 | 205 |

Rows 3-4 (514 s of the 1 569 s function-body total) are what sub-line information can
narrow. Rows 1-2 cannot be narrowed by any coverage: every test that evaluated the edited
operand must stay (edits to a condition keep every test that evaluated that operand).

## Mechanism

### Runtime (`blast/impact.py`, `_fine_parts`, `_part_bits`, `_arm_filter`)
Statements are paired as relprof does (`_differing`: equal text is equal AST). For each
differing statement in a function body, `_diff` finds the minimal old nodes E whose old and
new subtrees differ with everything around them identical. Until some E starts evaluating,
the patched function executes exactly like the pinned one, so only tests that evaluated an E
can change. Then:
- E must own an instruction of the pinned code (an instruction whose position lies inside
  E). An E without one (folded into a constant tuple, a constant `while True` test) climbs to
  its parent; reaching the statement keeps the statement rule.
- A test that evaluated E executed one of those instructions, and the coverage pass records
  every executed line, so `cov(E's lines)` holds every such test.
- That set is intersected with the arm-probe set of every probed arm enclosing E inside the
  statement (see build); import-time lines of E are added back unfiltered (`import_bits`).
- Kept on the statement rule: the statement itself differs (target list length, an
  operator of an AugAssign, ...), the set of names the function binds changes, or a
  yield/await/walrus/global/nonlocal appears or disappears (scope and code-kind changes act
  outside E), docstrings, def/class headers, except handlers and match cases.
- Class/module-level statements: when every E lies in a lambda *body*, the lambda body's
  coverage intersected with its arm probes (the lambda was called), plus the tests in
  modules that read code objects (`__code__`/`co_code`/`co_consts`, `tests/test_typing.py`
  compares annotator lambdas without calling them), plus the users of every module whose
  import called a lambda starting on that line (import probe). Maps without these two
  records keep the statement rule.

Edited lines handled this way skip the per-line rules; all other lines are unchanged.
`BR_FINE=0` turns it off.

### Build
- `br_cov_plugin.py` (same coverage pass, no extra suite run): an import hook compiles every
  sqlglot module from its pinned source with each conditionally evaluated operand A
  (and/or tail, if-else arm, comprehension element/key/value/filter/inner iterable, lambda
  body, assert message) rewritten to `(__br_arm__ or A)`, `__br_arm__` a builtin `False`.
  The probe name sits on a fake line `10_000_000 + index`, so its LINE event says the test
  entered A. The value, evaluation order and exceptions are A's. Real instructions keep
  their positions and the `or` keeps A's, so real line coverage can only gain the first line
  of an arm when it is entered (checked: on 3 test files, 1 line gained, none lost,
  identical outcomes; giving the `or` the fake position instead *lost* 10 lines, because a
  lambda's return takes its body's position). Annotations are not rewritten.
- `build/impact.py`: fake lines become `lines/<file>.json` `"arms"`: arm span -> test set
  (every probed arm listed, so a missing key means "not probed", never "not run"). Workers
  must agree on each file's arm table or the file gets no arms.
- `br_import_probe.py`: PY_START records lambdas (by first line) called during each
  module's import -> `"import_lambdas"`.
- `meta.json` `"code_readers"`: tests of test modules whose source reads code objects.

## Results (local, host Python, int/night-2120 docker maps WITHOUT arm/lambda records)

These cover only the runtime sub-expression placement. Without the new map records the arm
filter is a no-op and the class-level lambda path returns None (statement rule).

| set | base sim s | this sim s | misses (both) |
| --- | --- | --- | --- |
| sample (30) | 131.7 | 129.9 | 0 |
| m1 (42, current main checkout) | 455.5 | 444.6 | 8owm, doay (same in base) |
| syn1 (400 code edits, seed 7, unlabelled) | 4333.0 | 4166-4227 | n/a |
| syn2 (300 code edits, seed 11, unlabelled) | 3331.6 | 3229.5 | n/a |

(syn1 4227 is function bodies only; 4166 also includes a class-level lambda variant before
the import-lambda/code-reader guards were added, so take 4227 as the guarded figure.)

## Status at handoff (to r2-sound)

- Implemented: everything above (runtime + build).
- Validated: line maps unchanged under the arm rewrite on 3 test files (1 line gained, none
  lost, outcomes identical). Sample and m1 show no new misses.
- **Not done:** the full arm-map build (queued on CPU tokens, never ran), so there are no
  build seconds, no before/after numbers with arms, and no run-verification of dropped tests.
  The verifier is ready (`tmp/gb/mkjobs.py` + `vrun.py`: run the base selection on the patched
  copy in the image, then check that the failing tests are inside the fine selection).
- Build the maps (about one coverage pass plus the probe overhead; relprof skipped and copied
  from the image's maps):
  `docker run --rm --cpus 2 -e BR_RELPROF_TIMEOUT=1 -v <wt>:/w br-int-night-2120 python /w/build/impact.py --sqlglot /opt/sqlglot --out /w/.maps-arms/impact --work /w/.work-arms --jobs 2`
- Risk to check when composing with other import hooks (gap-expr descriptors, relprof): the
  arm loader is a meta_path finder for `sqlglot.*` and takes over loading, so two rewriting
  hooks must be merged into one loader.
