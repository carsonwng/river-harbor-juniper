"""Layer 4: split the selected tests across at most 8 workers.

Owner: strat/pack.

Cost = busiest worker's load (ms) + 2000 ms per non-empty worker. Input is the
selected test numbers; output is a list of workers.

Algorithm (all integer milliseconds, rounded exactly like score.py):
  * For each worker count k in 1..min(8, n) the cost is bounded below by
    LB(k) = max(largest test, ceil(total / k)) + 2000 k. Worker counts are tried
    in increasing LB order and the search stops once LB(k) >= best cost found.
  * For one k: longest-processing-time greedy, then a local search on the
    busiest worker (best single move, then best pairwise swap found by bisect),
    then, if still above the bound, a node-capped branch and bound
    (complete greedy with symmetry breaking) for small and medium sets.
Nothing here reads answers; it only uses each test's recorded duration.
"""

from __future__ import annotations

from bisect import bisect_left, insort
import math

from .context import Context

MAX_WORKERS = 8
WORKER_COST = 2000          # ms per non-empty worker
LOCAL_ITERS = 200           # cap on local-search improvement steps per k
BNB_MAX_ITEMS = 40          # exact search only for sets this small
BNB_NODES = 20000           # node cap for the exact search, per k


def _ms(seconds: float) -> int:
    return math.floor(seconds * 1000 + 0.5)


def _lpt(items: list[tuple[int, int]], k: int) -> list[list[tuple[int, int]]]:
    """items sorted by duration descending."""
    bins: list[list[tuple[int, int]]] = [[] for _ in range(k)]
    loads = [0] * k
    for item in items:
        j = loads.index(min(loads))
        bins[j].append(item)
        loads[j] += item[0]
    return bins


def _improve(bins: list[list[tuple[int, int]]], bound: int) -> None:
    """Move/swap items off the busiest bin while the sorted load vector shrinks."""
    k = len(bins)
    if k < 2:
        return
    loads = [sum(d for d, _ in b) for b in bins]
    for b in bins:
        b.sort()
    for _ in range(LOCAL_ITERS):
        m = max(range(k), key=loads.__getitem__)
        top = loads[m]
        if top <= bound:
            return
        best = None  # (new pair max, kind, j, i_m, i_j)
        src = bins[m]
        src_d = [d for d, _ in src]
        for j in range(k):
            if j == m:
                continue
            gap = top - loads[j]
            if gap <= 0:
                continue
            # move: item x from m with x < gap; best x close to gap/2
            i = bisect_left(src_d, (gap + 1) // 2)
            for ii in (i - 1, i):
                if 0 <= ii < len(src_d) and 0 < src_d[ii] < gap:
                    x = src_d[ii]
                    new = max(top - x, loads[j] + x)
                    if best is None or new < best[0]:
                        best = (new, 0, j, ii, -1)
            # swap: x in m, y in j with 0 < x - y < gap; best delta ~ gap/2
            dst_d = [d for d, _ in bins[j]]
            if not dst_d:
                continue
            want = gap / 2
            for ii, x in enumerate(src_d):
                # y close to x - want
                t = x - want
                jj = bisect_left(dst_d, t)
                for cand in (jj - 1, jj):
                    if 0 <= cand < len(dst_d):
                        delta = x - dst_d[cand]
                        if 0 < delta < gap:
                            new = max(top - delta, loads[j] + delta)
                            if best is None or new < best[0]:
                                best = (new, 1, j, ii, cand)
        if best is None or best[0] >= top:
            return
        _, kind, j, ii, jj = best
        item = src.pop(ii)
        if kind == 0:
            insort(bins[j], item)
            loads[m] -= item[0]
            loads[j] += item[0]
        else:
            other = bins[j].pop(jj)
            insort(bins[j], item)
            insort(src, other)
            delta = item[0] - other[0]
            loads[m] -= delta
            loads[j] += delta


def _bnb(items: list[tuple[int, int]], k: int, best_max: int, bound: int):
    """Exact-ish: DFS over assignments (items descending), node capped.
    Returns (max_load, assignment) if it beats best_max, else None."""
    n = len(items)
    durs = [d for d, _ in items]
    suffix = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + durs[i]
    loads = [0] * k
    assign = [0] * n
    found = None
    target = best_max  # need strictly below this
    nodes = 0

    def dfs(i: int, cur_max: int) -> bool:
        nonlocal nodes, target, found
        nodes += 1
        if nodes > BNB_NODES:
            return True
        if i == n:
            if cur_max < target:
                target = cur_max
                found = list(assign)
                if target <= bound:
                    return True
            return False
        d = durs[i]
        # remaining work must fit under target on all bins
        if sum(target - 1 - l for l in loads) < suffix[i]:
            return False
        seen = set()
        order = sorted(range(k), key=loads.__getitem__)
        for j in order:
            l = loads[j]
            if l in seen:
                continue  # symmetric bins
            seen.add(l)
            nl = l + d
            if nl >= target:
                continue
            loads[j] = nl
            assign[i] = j
            stop = dfs(i + 1, max(cur_max, nl))
            loads[j] = l
            if stop:
                return True
        return False

    dfs(0, 0)
    if found is None:
        return None
    bins: list[list[tuple[int, int]]] = [[] for _ in range(k)]
    for i, j in enumerate(found):
        bins[j].append(items[i])
    return target, bins


def plan(durations: dict[int, int]) -> list[list[int]]:
    """Best split for {test: ms}. Separated from split() for benchmarking."""
    if not durations:
        return []
    items = sorted(((d, t) for t, d in durations.items()), reverse=True)
    n = len(items)
    total = sum(d for d, _ in items)
    biggest = items[0][0]
    kmax = min(MAX_WORKERS, n)
    lbs = []
    for k in range(1, kmax + 1):
        lb_load = max(biggest, -(-total // k))
        lbs.append((lb_load + WORKER_COST * k, k, lb_load))
    lbs.sort()
    best_cost = None
    best_bins = None
    for lb, k, lb_load in lbs:
        if best_cost is not None and lb >= best_cost:
            break
        bins = _lpt(items, k)
        top = max(sum(d for d, _ in b) for b in bins)
        if top > lb_load:
            _improve(bins, lb_load)
            top = max(sum(d for d, _ in b) for b in bins)
        if top > lb_load and n <= BNB_MAX_ITEMS:
            res = _bnb(items, k, top, lb_load)
            if res is not None:
                top, bins = res
        bins = [b for b in bins if b]
        cost = top + WORKER_COST * len(bins)
        if best_cost is None or cost < best_cost:
            best_cost, best_bins = cost, bins
    return [[t for _, t in b] for b in best_bins]


def split(selected: set[int], ctx: Context) -> list[list[int]]:
    if not selected:
        return []
    return plan({n: _ms(ctx.seconds[n]) for n in selected})
