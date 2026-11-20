"""Offline benchmark for blast/pack.py (may read answers; pack.py never does).

Scores pack.plan() on realistic subsets and compares to the lower bound
min_k max(largest, ceil(total/k)) + 2000k, and to the old fixed-3 LPT default.
Usage: python harness/pack_bench.py [--data sample]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from blast import pack  # noqa: E402


def ms(s: float) -> int:
    return math.floor(s * 1000 + 0.5)


def cost(workers, dur) -> int:
    workers = [w for w in workers if w]
    if not workers:
        return 0
    return max(sum(dur[t] for t in w) for w in workers) + 2000 * len(workers)


def lower_bound(sel, dur) -> int:
    if not sel:
        return 0
    ds = [dur[t] for t in sel]
    tot, big = sum(ds), max(ds)
    return min(max(big, -(-tot // k)) + 2000 * k for k in range(1, min(8, len(ds)) + 1))


def old_split(sel, dur):
    count = min(3, len(sel))
    ws = [[] for _ in range(count)]
    loads = [0] * count
    for t in sorted(sel, key=lambda n: dur[n], reverse=True):
        j = loads.index(min(loads))
        ws[j].append(t)
        loads[j] += dur[t]
    return ws


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "sample"))
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    data = Path(args.data)
    tests = json.loads((data / "tests.json").read_text("utf-8"))["tests"]
    dur = {t["number"]: ms(t["seconds"]) for t in tests}
    by_file: dict[str, list[int]] = {}
    for t in tests:
        by_file.setdefault(t["file"], []).append(t["number"])
    answers = json.loads((data / "answers.json").read_text("utf-8"))["commits"]
    rng = random.Random(args.seed)
    allt = list(dur)

    suites: dict[str, list[set[int]]] = {}
    suites["failing"] = [set(v["failing"]) for v in answers.values() if v["failing"]]
    suites["failing+pad"] = [
        set(v["failing"]) | set(rng.sample(allt, rng.choice([5, 20, 50, 200, 600])))
        for v in answers.values()
    ]
    suites["files"] = [set(v) for v in by_file.values()]
    suites["file-unions"] = [
        set().union(*(by_file[f] for f in rng.sample(list(by_file), rng.randint(2, 8))))
        for _ in range(30)
    ]
    suites["random"] = [set(rng.sample(allt, rng.randint(1, 1620))) for _ in range(30)]
    suites["full"] = [set(allt)]

    grand = {"lb": 0, "new": 0, "old": 0}
    worst_call = 0.0
    calls = 0
    total_time = 0.0
    print(f"{'suite':12} {'n':>4} {'lower':>10} {'new':>10} {'gap':>7} {'old3':>10}")
    for name, sets in suites.items():
        lb = new = old = 0
        for sel in sets:
            t0 = time.perf_counter()
            ws = pack.plan({t: dur[t] for t in sel})
            dt = time.perf_counter() - t0
            worst_call = max(worst_call, dt)
            total_time += dt
            calls += 1
            flat = [t for w in ws for t in w]
            assert sorted(flat) == sorted(sel) and len(ws) <= 8, name
            lb += lower_bound(sel, dur)
            new += cost(ws, dur)
            old += cost(old_split(sel, dur), dur)
        grand["lb"] += lb
        grand["new"] += new
        grand["old"] += old
        print(f"{name:12} {len(sets):4} {lb/1000:10.3f} {new/1000:10.3f} {(new-lb)/1000:7.3f} {old/1000:10.3f}")
    print(f"{'TOTAL':12} {calls:4} {grand['lb']/1000:10.3f} {grand['new']/1000:10.3f} "
          f"{(grand['new']-grand['lb'])/1000:7.3f} {grand['old']/1000:10.3f}")
    print(f"cpu: {total_time*1000:.1f} ms over {calls} calls, mean {total_time/calls*1000:.2f} ms, "
          f"worst {worst_call*1000:.2f} ms")


if __name__ == "__main__":
    main()
