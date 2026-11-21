# r2-ledger: where the base selector's cost goes (base `f89594d`)

Branch `strat/r2-ledger`. Tools and raw tables: `research/r2-ledger/`.

## Method

- Image: `br-strat-runcpu2` (built from `f89594d`; `blast/*.py` and `selector.py` checked
  md5-identical to the commit, so this is the base image and its maps).
- `research/r2-ledger/ledger.py` runs *in that image* and re-executes the selector's
  pipeline (triage.classify, then a copy of `impact.select`) with the per-line rule
  functions wrapped, so every selected test carries the `(file, rule)` tags that put it
  there. Its selections were checked equal to the real `selector.py` stdout of the same
  image on all 5 sets (sample, m1, m1-extra, local-20261120, syn640: 0 mismatches).
- Rules (tags): `fn-body` (function-body statement coverage), `fn-header`
  (decorator/signature/defaults), `fn-docstring`, `class-stmt:<names|users(...)>`
  (class-level statement: narrowed by name readers, or every user of the class because
  the name is read at import / statement binds nothing nameable), `module-stmt:...`
  (same at module level), `import-fn:core|module` (function-body lines that also ran
  during an import: `core` = every test), `lambda-only`, `exprattr` (gap-expr state diff),
  `relprof-refined`, `import-check-fail` (the import check found a new import error,
  so every test), `triage:*` (fixture readers, tpc records, test functions), `floor`
  (tests the map does not know: **empty** in this image, 0 cost).
  `CORE-FILE` marks edits in `tokens.py`, `trie.py`, `tokenizer_core.py`, `__main__.py`,
  `typing/__init__.py`.
- `analyze.py` (host, uses answers: analysis only) computes per commit our cost, the oracle
  (only the failing tests, best split; 0 for unlabelled syn640, so its "excess" is an upper
  bound), and each rule's **marginal saving**: cost(S) - cost(S with that rule's tests
  removed unless another rule also selected them or they fail). Savings overlap, so they
  rank opportunities; they do not add up.
- "practice-like" = the mean per commit over m1, m1-extra and local-20261120 (the sets
  that predicted practice). syn640 is the generator's distribution, selection cost only.

## Totals (simulated, no CPU)

| set | commits | ours | per commit | oracle per commit | excess per commit | misses | all-tests commits |
|---|---|---|---|---|---|---|---|
| sample | 30 | 123.0 | 4.10 | 3.15 | 0.96 | 0 | 0 |
| m1 | 64 | 420.9 | 6.58 | 2.30 | 4.28 | 0 | 3 |
| m1-extra | 22 | 203.1 | 9.23 | 4.64 | 4.60 | 0 | 0 |
| local-20261120 | 32 | 326.4 | 10.20 | 3.94 | 6.26 | 0 | 0 |
| syn640 (no labels) | 640 | 5578.3 | 8.72 | n/a | <= 8.72 | n/a | 27 |

Practice-like: 8.67 s ours vs 3.63 s oracle, **5.05 s excess per commit**.

## Ranked opportunities (seconds per practice-like commit; marginal)

| # | opportunity | practice-like | m1 / m1x / local | syn640 (upper bound) | selected by |
|---|---|---|---|---|---|
| 1 | **#1106 tpcds** when not failing | **2.27** (tops busiest worker: 2.93) | 1.23 / 2.21 / 3.38 | 2.46 | optimizer fn-body (most), tpc-h records triage (1 per set), optimizer fn-header, typing, parsers/base table |
| 2 | **class-level tables, users-of-class because the name is read at import** (dialects, parsers; generators in syn640) | **1.35** (1.33 after tpcds is gone) | 1.24 / 0.96 / 1.84 | 1.85 | dialects 0.91, parsers 0.42; heavy test on top is **#1224 generator_memory** in 8 of 21 practice-like cases, else #1295 annotate_funcs / #1428 scope |
| 3 | fn-body, non-tpcds part | 0.59 | 0.33 / 0.73 / 0.72 | 1.46 | optimizer 0.31, parsers 0.12, dialects 0.09, expressions 0.04 |
| 4 | tpc-h records triage rule (all of it is tpcds) | 0.53 | 0.27 / 0.80 / 0.52 | 0.11 | `tests/fixtures/optimizer/tpc-h/tpc-h.sql` record edits select #1106 |
| 5 | import-check-fail -> all | 0.17-0.19 | 0.56 / 0 / 0 | 0.51 | 2 m1 commits that really break the import of a dialect (oracle 27.5 s and 10.7 s), so most of this is real |
| 6 | fn-header (signature/defaults) | 0.17 non-tpcds (0.36 with) | 0 / 0.49 / 0 | 0.04 | 1 optimizer commit |
| 7 | **CORE-FILE fallback** (`typing/__init__` in practice-like sets) | 0.19 (module-stmt 0.16 + lambda-only 0.03) | 0.48 / 0 / 0.10 | 0.75 (tokens 0.29, trie 0.27, tokenizer_core 0.18, `__main__` 0.23 per syn commit, 18 of 640 commits) | m1 `hpym` typing/__init__ AnnAssign -> every test (oracle 4.0 s); local typing lambda-only selects tpcds |
| 8 | module-stmt users (non-core) | 0.08 | | 0.48 | optimizer/executor module tables |
| 9 | fn-docstring | 0.07 | | 0.05 | generators/optimizer docstrings (cov of whole function + `__doc__` readers) |
| 10 | import-fn:module | 0.07 | | 0.01 | expressions function lines run at import of another module |
| 11 | lambda-only, exprattr, triage fixture, floor | <= 0.04 each; floor 0 | | triage fixture 0.41 | |

Heavy tests, saving from dropping each alone when it does not fail (practice-like):
tpcds 2.27, generator_memory 0.30, max_nodes 0.20, all five heavy together 3.05.
By test on top of the busiest worker (whole commit excess): tpcds 2.93, other 0.75,
max_nodes 0.71, generator_memory 0.66.

By edited area (whole-commit excess, practice-like): optimizer 1.41, dialects 0.99,
parsers 0.72, tests (tpc records, import-check) 0.55, expressions 0.48, typing 0.40,
generators 0.27, executor 0.04.

## What this says

- tpcds is about half the excess. Outside tpcds, the **class-level table rule is the
  largest single cause (1.33 s/commit)**, more than twice the non-tpcds fn-body excess.
  The typical case: a one-entry edit in a dialect's `TIME_MAPPING`, tokenizer `KEYWORDS`,
  parser `FUNCTIONS` or `NO_PAREN_FUNCTIONS`, selecting 16 to 320 tests (every test that
  used the dialect), with #1224 generator_memory (6.7 s) on the busiest worker. The
  mechanism that fits is gap-expr's, generalized: state-diff the import-time values
  (including metaclass-derived tries and inverse maps) and select the tests that read the
  changed attribute of that class at run time.
- The **core-module fallback is small on practice-like data**: no `tokens.py`,
  `trie.py`, `tokenizer_core.py` or `__main__.py` edit occurs in m1, m1-extra or local;
  only `typing/__init__.py` (2 commits, 0.19 s/commit). On syn640 these files are 18 of
  640 commits at about 37 s each, a 0.75 s/commit upper bound, but those edits
  (`or -> and` in the tokenizer, trie `0 -> 1`) mostly break broad swaths of tests in
  reality, so the true saving is much lower.
- The floor is empty. Misses: 0 everywhere.
