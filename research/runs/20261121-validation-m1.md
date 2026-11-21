# 2026-11-21: held-out validation set m1 (cloud session)

Branch `validation/m1` (from tag `cloud-base-1`). Tooling from `origin/strat/mutants` (`a4c246c`)
plus a docker labelling mode. Output: `validation/sets/m1/` (64 commits) and
`validation/sets/m1-extra/` (22 commits), both in harness format, 928 KB together.

**Validation data only.** Nothing under `validation/` may be read by the selector or a build
step; that would be a patch-to-tests table.

## Labelling environment

- Image `br-label` = `validation/Dockerfile.label`: the submission Dockerfile's base image at
  the same digest (`python:3.13-slim@sha256:e48f59cd…`, Python 3.13.18, SQLite 3.46.1) plus
  `requirements.lock`. `pip freeze` in the image is identical to `requirements.lock`.
- Per patch: apply to a scratch clone of sqlglot `eb11aec4`, then in the container (no network)
  `SKIP_INTEGRATION=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/work python -m pytest tests -n 4
  --timeout=180` with the `br_report` plugin. Failing = failure, error, timeout, a failing
  subtest (fails its parent), or a file that cannot be collected (fails every test in it).
  A test that times out is rerun alone with a 540 s budget and counts only if it fails again.
- Command: `python validation/label.py --set DIR --copies 1 --workers 4 --docker br-label`.

## Agreement with the sample (done before generating labels)

All 30 sample patches labelled this way: **30 / 30 exact matches** with `sample/answers.json`
(identical failing sets; 1620 numbered tests seen on every run; no unknown node ids; no
timeouts). That covers 12 clean commits, 16 commits with 1 to 3 failures, and the larger sets
(8, 10 and 37 failures).

## The sets

86 mutants were generated (`validation/generate.py`, seeds 101 to 106) and all 86 labelled; none
was incomplete or failed to apply. Raw mix: code 50 (17 clean), fixture 22 (0 clean), comment 8
(8 clean), multi 6 (0 clean); 25 of 86 clean (29%). That is more lethal than the sample (40%
clean), so 22 breaking commits were moved to `m1-extra`, chosen at random
(`random.Random(1).sample` over sorted ids: 17 of the 33 breaking code commits, 5 of the 22
fixture commits), not by which or how many tests they break.

| | sample | m1 | m1-extra |
| --- | --- | --- | --- |
| commits | 30 | 64 | 22 |
| code | 16 (53%), 9 clean | 33 (52%), 17 clean | 17, 0 clean |
| fixture | 8 (27%), 0 clean | 17 (27%), 0 clean | 5, 0 clean |
| comment | 3 (10%), 3 clean | 8 (12%), 8 clean | 0 |
| multi | 3 (10%), 0 clean | 6 (9%), 0 clean | 0 |
| break nothing | 12 (40%) | 25 (39%) | 0 |
| oracle floor (`br.py oracle`) | 94.4 s | 146.9 s | 102.0 s |
| every test, no maps (`br.py eval`, local) | 1104.5 s | 2356.3 s, 0 misses | 810.0 s, 0 misses |

Edited files by area (one count per edited file, multi commits count each file):

| Area | m1 | m1-extra |
| --- | --- | --- |
| parsers (`parsers/`, `parser.py`, tokenizer) | 9 | 4 |
| generators (`generators/`, `generator.py`) | 12 | 2 |
| dialects | 12 | 4 |
| optimizer | 7 | 2 |
| expressions | 3 | 2 |
| executor (`executor/`, `planner.py`) | 5 | 2 |
| core (other top-level modules, `typing/`) | 4 | 1 |
| fixtures (`tests/fixtures/**/*.sql`) | 20 | 5 |

Failing-set sizes in m1 (39 breaking commits): 25 with one test, 3 with two, 2 with three,
3 with four, then 5, 11, 17, 30, 71 and 1161. In m1-extra: 17 with one, then 3, 3, 5, 9, 46.
Heavy tests across all 86: `test_execute_tpcds` (#1106) fails on 3, `test_generator_memory`
(#1224) on 2, `test_max_nodes` (#1490) on none.

Generator settings: code edits draw an area first (`--areas`: parsers .20, generators .20,
dialects .18, optimizer .16, expressions .09, executor .08, core .09), then a file, then one of
the operators in `generate.py` (and/or, comparisons, `is`, dropped `not`, int ±1, string tweak,
dropped collection element, True/False, arithmetic). 4 top-up code edits were restricted to
executor (3) and expressions (1). Fixture, comment and multi edits are as on `strat/mutants`.
`edits.json` in each set records what each commit changed.

## Timing

- This box: 4 cores, about 140 s wall per full-suite run (median 140.3 s; the suite is recorded
  at 103 s CPU on the organisers' machine, so this machine is roughly 4 to 5 times slower).
- Sample check: 30 runs, 74 min. m1: 86 runs, 12,653 s (3 h 31 min), 147 s per commit.
- Slow outliers: `3utz` (sample, 442 s), `8owm` (284 s), `7lc8` (322 s, see below).

## Caveats

1. **`7lc8` (multi) has a borderline label.** `test_execute_tpcds` (#1106) hit the 180 s
   timeout in the full run, then passed alone in 199 s, so it is labelled passing. The mutant
   makes that test several times slower. Whether the organisers' run would time out depends on
   their timeout, which we do not know. Treat a selector "miss" or "hit" on #1106 for `7lc8` as
   uninformative.
2. **Each commit was labelled once.** No repeat runs for flakiness. Evidence that the suite is
   deterministic here: 30/30 sample agreement, and all 20 clean sample plus comment runs
   reported zero failures.
3. **The mutants are more lethal than the sample's code edits** (66% of code mutants break
   something against 44% in the sample). m1's clean share was restored by moving breaking commits
   out, which keeps the kind mix but does not make the individual edits more sample-like. The
   hidden sets may contain edit shapes the generator lacks (multi-line edits, added code,
   renamed symbols).
4. **Collection errors dominate one commit**: `8owm` (`*` to `+` in `dialects/hive.py`) breaks
   imports and fails 1161 tests. The sample's largest set is 37.
5. **No mutant breaks `test_max_nodes` (#1490).** The set cannot show that gating #1490 is
   safe; it only fails to contradict it.
6. **Fixture edits hit only `tests/fixtures/optimizer/*`**, as in the sample, although
   `identity.sql`, `pretty.sql` and `partial.sql` were eligible. All 22 break at least one test.
7. Partial pushes during the run (14, 28, 42, 56, 70, 84, 86 commits) had every commit in
   `m1`. Results reported against a partial set name a different commit list than the final one.
8. Docker Hub rate-limited the first pull of the base image (429); a retry worked. The image
   was pulled by digest, so its content is the pinned one.

## Reproduce

```sh
python3 harness/br.py sqlglot
docker build -f validation/Dockerfile.label -t br-label .
python3 validation/label.py --set <dir with commits/> --src commits --docker br-label --copies 1 --workers 4
python3 harness/br.py eval --data validation/sets/m1 -m "note"
```
