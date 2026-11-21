#!/usr/bin/env python3
"""Build the record-replay map for test_execute_tpcds (strat/r2-replay).

    python build/replay.py --sqlglot DIR --out DIR

1. build/impact_lib/br_replay_rec.py runs the test's work in one process on the pinned
   commit (PYTHONHASHSEED=0) and records per-stage line coverage and every optimizer
   rule call's input (pickled) and output digest.
2. Self-check: blast/replay_child.py replays every recorded call with the pinned code in
   a fresh process. Only stages whose every call reproduces its digest are replayable.
   The sqlglot global state the pinned replay changes must be dict caches; their
   pinned contents are recorded so a patched replay can be compared with them.
3. maps/replay/meta.json: {"test", "stages", "files", "cov_stages", "cov", "modules",
   "caches"}. No meta.json (any failure) means the gate is off.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TEST = "tests/test_executor.py::TestExecutor::test_execute_tpcds"
VERSION = 1


def log(message: str) -> None:
    print(f"[replay] {message}", file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlglot", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    options = parser.parse_args()
    out: Path = options.out
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "meta.json"
    if meta_path.exists():
        meta_path.unlink()
    if os.environ.get("BR_REPLAY", "1") != "1":
        log("disabled")
        return 0
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["SKIP_INTEGRATION"] = "0"
    start = time.time()
    try:
        subprocess.run([sys.executable, "-s", "-P", str(HERE / "impact_lib" / "br_replay_rec.py"),
                        str(options.sqlglot), str(out), str(REPO)],
                       cwd=str(options.sqlglot), env=env, check=True, timeout=300)
    except Exception as exc:  # noqa: BLE001
        log(f"recording failed: {exc!r}; gate off")
        return 0
    with open(out / "rec.pkl", "rb") as f:
        rec = pickle.load(f)
    (out / "rec.pkl").unlink()
    log(f"recorded in {time.time() - start:.1f}s (cpu {rec['cpu']:.1f}s), errors {rec['errors'][:3]}")
    stages = sorted(rec["files"])
    meta = {"version": VERSION, "test": TEST, "stages": stages, "files": rec["files"],
            "modules": rec["modules"], "cov_stages": rec["cov_stages"],
            "cov": {rel: {str(k): v for k, v in lines.items()} for rel, lines in rec["cov"].items()},
            "caches": {}}
    tmp = out / "meta.json.tmp"
    tmp.write_text(json.dumps(meta), "utf-8")
    os.replace(tmp, meta_path)  # the self-check child reads it
    start = time.time()
    try:
        proc = subprocess.run([sys.executable, "-s", "-P", str(REPO / "blast" / "replay_child.py"),
                               str(out), str(options.sqlglot)],
                              input=json.dumps({"selfcheck": True}), text=True,
                              stdout=subprocess.PIPE, env=env, cwd=str(options.sqlglot),
                              check=True, timeout=300)
        check = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        log(f"self-check failed: {exc!r}; gate off")
        meta_path.unlink()
        return 0
    good = [s for s in stages if check["stages"].get(s) == 0]
    log(f"self-check {time.time() - start:.1f}s: {len(good)}/{len(stages)} stages reproduce; "
        f"global changes {check.get('changes')}")
    if check.get("caches") is None:
        log("pinned replay changes non-dict global state; gate off")
        meta_path.unlink()
        return 0
    for s in stages:
        if s not in good:
            (out / rec["files"][s]).unlink()
    meta["stages"] = good
    meta["files"] = {s: rec["files"][s] for s in good}
    meta["caches"] = check["caches"]
    tmp.write_text(json.dumps(meta), "utf-8")
    os.replace(tmp, meta_path)
    for p in out.iterdir():
        os.chmod(p, 0o644)
    return 0


if __name__ == "__main__":
    sys.exit(main())
