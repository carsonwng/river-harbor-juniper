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

## Recommended next submission (2026-11-21 cloud run)

**`strat/impact-combo-sound` at `7a82a66`** ([PR 6](https://github.com/carsonwng/river-harbor-juniper/pull/6)),
after two gates:

1. `python harness/br.py eval --docker` passes with 0 misses (never run in Docker so far: the
   build time and the run-time subprocess are unverified under the scorer's limits).
2. The relprof rule decision (`open-questions.md` Q7). If relprof is not allowed or the answer
   is unknown, submit the same commit with `ENV BR_IMPACT_RELPROF=0` in the Dockerfile.

Local numbers: sample 103.6 s (156.3 s without relprof), held-out m1 570.3 s, 0 misses on both.
Expected on the practice board, if its mix is like submission 1's: well below 868.3 s, because
source commits drop from 36.8 s each to a few seconds for most.

## Queue after that (next first)

1. **Same commit with the import-check fallback changed to every test**, once that fix is on
   the branch. It removes the one known unsound fallback.
2. **Probe: comment skip off** on top of the combo. Still the cheapest way to answer Q1.
3. **Probe: fixture rules off** on top of the combo, read the same way.

The earlier queue (probes on `main` alone) is replaced by the above. The every-test probe stays
optional.
