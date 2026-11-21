# gap-integrate: the lead's gap-* branches on top of int/night-0147

Integration agent, night of 2026-11-21 (04:00-05:00). Base: `origin/int/night-0147`
(`24f5e6d` = origin/main + strat/gap-mix), the best m1-safe practice entry (244.9 s).

**Best branch: `int/night-0434-all`** = 0147 + gap-tests + gap-expr (ported) + gap-heavy.

## Combinations tried (docker `br.py verify`, host builder, all sets 0 misses)

Totals (simulated + cgroup CPU). syn-import16 and gap-tests-tpc are the branch sets.

| set | 0147 `24f5e6d` | +tests `fb8fb95` | +tests+expr `428d2d4` | +tests+expr, prefilter `ae47472` | +tests+heavy `d1d1203` | **all `5102dc1`** |
| --- | --- | --- | --- | --- | --- | --- |
| sample (30) | 135.0 | 128.3 | 130.2 | 127.3 | 133.3 | **125.9** |
| m1 (64) | 507.2 | 464.8 | 443.2 | 433.0 | 448.6 | **416.9** |
| m1-extra (22) | 259.8 | 238.5 | 209.5 | 206.7 | 238.3 | **206.5** |
| local-20261120 (32) | 329.5 | 329.5 | 333.1 | 329.5 | 308.3 | **308.3** |
| syn-import16 (16) | 593.0 | 593.2 | 595.9 | 593.1 | 593.8 | 593.4 |
| gap-tests-tpc (20) | n/a | 361.1 | 361.2 | 361.2 | 361.2 | 361.1 |

Caveat: coverage maps are not reproducible build to build (research/open-questions.md).
The 0147 -> +tests sample drop (131.4 -> 124.7 sim) has no tpc commit in it, so it is map
variance, not gap-tests. Layer effects below were isolated inside one image instead.

## What each piece does, isolated in one image

- **gap-tests** (triage only, merged clean): tpc-h/tpc-ds fixture edits narrowed by record
  field. Only fixture commits move (m1 4qrg/elzo/mji2, m1-extra 4mef/qr0o, local 6bhm).
- **gap-expr** (`b0a6902` ported, `e26d85f` not): class-level-assignment edits in sqlglot
  Expr classes are re-scored by an import-time state diff plus per-test attribute reads
  recorded in the coverage pass. In the 0405 image only m1 `bf82` changed (1418 tests,
  36.8 s -> 6 tests, 4.7 s); sample `ktz3` and m1-extra also shrink.
- **prefilter (new, `ae47472`)**: the state diff imports the edited sqlglot (about 0.5 s CPU)
  and could only narrow when every changed attribute is a tracked Expr-class attribute.
  14 of 15 m1 diffs were dialect/parser/generator classes and kept the base. Now the diff
  runs only when every edited line sits in a class present in `exprattr.json` tracked.
  Skipping keeps the base selection, so it cannot drop a test. m1 selection identical,
  container wall 23.4 -> 13.5 s; CPU back to 0147 levels on every set.
- **gap-heavy** (instruction-coverage gate on tests >= 1.5 s). Same image with `Maps.instr`
  forced to None: sample 131.4 -> 130.0, m1 453.1 -> 436.3, m1-extra 235.5 -> 235.0,
  local 326.4 -> 304.9.

## Conflicts

- gap-expr sits on strat/gap-recall, whose own import check (`impact_import.py`,
  `_touches_import`/`_import_breaks`) and cache widening (`_cache_bits`) overlap
  gap-mix's import check. A full merge is a semantic choice between two import checks,
  so **the branch merge was skipped**. Only gap-expr's own commit `b0a6902` was
  cherry-picked: gap-mix's import check and lambda path kept as-is, the state-diff
  narrowing appended after them (without `_cache_bits`, which 0147 never had).
  `e26d85f` (skip gap-recall's check after a state diff) does not apply; both checks run.
  gap-expr was committed with CRLF; normalised to LF.
- gap-heavy: mechanical. Maps keeps both accessor groups; the build runs the state
  snapshot, then the instr pass, then gap-mix's compileall.

## Build time (`eval --docker --scorer-limits --cold`, 4 CPU / 8 GB, local)

428.8 s total (target 420, limit 600): coverage 79.3 s, relprof 206.2 s, state snapshot
9.9 s, instr 52.9 s (19/19 tests), impact step 360.6 s, triage 8.6 s. Simulated 121.6 s,
0 misses. (`br.py cpu` queue was deadlocked by stale wait/slot files from 00:39-00:45
and a 22:55 slot; the build ran without the queue, capped at 4 CPUs by the builder.)

## Practice effect: which gains survive the scorer's build

On practice, relprof was dropped by its time limit. The instr pass has a deadline of the
impact step's start + 450 s (`BR_IMPACT_DEADLINE`) and needs 40 s left, so if relprof
runs to its 420 s limit, instr is skipped too. gap-tests is runtime-only, gap-expr's map
comes from the coverage pass plus a 10 s snapshot: both survive.

Practice-like run (same images, `Maps.relprof` and `Maps.instr` forced to None), sim:

| set | 0147 | all |
| --- | --- | --- |
| sample | 184.1 | 175.8 |
| m1 | 536.0 | 461.5 |
| m1-extra | 256.8 | 203.1 |
| local-20261120 | 326.4 | 326.4 |

Expected practice: 0 to -14 % sim, likely -3 to -8 % (242 -> about 225-235), CPU unchanged.
gap-heavy's gain transfers only if the scorer's relprof finishes or is moved after instr.

## Dead ends / not done

- Reordering instr before relprof (or giving instr its own share of the deadline) would
  carry gap-heavy's local gains to practice. It changes the build policy, so left to the lead.
