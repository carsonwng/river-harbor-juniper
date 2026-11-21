# Practice submissions

Limit: one submission per hour. Track page: not committed (ask the human).

| # | Date | Commit | Contents | Sample (docker) | Practice board | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-11-20 | `a9e62c0` | pack + triage, impact at its default (all tests) | 720.2 s, 0 misses | **868.3 s** | Runs: 867.471 s sim, 0.82 s CPU, **0 missed** (from the track page) |
| 2 | 2026-11-20 20:18 | `1a5f961` | `int/night-1948`: main + strat/impact-base (provisional, sample only) | 178.9 s, 0 misses | pending | receipt BB-69453A60 |

## Reading submission 1

Practice commits cost about 36.8 s (source edit), 2 to 3 s (fixture) or 0 s (comment-only
skip), and a miss adds 300 s. Without the commit count or mix, 868.3 s fits about 23 source and
7 fixture commits with 0 misses, and also a sample-like mix with 1 miss. The suspect rules are
triage's comment-only skip and the fixture map.

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
