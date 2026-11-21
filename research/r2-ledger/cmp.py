"""Compare base vs new selector outputs: cost, misses, per-commit differences."""
import json
import os
import sys

sys.path.insert(0, "D:/blast-radius-cwng/.worktrees/r2-ledger")
S = "C:/Users/carso/.claude/jobs/6eafe579/tmp/r2-ledger"


def load(path):
    out = {}
    for line in open(path):
        o = json.loads(line)
        out[o["commit"]] = o["workers"]
    return out


def cost(workers, dur):
    ws = [w for w in workers if w]
    if not ws:
        return 0
    return max(sum(dur[t] for t in w) for w in ws) + 2000 * len(ws)


for s in sys.argv[1:]:
    tests = json.load(open(f"{S}/sets/{s}/tests.json"))["tests"]
    dur = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    ap = f"{S}/sets/{s}/answers.json"
    ans = json.load(open(ap))["commits"] if os.path.exists(ap) else None
    b, n = load(f"{S}/out/base/{s}.sel"), load(f"{S}/out/new/{s}.sel")
    tb = tn = 0
    mb = mn = 0
    diffs = []
    for c in b:
        cb, cn = cost(b[c], dur), cost(n[c], dur)
        tb += cb
        tn += cn
        sb = {t for w in b[c] for t in w}
        sn = {t for w in n[c] for t in w}
        if ans:
            f = set(ans[c]["failing"])
            mb += bool(f - sb)
            mn += bool(f - sn)
        if sb != sn:
            diffs.append((c, cb, cn, len(sb), len(sn), sn <= sb))
    print(f"{s}: base {tb/1000:.1f} new {tn/1000:.1f} delta {(tn-tb)/1000:+.1f} "
          f"({(tn-tb)/1000/len(b):+.3f}/commit) misses {mb}->{mn} changed {len(diffs)}")
    for d in diffs:
        print("   ", d)
