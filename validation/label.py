"""Label patches by running the full sqlglot suite the way the organisers did.

    python validation/label.py --set SETDIR [--copies 3 --workers 5] [--src pending|commits]
    python validation/label.py --set SETDIR --finalize

For each SETDIR/<src>/<id>.patch: apply it to a scratch clone of the pinned
sqlglot (`.cache/mutants/work/s<k>`), run
  SKIP_INTEGRATION=0 PYTHONPATH=<clone> python -m pytest tests -n W --timeout T
with the venv built from requirements.lock, and record the failing node ids
(failure, error, timeout, crashed worker, a failing subtest -> its parent, or a
test file that cannot be collected -> every test of that file). Results go to
SETDIR/labels/<id>.json. --finalize turns labels into the harness format:
SETDIR/commits/<id>.patch, SETDIR/tests.json, SETDIR/answers.json.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # worktree root
MAIN = Path(subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute",
                            "--git-common-dir"], capture_output=True, text=True).stdout.strip()).parent
CACHE = MAIN / ".cache"
WORK = CACHE / "mutants" / "work"
SAMPLE_TESTS = MAIN / "sample" / "tests.json"


def venv_python() -> Path:
    for p in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if p.exists():
            return p
    sys.exit("no .venv; run: python -m venv .venv && .venv/bin/pip install -r requirements.lock")


def git(repo: Path, *args: str, check=True, input=None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=check, capture_output=True,
                          text=True, encoding="utf-8", input=input)


def ensure_copy(k: int) -> Path:
    d = WORK / f"s{k}"
    if not d.exists():
        WORK.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--local", str(CACHE / "sqlglot"), str(d)], check=True)
    reset(d)
    # stale bytecode could hide an edit that keeps size and mtime second; never write any
    for pyc in d.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    return d


def reset(d: Path) -> None:
    git(d, "checkout", "-q", "--", ".")
    git(d, "clean", "-fdq", "-e", "__pycache__")


def run_suite(d: Path, workers: int, timeout: int, report: Path,
              targets: list[str] | None = None) -> tuple[int, float]:
    env = dict(os.environ)
    env.update(SKIP_INTEGRATION="0", PYTHONDONTWRITEBYTECODE="1", BR_REPORT=str(report), BR_TESTS=str(SAMPLE_TESTS),
               PYTHONPATH=os.pathsep.join([str(d), str(HERE)]))
    env.pop("PYTHONHOME", None)
    if report.exists():
        report.unlink()
    t0 = time.time()
    cmd = [str(venv_python()), "-m", "pytest", *(targets or ["tests"]), "-q", "-p", "br_report",
           "-p", "no:cacheprovider", f"--timeout={timeout}", "-rN"]
    if workers > 1:
        cmd += ["-n", str(workers)]
    proc = subprocess.run(cmd, cwd=d, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout * 6 + 600)
    (report.with_suffix(".out")).write_text(proc.stdout[-20000:] + "\n--stderr--\n" + proc.stderr[-5000:],
                                            "utf-8")
    return proc.returncode, time.time() - t0


def parse_report(report: Path, tests: list[dict]):
    failing_ids: set[str] = set()
    timeouts: set[str] = set()
    collect_errors: list[str] = []
    passed: set[str] = set()
    for line in report.read_text("utf-8").splitlines() if report.exists() else []:
        rec = json.loads(line)
        if rec["t"] == "fail":
            failing_ids.add(rec["id"])
            if rec.get("timeout"):
                timeouts.add(rec["id"])
        elif rec["t"] == "collect":
            collect_errors.append(rec["id"])
        else:
            passed.add(rec["id"])
    return failing_ids, timeouts, collect_errors, passed


def to_numbers(failing_ids, collect_errors, passed, tests) -> dict:
    by_id = {t["id"]: t["number"] for t in tests}
    by_file: dict[str, list[int]] = {}
    for t in tests:
        by_file.setdefault(t["file"], []).append(t["number"])
    numbers: set[int] = set()
    unknown: list[str] = []
    for nid in failing_ids:
        if nid in by_id:
            numbers.add(by_id[nid])
        else:
            unknown.append(nid)
    for cid in collect_errors:
        f = cid.split("::")[0]
        hit = [n for file, ns in by_file.items() if file == f or file.startswith(f.rstrip("/") + "/")
               for n in ns]
        numbers.update(hit)
        if not hit:
            unknown.append("collect:" + cid)
    seen = len({i for i in passed | failing_ids if i in by_id})
    return {"failing": sorted(numbers), "unknown": sorted(unknown),
            "collect_errors": collect_errors, "seen": seen}


def label_one(d: Path, patch: Path, tests, workers, timeout, labels: Path) -> dict:
    reset(d)
    ap = git(d, "apply", "--whitespace=nowarn", str(patch), check=False)
    if ap.returncode:
        return {"error": "apply failed: " + ap.stderr.strip()}
    report = labels / f"{patch.stem}.report.jsonl"
    try:
        rc, secs = run_suite(d, workers, timeout, report)
        failing, timeouts, collect_errors, passed = parse_report(report, tests)
        # Timeouts and crashed workers under a loaded machine are often just slowness:
        # rerun those tests alone with a 3x budget and keep only those that fail again.
        retried = sorted(timeouts)
        if retried:
            rep2 = labels / f"{patch.stem}.retry.jsonl"
            run_suite(d, 1, timeout * 3, rep2, targets=retried)
            f2, _, _, p2 = parse_report(rep2, tests)
            failing = (failing - timeouts) | (f2 & timeouts)
            passed |= p2
    finally:
        reset(d)
    res = to_numbers(failing, collect_errors, passed, tests)
    res.update(rc=rc, seconds=round(secs, 1), timeouts_retried=retried)
    # a complete run reports every numbered test, unless a whole file failed to import
    if rc not in (0, 1) or (res["seen"] < len(tests) and not res["collect_errors"]):
        res["incomplete"] = True
    return res


def label(opts) -> None:
    setdir = Path(opts.set).resolve()
    src = setdir / opts.src
    labels = setdir / "labels"
    labels.mkdir(exist_ok=True)
    tests = json.loads((Path(opts.tests) if opts.tests else SAMPLE_TESTS).read_text("utf-8"))["tests"]
    todo = sorted(p for p in src.glob("*.patch")
                  if opts.redo or not (labels / f"{p.stem}.json").exists())
    if opts.only:
        keep = set(opts.only.split(","))
        todo = [p for p in todo if p.stem in keep]
    print(f"{len(todo)} patches to label with {opts.copies} copies x {opts.workers} workers",
          file=sys.stderr)
    q: queue.Queue = queue.Queue()
    for p in todo:
        q.put(p)
    lock = threading.Lock()
    t_start = time.time()
    done = [0]

    def worker(k: int):
        d = ensure_copy(k)
        while True:
            try:
                p = q.get_nowait()
            except queue.Empty:
                return
            meta_path = p.with_suffix(".json")
            meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
            res = label_one(d, p, tests, opts.workers, opts.timeout, labels)
            res["kind"] = meta.get("kind", res.get("kind"))
            res["edits"] = meta.get("edits")
            (labels / f"{p.stem}.json").write_text(json.dumps(res, indent=1), "utf-8")
            with lock:
                done[0] += 1
                el = time.time() - t_start
                print(f"[{done[0]}/{len(todo)} {el:.0f}s] {p.stem} {res.get('kind')} "
                      f"fail={len(res.get('failing', []))} unknown={len(res.get('unknown', []))} "
                      f"{res.get('seconds')}s rc={res.get('rc')}"
                      f"{' INCOMPLETE' if res.get('incomplete') else ''}{' ' + res['error'] if 'error' in res else ''}",
                      file=sys.stderr, flush=True)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(opts.copies)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    n = max(done[0], 1)
    print(f"labelled {done[0]} in {time.time() - t_start:.0f}s "
          f"({(time.time() - t_start) / n:.1f}s per commit wall)", file=sys.stderr)


def finalize(opts) -> None:
    setdir = Path(opts.set).resolve()
    labels = setdir / "labels"
    commits = setdir / "commits"
    commits.mkdir(exist_ok=True)
    answers = {}
    skipped = []
    for lp in sorted(labels.glob("*.json")):
        if lp.name.endswith(".report.jsonl"):
            continue
        res = json.loads(lp.read_text("utf-8"))
        cid = lp.stem
        src = next((p for p in (setdir / "pending" / f"{cid}.patch", commits / f"{cid}.patch") if p.exists()), None)
        if "error" in res or res.get("incomplete") or src is None:
            skipped.append(cid)
            continue
        if src.parent != commits:
            shutil.copyfile(src, commits / f"{cid}.patch")
        answers[cid] = {"failing": res["failing"], "kind": res["kind"]}
        if res["unknown"]:
            answers[cid]["unknown"] = res["unknown"]
    for p in commits.glob("*.patch"):  # commits dir and answers must match exactly
        if p.stem not in answers:
            p.unlink()
    shutil.copyfile(Path(opts.tests) if opts.tests else SAMPLE_TESTS, setdir / "tests.json")
    (setdir / "answers.json").write_text(json.dumps({"commits": answers}, indent=1), "utf-8")
    kinds: dict[str, list[int]] = {}
    for a in answers.values():
        k = kinds.setdefault(a["kind"], [0, 0])
        k[0] += 1
        k[1] += not a["failing"]
    print(f"{len(answers)} commits ({sum(v[1] for v in kinds.values())} break nothing); "
          + ", ".join(f"{k} {v[0]} ({v[1]} clean)" for k, v in sorted(kinds.items()))
          + (f"; skipped {skipped}" if skipped else ""), file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--src", default="pending")
    ap.add_argument("--tests", help="tests.json (default: sample's)")
    ap.add_argument("--copies", type=int, default=3)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--only")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--finalize", action="store_true")
    opts = ap.parse_args()
    finalize(opts) if opts.finalize else label(opts)


if __name__ == "__main__":
    main()
