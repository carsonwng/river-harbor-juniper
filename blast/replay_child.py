"""Replay recorded optimizer-rule calls on patched sqlglot (strat/r2-replay).

    python -I replay_child.py MAPDIR SQLGLOT < {"patches": {rel: text}, "stages": [...],
                                                "selfcheck": false}

Runs with PYTHONHASHSEED=0 (the recording's seed: the inputs carry cached
Expression hashes). The edited modules are compiled from the given sources through
a meta-path finder; everything else is the pinned checkout. Every recorded call of
the given stages is unpickled, run with the (patched) rule, and the pickle digest of
(return value, arguments after the call) is compared with the recorded one. sqlglot's
global state (module and class namespaces) is fingerprinted before and after; only
the caches the pinned replay also fills may change, and their contents must match
the pinned contents. Prints one JSON object on stdout. Any doubt -> "same": false.
"""

import importlib
import importlib.abc
import importlib.util
import json
import os
import pickle
import sys
import time
import zlib


def main() -> None:
    mapdir, sg = sys.argv[1], sys.argv[2]
    req = json.loads(sys.stdin.read())
    patches = req.get("patches", {})
    selfcheck = bool(req.get("selfcheck"))
    sys.stdout = sys.stderr  # nothing but the verdict on the real stdout
    out = sys.__stdout__
    sys.dont_write_bytecode = True
    sys.path.insert(0, sg)
    sys.path.insert(1, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    class Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, name, path, target=None):
            base = name.replace(".", "/")
            for rel, pkg in ((base + ".py", False), (base + "/__init__.py", True)):
                if rel in patches:
                    origin = os.path.join(sg, rel)
                    return importlib.util.spec_from_file_location(
                        name, origin, loader=self,
                        submodule_search_locations=[os.path.dirname(origin)] if pkg else None)
            return None

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            rel = os.path.relpath(module.__spec__.origin, sg).replace(os.sep, "/")
            exec(compile(patches[rel], module.__spec__.origin, "exec", dont_inherit=True),
                 module.__dict__)

    sys.meta_path.insert(0, Finder())
    t0 = time.process_time()
    meta = json.load(open(os.path.join(mapdir, "meta.json"), encoding="utf-8"))

    from blast import replaylib as R

    for name in meta["modules"]:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001  (pinned failures are recorded as such)
            if name not in meta.get("module_errors", []):
                raise
    from sqlglot.optimizer import optimizer as O

    rules = {"rule:" + r.__name__: r for r in O.RULES}
    stages = req["stages"] if not selfcheck else meta["stages"]
    g0 = R.gstate_snapshot()
    result = {"same": True, "calls": 0, "stages": {}}
    if os.environ.get("PYTHONHASHSEED") != "0" or sys.flags.hash_randomization:
        stages = []  # the recorded inputs carry seed-0 string hashes
        result.update(same=False, reason="hash seed is not 0")
    for stage in stages:
        rule = rules.get(stage)
        if rule is None:
            result.update(same=False, reason=f"unknown stage {stage}")
            break
        with open(os.path.join(mapdir, meta["files"][stage]), "rb") as f:
            records = pickle.load(f)
        bad = 0
        for q, blob, digest in records:
            result["calls"] += 1
            try:
                args = R.loads(zlib.decompress(blob))
                ret = rule(**args)
                ok = R.odigest((ret, args)) == digest
            except Exception as exc:  # noqa: BLE001
                ok = False
                if not selfcheck:
                    result["reason"] = f"{stage} q{q}: {type(exc).__name__}"
            if not ok:
                bad += 1
                result["same"] = False
                result.setdefault("reason", f"{stage} q{q}: output differs")
                if not selfcheck:
                    break
        result["stages"][stage] = bad
        if not result["same"] and not selfcheck:
            break
    if result["same"] or selfcheck:
        changes = R.gstate_changes(g0, R.gstate_snapshot())
        if selfcheck:
            result["changes"] = [repr(c) for c in changes]
            result["caches"] = R.cache_digests(changes)
        else:
            allowed = meta.get("caches") or {}
            for c in changes:
                pinned = allowed.get(repr(c)) if c[0] != "+" else None
                if pinned is None:
                    result.update(same=False, reason=f"global state changed: {c!r}")
                    break
                now = R.cache_digests([c])
                if now is None or any(pinned.get(k) != v for k, v in now[repr(c)].items()):
                    result.update(same=False, reason=f"cache differs: {c!r}")
                    break
    result["cpu"] = round(time.process_time() - t0, 3)
    out.write(json.dumps(result) + "\n")
    out.flush()


if __name__ == "__main__":
    main()
