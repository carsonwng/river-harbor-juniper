# strat/pack: optimal worker count and split

Layer: `blast/pack.py` only. Signature `split(selected, ctx)` unchanged; `plan({test: ms})`
is the pure core (used by `harness/pack_bench.py`).

## Hypothesis
Cost = max load + 2000 ms x workers. For k workers the cost is bounded below by
LB(k) = max(largest test, ceil(total/k)) + 2000k. Choosing k per commit and packing close
to that bound beats the fixed 3-worker LPT. The test durations are skewed (largest 28.8 s,
next 21.5 s, total 103 s), so fewer workers often win, and when one test dominates,
extra workers are pure cost.

## Algorithm
1. Durations are integer ms, rounded like score.py (floor(s*1000+0.5)).
2. Try k = 1..min(8, n) in increasing LB(k) order. Stop when LB(k) >= the best cost found.
3. For each k: LPT, then a local search on the busiest bin (best move, or best swap found
   by bisect so it stays O(n log n) per step, capped at 200 steps). If the load is still
   above the bound and n <= 40, run a branch and bound over items in descending order
   (bins with equal loads treated as one, capacity pruning, capped at 20k nodes).
4. Empty bins are dropped. Nothing reads answers; this layer needs no maps.

## Measurements (local only, Docker not running)
`python harness/pack_bench.py` (sample tests, seed 1, 167 subsets):

| suite | n | lower bound (s) | new (s) | old fixed-3 (s) |
| --- | --- | --- | --- | --- |
| failing sets | 18 | 94.351 | 94.351 | 105.138 |
| failing + random pad | 30 | 343.586 | 343.586 | 420.571 |
| single test files | 58 | 201.724 | 201.724 | 375.643 |
| unions of 2-8 files | 30 | 190.516 | 190.516 | 278.337 |
| random subsets | 30 | 827.467 | 827.467 | 863.431 |
| full set | 1 | 36.817 | 36.817 | 40.349 |

Every case reaches the lower bound, so each split is provably optimal. CPU is 0.13 ms
mean and 1.4 ms worst per call.
Stress test: 150 random sets of 2-7 of the 60 largest tests match brute force exactly.
100 sets of 10-60 big tests come out 48 ms total above LB (the bound is not always
reachable), at about 2 ms per call.

`br.py eval -v` (all tests selected, since the other layers are still defaults): full set
36.817 s/commit on 4 workers, total 1104.989 s vs the 1210 s baseline.

## Dead ends / notes
- No pack-level tricks beyond this: the bound is already reached on realistic sets, so the
  remaining gains belong to selection.
