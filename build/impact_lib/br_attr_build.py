"""Build step for the attribute-read profile (called from build/impact.py).

1. state: pristine import-time fingerprints (br_state.build_state) -> attrs/state.json
2. tracked names (br_state tracked)
3. the instrumented suite pass (br_attr_plugin) -> raw per-worker JSON
4. postprocess into attrs/attrs.json, indexing bitsets in the shared sets.json
5. byte-compile the pinned sqlglot into attrs/pyc (PYTHONPYCACHEPREFIX), so the
   run-time diff imports from bytecode on the read-only disk.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import br_state  # noqa: E402

# State that tests change at run time without caching class data in it:
#   loggers; PEP 562 lazy module attributes (sqlglot.dialects.X, sqlglot.optimizer.X)
#   cached in the module namespace; the dialect registry (_Dialect._classes) that
#   test-defined dialects join; the default schema object tests rebind (its memo
#   caches are emptied before every phase by the plugin); copyreg's __slotnames__.
BENIGN_MODULE_ATTRS = {("sqlglot", "schema")}
LAZY_MODULES = {"sqlglot.dialects", "sqlglot.optimizer"}
BENIGN_CLASS_ATTRS = {("sqlglot.dialects.dialect:_Dialect", "_classes")}


def _benign(change: list) -> bool:
    if change[0] == "module":
        _, mod, attr = change
        return attr == "logger" or mod in LAZY_MODULES or (mod, attr) in BENIGN_MODULE_ATTRS
    if change[0] == "class":
        return change[2] == "__slotnames__" or (change[1], change[2]) in BENIGN_CLASS_ATTRS
    return False


def log(message: str) -> None:
    print(f"[impact-attrs] {message}", file=sys.stderr, flush=True)


def run(root: Path, work: Path, out: Path, env: dict, jobs: int, reuse: bool) -> list[dict] | None:
    adir = out / "attrs"
    adir.mkdir(parents=True, exist_ok=True)
    start = time.time()
    state = br_state.build_state(root, adir / "state.json")
    (adir / "opaque.json").write_text(json.dumps(state["opaque"]), "utf-8")
    log(f"state in {time.time() - start:.1f}s")
    penv = dict(env, PYTHONHASHSEED="0")
    tracked = work / "tracked.json"
    subprocess.run([sys.executable, "-W", "ignore", str(HERE / "br_state.py"), "tracked",
                    "--sqlglot", str(root), "--out", str(tracked)], env=penv, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.copy(tracked, adir / "tracked.json")
    raw = work / "attr"
    if not (reuse and list(raw.glob("attr-*.json"))):
        raw.mkdir(parents=True, exist_ok=True)
        for p in raw.glob("attr-*.json"):
            p.unlink()
        start = time.time()
        penv.update(BR_ATTR_ROOT=str(root), BR_ATTR_OUT=str(raw), BR_ATTR_TRACKED=str(tracked))
        command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "br_attr_plugin",
                   "--assert=plain", "-q", "-n", str(jobs), "--dist", "load", "tests"]
        proc = subprocess.run(command, cwd=str(root), env=penv, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors="replace")
        tail = "\n".join(proc.stdout.strip().splitlines()[-3:])
        log(f"profile pytest exit {proc.returncode} in {time.time() - start:.1f}s: {tail}")
    workers = [json.loads(p.read_text("utf-8")) for p in sorted(raw.glob("attr-*.json"))]
    start = time.time()
    pyc = adir / "pyc"
    if pyc.exists():
        shutil.rmtree(pyc)
    subprocess.run([sys.executable, "-m", "compileall", "-q", str(root / "sqlglot")],
                   env=dict(env, PYTHONPYCACHEPREFIX=str(pyc.resolve()), PYTHONDONTWRITEBYTECODE=""),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f"pyc in {time.time() - start:.1f}s")
    return workers or None


class Sets:
    """Deduplicated bitsets over the coverage pass's test order, stored sparse
    (a list of bit positions) when that is shorter than hex."""

    def __init__(self) -> None:
        self.index: dict[int, int] = {}
        self.items: list[int] = []

    def add(self, bits: int) -> int:
        got = self.index.get(bits)
        if got is None:
            got = self.index[bits] = len(self.items)
            self.items.append(bits)
        return got

    def dump(self) -> list:
        out = []
        for bits in self.items:
            if bits.bit_count() * 5 < bits.bit_length() // 4:
                pos, b = [], bits
                while b:
                    low = b & -b
                    pos.append(low.bit_length() - 1)
                    b ^= low
                out.append(pos)
            else:
                out.append(format(bits, "x"))
        return out


def postprocess(out: Path, workers: list[dict] | None, pos: dict[str, int], by_class: dict,
                by_module: dict, ALL: int, _shared_sets=None) -> None:
    adir = out / "attrs"
    sets = Sets()
    result: dict = {"ok": False}
    if not workers:
        (adir / "attrs.json").write_text(json.dumps(result), "utf-8")
        log("no profile results: run-time diffing disabled")
        return
    attrs: dict[str, int] = {}
    keys: dict[str, dict[str, int]] = {}
    star: dict[str, int] = {}
    iters: dict[str, int] = {}
    tables: dict[str, str] = {}
    seen: set[str] = set()
    unsure: set[str] = set()
    problems: list = []
    for w in workers:
        wtests = w["tests"]
        expand: dict[int, int] = {}

        def bits_for(code: int) -> int:
            got = expand.get(code)
            if got is None:
                if code < 0:
                    got = ALL
                else:
                    idx, phase = divmod(code, 4)
                    key = wtests[idx]
                    if phase == 3:
                        got = by_module.get(key[len("collect::"):], 0)
                    elif key not in pos:
                        got = ALL  # a test the coverage pass did not see: be safe
                    elif phase == 1:
                        got = 1 << pos[key]
                    else:
                        got = by_class.get(key.rsplit("::", 1)[0], 0) | (1 << pos[key])
                expand[code] = got
            return got

        def acc(codes) -> int:
            b = 0
            for c in codes:
                b |= bits_for(c)
            return b

        for k, codes in w["attrs"].items():
            attrs[k] = attrs.get(k, 0) | acc(codes)
        for tid, per in w["keys"].items():
            dst = keys.setdefault(tid, {})
            for kfp, codes in per.items():
                dst[kfp] = dst.get(kfp, 0) | acc(codes)
        for tid, codes in w["star"].items():
            star[tid] = star.get(tid, 0) | acc(codes)
        for tid, codes in w["iters"].items():
            iters[tid] = iters.get(tid, 0) | acc(codes)
        wt = {}
        for tid, names in w["tables"].items():
            for ckey, attr in names:
                wt[f"{ckey}|{attr}"] = tid
        if tables and wt != tables:
            problems.append("table ids differ between workers")
        tables = tables or wt
        for t in wtests:
            if not t.startswith("collect::"):
                seen.add(t)
        for t, outcome in w["outcomes"].items():
            if outcome not in ("passed", "skipped"):
                unsure.add(t)
        for change in w["runtime_changes"]:
            if not _benign(change):
                problems.append(change)
        if w.get("leaky"):
            problems.append(["leaky", w["leaky"]])
    missing = sorted(t for t in pos if t not in seen)
    unsure |= set(missing)
    usure_bits = 0
    for t in unsure:
        if t in pos:
            usure_bits |= 1 << pos[t]
    result = {
        "ok": not problems,
        "problems": problems[:50],
        "unsure": sets.add(usure_bits),
        "attrs": {k: sets.add(v) for k, v in sorted(attrs.items())},
        "keys": {t: {k: sets.add(v) for k, v in per.items()} for t, per in keys.items()},
        "star": {t: sets.add(v) for t, v in star.items()},
        "iters": {t: sets.add(v) for t, v in iters.items()},
        "tables": tables,
    }
    (adir / "attrs.json").write_text(json.dumps(result, separators=(",", ":")), "utf-8")
    (adir / "sets.json").write_text(json.dumps(sets.dump(), separators=(",", ":")), "utf-8")
    log(f"profile: {len(attrs)} attrs, {len(keys)} tables looked up, {len(unsure)} unsure "
        f"({len(missing)} unseen), problems: {problems[:5]}")
