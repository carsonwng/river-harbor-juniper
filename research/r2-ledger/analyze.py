"""Ledger analysis (host). usage: python analyze.py SETS_DIR OUT_DIR set1 set2 ..."""
import json
import sys, os
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "D:/blast-radius-cwng/.worktrees/r2-ledger")
from blast import pack  # noqa: E402

HEAVY = {1106: "tpcds", 1490: "max_nodes", 1224: "gen_memory", 1107: "tpch", 1123: "opt_tpch"}


def cost(sel, dur):
    if not sel:
        return 0
    w = pack.plan({t: dur[t] for t in sel})
    w = [x for x in w if x]
    return max(sum(dur[t] for t in x) for x in w) + 2000 * len(w), w


def c(sel, dur):
    return cost(sel, dur)[0] if sel else 0


def area(path):
    if not path.startswith("sqlglot/"):
        return "tests/" + ("fixtures" if "fixtures" in path else "py")
    p = path[len("sqlglot/"):]
    if "/" in p:
        top = p.split("/")[0]
        if top in ("dialects", "parsers", "generators", "optimizer", "executor", "expressions"):
            return top
        return top
    return p  # top-level module


def rule_family(f, r):
    if r.startswith("triage:"):
        return "triage"
    return r


def main():
    sets_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    report = {}
    for name in sys.argv[3:]:
        tests = json.loads((sets_dir / name / "tests.json").read_text())["tests"]
        dur = {t["number"]: round(t["seconds"] * 1000) for t in tests}
        allt = set(dur)
        ans_p = sets_dir / name / "answers.json"
        answers = json.loads(ans_p.read_text())["commits"] if ans_p.exists() else None
        led = json.loads((out_dir / f"{name}.json").read_text())
        sel_lines = {}
        selp = out_dir / f"{name}.sel"
        if selp.exists():
            for line in selp.read_text().splitlines():
                o = json.loads(line)
                sel_lines[o["commit"]] = {t for w in o["workers"] for t in w}
        rows = []
        for commit, e in led.items():
            S = set(e["selected"])
            fail0 = set(answers[commit]["failing"]) if answers else set()
            if os.environ.get("NO_TPCDS") and 1106 not in fail0:
                S.discard(1106)
                e["tags"] = [[f, r, (sorted(allt - {1106}) if ts == "ALL" else [t for t in ts if t != 1106])] for f, r, ts in e["tags"]]
            if sel_lines and not os.environ.get("NO_TPCDS") and sel_lines[commit] != S:
                print("MISMATCH", name, commit, len(S), len(sel_lines[commit]))
            fail = set(answers[commit]["failing"]) if answers else set()
            miss = bool(fail - S)
            ours = c(S, dur)
            orac = c(fail, dur) if answers else 0
            # per-rule test sets
            by_rule = defaultdict(set)
            for f, r, ts in e["tags"]:
                if r == "bits==ALL":
                    continue
                ts = allt if ts == "ALL" else set(ts)
                by_rule[(f, r)] |= ts
            # marginal saving of each rule: drop its tests that no other rule gives, keep failing
            margin = {}
            for key, ts in by_rule.items():
                if not ts:
                    continue
                others = set()
                for k2, t2 in by_rule.items():
                    if k2 != key:
                        others |= t2
                if key[0] == "*" and key[1].startswith("floor"):
                    pass
                S2 = (S & others) | (S & fail) | (ts & fail)
                # tests in S not explained by any rule stay
                explained = set().union(*by_rule.values())
                S2 |= S - explained
                margin["%s|%s" % key] = ours - c(S2, dur)
            # all-tests rule (ALL tag) -> marginal of the whole ALL fallback
            heavy = {}
            for t, nm in HEAVY.items():
                if t in S and t not in fail:
                    heavy[nm] = ours - c(S - {t}, dur)
            # heavy set together
            hv = {t for t in HEAVY if t in S and t not in fail}
            heavy_all = ours - c(S - hv, dur) if hv else 0
            busiest = []
            if S:
                _, w = cost(S, dur)
                top = max(w, key=lambda x: sum(dur[t] for t in x))
                busiest = sorted(top, key=lambda t: -dur[t])[:3]
            rows.append({"commit": commit, "ours": ours, "oracle": orac, "excess": ours - orac,
                         "miss": miss, "n": len(S), "nfail": len(fail),
                         "areas": sorted({area(f[0]) for f in e["files"]}),
                         "files": e["files"], "margin": margin, "heavy": heavy,
                         "heavy_all": heavy_all, "busiest": busiest,
                         "is_all": len(S) == len(allt), "secs": e["secs"],
                         "rules": sorted({"%s|%s" % k for k in by_rule})})
        report[name] = rows
    (out_dir / "rows.json").write_text(json.dumps(report, indent=0))


if __name__ == "__main__":
    main()
