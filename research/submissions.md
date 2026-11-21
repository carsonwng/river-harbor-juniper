# Practice submissions

Limit: one submission per hour. Track page: not committed (ask the human).

| # | Date | Commit | Contents | Sample (docker) | Practice board | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-11-20 | `a9e62c0` | pack + triage, impact at its default (all tests) | 720.2 s, 0 misses | **868.3 s** | Unknown whether this includes a miss (see below) |

## Reading submission 1

Practice commits cost about 36.8 s (source edit), 2 to 3 s (fixture) or 0 s (comment-only
skip), and a miss adds 300 s. Without the commit count or mix, 868.3 s fits about 23 source and
7 fixture commits with 0 misses, and also a sample-like mix with 1 miss. The suspect rules are
triage's comment-only skip and the fixture map.

## Queue (next first)

1. **Probe: comment skip off.** `main` with AST-identical source edits deferred instead of
   skipped. A rise by a whole multiple of 36.8 s means the skip was safe. A drop of about 300 s,
   or a residual of about 5 s against multiples of 36.8, means the skip missed.
2. **Probe: fixture rules off.** Fixture edits run every test. Read it the same way.
3. **Probe: every test on every commit** (optional). Total = 36.817 × N + CPU, which gives N,
   the practice commit count.
4. Best impact candidate from the cloud cluster, once it has 0 misses on held-out data.
