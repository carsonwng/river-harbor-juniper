# Practice submissions

Limit: one submission per hour. Track page: not committed (ask the human).

| # | Date | Commit | Contents | Sample (docker) | Practice board | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-11-20 | `a9e62c0` | pack + triage, impact at its default (all tests) | 720.2 s, 0 misses | **868.3 s** | Runs: 867.471 s sim, 0.82 s CPU, **0 missed** (from the track page) |
| 2 | 2026-11-20 20:18 | `1a5f961` | `int/night-1948`: main + strat/impact-base (provisional, sample only) | 178.9 s, 0 misses | **245.2 s** | receipt BB-69453A60. 0 misses (a miss alone costs 300 s). Practice/sample ratio 1.37 vs 1.21 for submission 1, so impact saves less on practice than on the sample |
| 3 | 2026-11-20 21:27 | `53eff6e` | `int/night-2120`: main + strat/impact-relprof (provisional) | 133.7 s, 0 misses | **243.2 s** | receipt BB-C54059C4. Runs 242.04 sim, 1.16-1.18 s CPU, 0 missed. CPU equals submission 2, so the relprof map was most likely dropped in their build (time limit) |
| 4 | 2026-11-21 00:01 | `2dd8f15` | `int/night-2354`: main + strat/impact-combo `760d8bd`, deadline 510 (default) | 194.1 s, 0 misses; m1 (70) 0; local (16) 0 | **284.7 s** | receipt BB-0083E2D6. Runs 279.936 sim, 4.76-4.87 s CPU, 0 missed. **First m1-safe submission** |
| 5 | 2026-11-21 01:54 | `24f5e6d` | `int/night-0147`: main + strat/gap-mix `5fc0c56` (int/night-2120 + lambda cut + import check) | 135.5 s; m1 (64) 0; m1-extra (22) 0; local (16) 0 | **244.9 s** | receipt BB-46097CB1. Runs 242.04 sim (= submission 3), 2.89-2.92 s CPU, 0 missed. **Best m1-safe** |
| 6 | 2026-11-21 04:34 | `ae47472` | `int/night-0405-expr`: night-0147 + gap-tests + gap-expr port (gap-integrate agent) | sample 127.3; m1 sim 420.9; m1-extra 203.1; local (32) 326.4; all 0 misses | **213.9 s** | receipt BB-83C2AEA4. Runs 210.365 sim, 3.54-3.57 s CPU, 0 missed. Best until sub 8; m1-safe |
| 7 | 2026-11-21 05:38 | `1b6118f` | `int/night-0519-norelprof`: sub 6 + gap-heavy + `ENV BR_RELPROF_TIMEOUT=0` (build 228.5 s) | sample 178.6 (no relprof); m1 456.8; m1-extra 206.7; local 308.4; all 0 misses | **215.8 s** | receipt BB-97E04B82. Runs 211.977 sim, 3.80-3.81 s CPU, 0 missed. +1.9 vs sub 6: heavy gives no practice gain, relprof was worth about 1.6 s. Shorter build (228.5 s) |
| 8 | 2026-11-21 06:58 | `f89594d` | `strat/runcpu2`: sub 6 + CPU-only cuts (AST cache, fork-server import check, compileall, -I -S); stdout byte-identical | sample 125.4; CPU m1 12.1 to 4.8; all 0 misses | **212.9 s** | receipt BB-AC138D27. Runs 210.365 sim (= sub 6), 2.50-2.51 s CPU, 0 missed. **Best; m1-safe; current final entry** |

## Reading submission 1

Practice commits cost about 36.8 s (source edit), 2 to 3 s (fixture) or 0 s (comment-only
skip), and a miss adds 300 s. Without the commit count or mix, 868.3 s fits about 23 source and
7 fixture commits with 0 misses, and also a sample-like mix with 1 miss. The suspect rules are
triage's comment-only skip and the fixture map.

## Final-pick safety

**Final-pick safety requires 0 misses on the held-out set m1** (`validation/sets/m1`). Submissions 2 (`1a5f961`) and
3 (`53eff6e`) miss m1 commit `8owm` (import crash in `dialects/hive.py`), so neither is a safe final pick, even
though both had 0 misses on practice. Never submit a candidate that fails m1.

## Probes retired

The track page shows missed-commit counts per run. Submission 1 had 0 misses, so the comment-skip
and fixture probes below are answered: both rules were safe on practice.

## Queue (next first, historical)

1. **Probe: comment skip off** (`probe/comment-skip-off` `474e945`, sample 828.3 s locally). `main` with AST-identical source edits deferred instead of
   skipped. A rise by a whole multiple of 36.8 s means the skip was safe. A drop of about 300 s,
   or a residual of about 5 s against multiples of 36.8, means the skip missed.
2. **Probe: fixture rules off** (`probe/fixtures-off` `d861da4`, sample 994.5 s locally). Fixture edits run every test. Read it the same way.
3. **Probe: every test on every commit** (`probe/all-tests` `8735070`, sample 1105.0 s locally; optional). Total = 36.817 × N + CPU, which gives N,
   the practice commit count.
4. Best impact candidate from the cloud cluster, once it has 0 misses on held-out data.
