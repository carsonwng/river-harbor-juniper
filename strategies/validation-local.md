# validation/local-20261120: first held-out set, labelled in Docker

Set: `validation/sets/local-20261120/` (`commits/*.patch`, `tests.json`, `answers.json`, plus
`edits.json`, which describes each edit for people reading misses). Validation data only. The
selector and its build must never read anything under `validation/`.

```sh
python harness/br.py eval --docker --data validation/sets/local-20261120 -m "..."
```

## Method

- Tooling: `validation/generate.py` and `validation/label.py` from `origin/validation/m1`
  (`6a8ae46`, which came from `strat/mutants`). This branch merges `origin/main` so that it
  carries `br.py verify`.
- Generation: `generate.py --areas --seed 20261120 -n 60` on a scratch clone
  (`.cache/validation-local/gen`, a `git clone --local` of the pinned `eb11aec4`; the shared
  cache was never touched). It used the default kind weights (code .56, fixture .27,
  comment .10, multi .07) and drew a source area before a file. I added three docstring-only
  edits (`--kinds comment=1 --seed 777`, keeping only the `docstring` edits), because the main
  draw produced no docstring edits.
- Subset: labelling turned out to be slow (below), so I labelled a subset of the 63
  generated patches. I chose it **without looking at outcomes**: within each kind, sorted by
  id, alternate entries. Batch A is the 16 below. Batch B is the other 16 alternates (8 code,
  4 fixture, 1 comment, 1 docstring, 2 multi). Both batches are in the set: **32 commits**.
  The remaining 31 generated patches stay unlabelled in `.cache/validation-local/v1/pending`.
