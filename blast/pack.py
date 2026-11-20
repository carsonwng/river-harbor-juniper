"""Layer 4: split the selected tests across at most 8 workers.

Owner: strat/pack.

Cost = busiest worker's load (ms) + 2000 ms per non-empty worker. Input is the
selected test numbers; output is a list of workers. Default: longest-first greedy
onto 3 workers (the starter's split).
"""

from __future__ import annotations

from .context import Context

MAX_WORKERS = 8
WORKERS = 3


def split(selected: set[int], ctx: Context) -> list[list[int]]:
    if not selected:
        return []
    count = min(WORKERS, len(selected))
    workers: list[list[int]] = [[] for _ in range(count)]
    loads = [0.0] * count
    for number in sorted(selected, key=lambda n: ctx.seconds[n], reverse=True):
        lightest = loads.index(min(loads))
        workers[lightest].append(number)
        loads[lightest] += ctx.seconds[number]
    return workers
