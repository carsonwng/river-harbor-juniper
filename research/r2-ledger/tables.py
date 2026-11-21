import json
import re
import sys
from collections import defaultdict

S = sys.argv[1]
R = json.load(open(S + "/out/rows.json"))
SETS = ["m1", "m1-extra", "local-20261120", "syn640", "sample"]
CORE = ("sqlglot/tokens.py", "sqlglot/trie.py", "sqlglot/tokenizer_core.py", "sqlglot/__main__.py",
        "sqlglot/typing/__init__.py")


def fam(key):
    f, r = key.split("|", 1)
    r = re.sub(r"stmt:\w+", "stmt", r)
    if r.startswith("triage:"):
        r = "triage:" + ("fixture" if "fixture" in r else "testfn" if "test functions" in r else r[7:40])
    tag = r
    if f in CORE:
        tag = "CORE-FILE " + r
    return tag


def area(f):
    if not f.startswith("sqlglot/"):
        return "tests"
    p = f[8:]
    return p.split("/")[0] if "/" in p else p


def table(title, getter):
    tot = {s: defaultdict(int) for s in SETS}
    cnt = {s: defaultdict(int) for s in SETS}
    for s in SETS:
        for x in R[s]:
            for k, v in getter(x).items():
                tot[s][k] += v
                if v:
                    cnt[s][k] += 1
    keys = set()
    for s in SETS:
        keys |= set(tot[s])
    n = {s: len(R[s]) for s in SETS}
    def score(k):  # mean per-commit over practice-like sets
        return sum(tot[s][k] / n[s] for s in ["m1", "m1-extra", "local-20261120"]) / 3
    print(f"\n## {title} (ms per commit; [commits hit])")
    print("| key | m1 | m1-extra | local | syn640 | sample | mean(m1,m1x,local) |")
    print("|---|---|---|---|---|---|---|")
    for k in sorted(keys, key=lambda k: -score(k)):
        if score(k) < 20 and tot["syn640"][k] / n["syn640"] < 20:
            continue
        cells = [f"{tot[s][k]/n[s]:.0f} [{cnt[s][k]}]" for s in SETS]
        print(f"| {k} | " + " | ".join(cells) + f" | {score(k):.0f} |")


def by_rule(x):
    out = defaultdict(int)
    for k, v in x["margin"].items():
        out[fam(k)] = max(out[fam(k)], v)
    return out


def by_rule_area(x):
    out = defaultdict(int)
    for k, v in x["margin"].items():
        f = k.split("|", 1)[0]
        key = area(f) + " :: " + fam(k)
        out[key] = max(out[key], v)
    return out


def by_area(x):
    if x["excess"] <= 0:
        return {}
    a = "+".join(sorted({area(f[0]) for f in x["files"]}))
    return {a: x["excess"]}


def heavy(x):
    d = dict(x["heavy"])
    d["ALL5 together"] = x["heavy_all"]
    return d


def busiest(x):
    names = {1106: "tpcds", 1490: "max_nodes", 1224: "gen_memory", 1107: "tpch", 1123: "opt_tpch"}
    if x["excess"] <= 0 or not x["busiest"]:
        return {}
    return {names.get(x["busiest"][0], "other"): x["excess"]}


def heavy_rule(x):
    """which rule selected the heavy tests (by excess when heavy is in busiest)."""
    return {}


table("Excess by rule family: marginal saving if this rule selected only failing tests", by_rule)
table("Excess by area :: rule", by_rule_area)
table("Excess by edited area (whole commit excess)", by_area)
table("Heavy tests: saving from dropping each (when not failing)", heavy)
table("Excess by test that tops the busiest worker", busiest)