- Labelling environment: `validation/Dockerfile.label`, which is
  `python:3.13-slim@sha256:e48f59cd…` (the submission Dockerfile's digest) plus
  `requirements.lock` (pytest 9.2.0, xdist 3.8.0, timeout 2.4.0). Each run is the full suite,
  `SKIP_INTEGRATION=0 python -m pytest tests` with `--network none` and
  `PYTHONDONTWRITEBYTECODE=1`, with the `br_report` plugin recording failing node ids.
- Changes to `label.py` on this branch:
  - The patched clone is bind-mounted read-only and **copied into the container** before
    pytest runs. On Docker Desktop for Windows, the bind-mounted tree ran 471 s vs 339 s
    with `-n 4`.
  - Each container gets `--cpus = workers`, can be overridden with `BR_LABEL_CPUS`, and runs
    under `nice -n 10`.
  - `BR_LABEL_WORK` puts the scratch clones in `.cache/validation-local/work`, not the
    shared `.cache/mutants/work`.
- CPU: 4 containers × 1 pytest process (`--copies 4 --workers 1`), so 4 test processes in
  total. This gave more throughput than 1 × `-n 4` (240 s vs 340 s per commit of wall time),
  because `test_max_nodes` (94 s here) leaves the xdist workers idle at the end.

## The environment matches the scorer's labels

I relabelled 5 sample commits with the same Docker recipe: `2haz` (0 failing), `2jrq` (8),
`2vdj` (1), `rszg` (3) and `3utz` (37). **All 5 failing sets equal `sample/answers.json`
exactly.**

## Set composition (32 commits)

Batch A:

| id | kind | failing | edit |
| --- | --- | --- | --- |
| 0n1s | fixture | 1 | tests/fixtures/optimizer/pushdown_projections.sql: line 245: insert 'NULL' |
| 23xe | code | 25 | sqlglot/generators/python.py: == -> != |
| 31n7 | fixture | 1 | tests/fixtures/optimizer/qualify_tables.sql: line 324: insert '42' |
| 3ykp | code | 0 | sqlglot/dialects/duckdb.py: 3 -> 4 |
| 5u2j | multi | 51 | sqlglot/executor/context.py: != -> ==; sqlglot/optimizer/journal.py: is -> is not |
| 5z9i | comment | 0 | sqlglot/dialects/prql.py: comment before line 24 |
| 6ku0 | code | 1 | sqlglot/dialects/fabric.py: string "UTINYINT" -> "utinyint" |
| 955b | comment (docstring) | 0 | sqlglot/optimizer/simplify.py: docstring line 277: logical -> logica |
| 95r6 | code | 59 | sqlglot/optimizer/qualify_columns.py: drop not |
| 9y3p | code | 3 | sqlglot/optimizer/eliminate_joins.py: and -> or |
| b13e | fixture | 1 | tests/fixtures/optimizer/quote_identifiers.sql: line 1: insert 'a' |
| bajg | code | 3 | sqlglot/executor/python.py: and -> or |
| d4s6 | fixture | 0 | tests/fixtures/optimizer/eliminate_ctes.sql: line 1: delete 'i' |
| d811 | code | 1 | sqlglot/parsers/databricks.py: string "REGR_AVGY" -> "REGR_AVY" |
| f7kz | code | 1 | sqlglot/parsers/base.py: drop element line 13: TokenType.LOCALTIMESTAMP: exp.Localtimestamp |
| kryt | comment | 0 | sqlglot/optimizer/canonicalize_internal_names.py: trailing comment at line 129 |

Batch B:

| id | kind | failing | edit |
| --- | --- | --- | --- |
| 6bhm | multi | 5 | 3 fixtures: qualify_columns_ddl.sql insert '('; tpc-h/tpc-h.sql insert '42'; unnest_subqueries.sql case JOIN |
| c6z4 | multi | 1 | tests/fixtures/optimizer/annotate_types.sql: line 14: delete 'O'; sqlglot/typing/databricks.py: comment before line 30 |
| g9ct | code | 0 | sqlglot/optimizer/merge_subqueries.py: True -> False |
| h12g | code | 1 | sqlglot/optimizer/helpers.py: is -> is not |
| hl6e | fixture | 1 | tests/fixtures/optimizer/optimizer.sql: line 1106: delete 'x' |
| i0ru | code | 8 | sqlglot/typing/__init__.py: is not -> is |
| k1ra | fixture | 1 | tests/fixtures/optimizer/unnest_subqueries.sql: line 355: split word |
| kghr | code | 0 | sqlglot/dialects/materialize.py: string "v" -> "V" |
| nu29 | code | 0 | sqlglot/typing/hive.py: is not -> != |
| o0d4 | code | 2 | sqlglot/typing/hive.py: string "this" -> "thisX" |
| osg5 | fixture | 1 | tests/fixtures/optimizer/optimizer.sql: line 183: insert 'NULL' |
| p4xq | code | 2 | sqlglot/generators/python.py: drop not |
| s31h | code | 0 | sqlglot/optimizer/resolver.py: is -> == |
| triu | fixture | 1 | tests/fixtures/optimizer/canonicalize_internal_names.sql: line 43: delete '"' |
| u8sx | comment | 0 | sqlglot/typing/clickhouse.py: comment before line 20 |
| uwn6 | comment (docstring) | 0 | sqlglot/generators/postgres.py: docstring line 555: need -> needs |

Label stats: 32 commits (16 code, 8 fixture, 5 comment including 2 docstring, 3 multi).
**11 break nothing** (5 comment/docstring, 5 code, 1 fixture). The sample has 12 of 30.
Failing-set sizes range from 0 to 59, with a median of 1. All 32 runs completed cleanly: a
full pytest summary, rc 0 or 1, all 1620 numbered tests seen, no timeouts retried, no unknown
ids, no collection errors. The oracle floor is 126.0 s over 32 commits (93.4 s for batch A).
Running every test costs 32 × 36.8 = 1178 s.

## int/night-0147 (`24f5e6d`) on the full 32-commit set

The detached worktree and `br.py eval --docker --data` give **0 misses**: simulated 326.4 s,
cgroup CPU 3.2 s, **total 329.5 s** (oracle 126.0 s). The most expensive commit is f7kz
(`parsers/base.py`: 771 tests selected for 1 failing, 3 workers, 36.0 s). In batch B, the most over-selected
commits are s31h (178 tests selected, 0 failing, 25.5 s), g9ct (25.5 s, 0 failing) and i0ru
(25.5 s for 8 failing). nu29 (`is not -> !=` in `typing/hive.py`, 0 failing) was skipped
entirely.

`python harness/br.py verify int/night-0147` from the repo root: **VERIFY PASS**. Its sets are
sample, 0 misses, total 135.0 s; local-20261120, 0 misses, 172.8 s (this ran the 16-commit
batch A copy that sits untracked in the main checkout, not the 32-commit set on this branch);
m1, 0 misses, 507.2 s; m1-extra, 0 misses, 259.8 s; and syn-import16, 0 misses, 593.0 s.
The lead has since copied this 32-commit set into the main checkout's `validation/sets/`,
so future `verify` runs use all 32. No more labelling is planned. The held-out sets now total
64 (m1), 22 (m1-extra) and 32 (this one).

## int/night-1948 (`1a5f961`) on batch A

`br.py verify` only finds sets in the ref's tree or in the main checkout, so I ran the
equivalent by hand: a detached worktree at `1a5f961`, then
`br.py eval --docker --data <this set>`:

- **local-20261120 (16): 0 misses.** Simulated 170.6 s, cgroup CPU 1.5 s, **total 172.2 s**
  (oracle 93.4 s). The costliest commits are f7kz (36.0 s: `parsers/base.py`, 771 tests
  selected for 1 failing), 95r6, 9y3p, 5u2j and 23xe (24 to 25 s each), and 3ykp (12.5 s, 0 failing).
  Both comment-only edits cost 0. The docstring edit (955b) still selected 16 tests, at 2.7 s.

`python harness/br.py verify int/night-1948 -m "held-out check"` from the repo root:
- sample: 0 misses, total 180.2 s.
- **m1: 2 misses, `8owm` and `doay`** → `VERIFY FAIL`. `m1` is an *untracked*
  `validation/sets/m1/` in the main checkout. Another session put it there; it isn't from this
  branch. Both misses are `* -> +` edits, in `sqlglot/dialects/hive.py` and
  `sqlglot/parsers/risingwave.py`. `8owm` breaks about 1100 tests, and `doay` 17. The run is
  only as clean as that untracked set. I didn't inspect or change it.
- Other local agents were running `br-label` containers during these evals. The CPU numbers
  are cgroup-measured and small, so the totals are barely affected.

## Incident: killed br-label containers (lead report, about 23:2x)

The lead reported that another agent killed up to 4 `br-label` containers by mount pattern
(funny_hodgkin, objective_mendel, unruffled_torvalds, zen_driscoll, quirky_neumann). I
checked every batch A label. Each has a completed pytest summary in its `.report.out`, rc in
{0, 1}, `seen == 1620` and no retries. All 16 label files were written between 21:12 and 22:00,
before the kill window, and the labelling process had exited. **No batch A commit was
affected, and none needed relabelling.** Batch B ran from about 23:14 to 00:46, so its first round (g9ct, h12g, 6bhm and c6z4,
23:14 to 23:38) overlapped the reported window. A killed container would exit 137 or 143 with no
pytest summary. All 16 batch B runs have a complete summary (`... passed ... in NNNs`),
rc 0 or 1, 1620 seen and no retries, so none were killed and none needed relabelling.

## Caveats

- These are synthetic single-site mutants, not real commits. Fixtures are all under
  `tests/fixtures/optimizer/`, because that is where the generator finds `.sql` files. The
  generator only draws a fixture's line, not whether it is a test-input or a metadata line.
- 32 commits is small. With 0 misses here, the false-negative rate could still be above zero.
- Batch B ran at about 1450 s per commit per container, against about 950 s for batch A,
  because other agents' `br-label` containers were sharing the machine.
- Sample timings in `tests.json` are about 3.3× faster than this laptop under load
  (`test_max_nodes` takes 28.8 s nominal and 94 s here), so this labels about 15 commits per hour
  at 4 processes.
- Full-suite labelling only. I didn't use coverage or other pruning, so the labels share no
  assumptions with the impact layer.
