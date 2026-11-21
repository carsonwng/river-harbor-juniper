#!/usr/bin/env python3
"""Agent harness: strategy worktrees, evaluation, shared leaderboard.

Stdlib only, works on Windows and Linux. Run from anywhere inside the repo or
one of its worktrees:

    python harness/br.py new <name> [--from REF]   create strat/<name> in .worktrees/<name>
    python harness/br.py list                      branches, worktrees, best score each
    python harness/br.py eval [--docker] [-m MSG]  score the current worktree, log to board
    python harness/br.py board [--all]             leaderboard (best per branch, or every run)
    python harness/br.py verify <ref> [--local]    docker-eval a ref on sample + held-out sets
    python harness/br.py submission [add|score]    practice submission log + hourly slot
    python harness/br.py maps [--only LAYER]       run build/ steps locally into .maps/
    python harness/br.py oracle                    perfect-selection lower bound on sample
    python harness/br.py sqlglot                   clone sqlglot at the pinned commit (shared cache)

`eval` without --docker runs `python selector.py` on the host with BR_DATA
pointing at the data set, and reports wall time as a stand-in for CPU. With
--docker it builds the worktree's Dockerfile and runs it under the scorer's
exact flags, reading the container's real CPU time from its cgroup. Only
docker numbers are comparable to the board on the track page.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

PINNED_SQLGLOT = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
SQLGLOT_URL = "https://github.com/tobymao/sqlglot.git"
BRANCH_PREFIX = "strat/"

# Mirrors the Makefile's RUN_FLAGS (minus the data volumes, added per run).
RUN_FLAGS = [
    "--rm", "--platform", "linux/amd64", "--network", "none", "--cpus", "2",
    "--memory", "4096m", "--memory-swap", "4096m", "--pids-limit", "256",
    "--read-only", "--tmpfs", "/tmp:rw,size=64m,mode=1777", "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges", "--user", "65534:65534",
    "--env", "HOME=/tmp",
]
RUN_TIME_LIMIT_SECONDS = 180  # sample/practice; the final allows 270 for ~1.5x commits


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=check, capture_output=True, text=True
    )
    return result.stdout.strip()


def worktree_root() -> Path:
    return Path(git("rev-parse", "--show-toplevel"))


def main_root() -> Path:
    """The primary checkout (holds .worktrees/ and .cache/)."""
    return Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).parent


def harness_state_dir() -> Path:
    path = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "br-harness"
    path.mkdir(parents=True, exist_ok=True)
    return path


def leaderboard_path() -> Path:
    return harness_state_dir() / "leaderboard.jsonl"


def load_scorer(root: Path):
    # Always score with main's score.py so a branch cannot drift the scorer.
    main_scorer = main_root() / "score.py"
    path = main_scorer if main_scorer.exists() else root / "score.py"
    spec = importlib.util.spec_from_file_location("br_score", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- new / list


def cmd_new(options) -> int:
    name = options.name.removeprefix(BRANCH_PREFIX)
    branch = BRANCH_PREFIX + name
    root = main_root()
    target = root / ".worktrees" / name
    if target.exists():
        log(f"{target} already exists")
        return 1
    existing = git("branch", "--list", branch, cwd=root)
    if existing:
        git("worktree", "add", str(target), branch, cwd=root)
    else:
        git("worktree", "add", "-b", branch, str(target), options.base, cwd=root)
    log(f"created {branch} at {target} (from {options.base if not existing else branch})")
    print(target)
    return 0


def best_by_branch() -> dict[str, dict]:
    best: dict[str, dict] = {}
    for record in read_board():
        key = record["branch"]
        if key not in best or rank_key(record) < rank_key(best[key]):
            best[key] = record
    return best


def cmd_list(options) -> int:
    root = main_root()
    worktrees: dict[str, str] = {}
    current_path = None
    for line in git("worktree", "list", "--porcelain", cwd=root).splitlines():
        if line.startswith("worktree "):
            current_path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/"):
            worktrees[line[len("branch refs/heads/"):]] = current_path
    branches = git("for-each-ref", "--format=%(refname:short)\t%(committerdate:relative)\t%(subject)",
                   "refs/heads", cwd=root).splitlines()
    best = best_by_branch()
    print(f"{'branch':28} {'best total':>11} {'misses':>6}  {'last commit':16} worktree")
    for row in branches:
        branch, when, subject = (row.split("\t") + ["", ""])[:3]
        record = best.get(branch)
        score = f"{record['total_seconds']:.1f}" if record else "-"
        misses = str(record["misses"]) if record else "-"
        print(f"{branch:28} {score:>11} {misses:>6}  {when:16} {worktrees.get(branch, '')}")
    return 0


# ---------------------------------------------------------------- eval


def run_local(root: Path, data_dir: Path) -> tuple[bytes, float, str]:
    env = dict(os.environ, BR_DATA=str(data_dir), PYTHONHASHSEED="0")
    command = [sys.executable, str(root / "selector.py")]
    start = time.perf_counter()
    result = subprocess.run(command, cwd=root, env=env, capture_output=True)
    elapsed = time.perf_counter() - start
    sys.stderr.write(result.stderr.decode("utf-8", "replace"))
    if result.returncode != 0:
        raise SystemExit(f"selector exited {result.returncode}")
    return result.stdout, elapsed, "wall"


def docker_image_command(image: str) -> list[str]:
    raw = subprocess.run(
        ["docker", "inspect", "-f", "{{json .Config.Entrypoint}}|{{json .Config.Cmd}}", image],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    entrypoint, cmd = (json.loads(part) or [] for part in raw.split("|", 1))
    return list(entrypoint) + list(cmd)


def run_docker(root: Path, data_dir: Path, tag: str) -> tuple[bytes, float, str]:
    log(f"docker build -> {tag}")
    build = subprocess.run(
        ["docker", "build", "--platform", "linux/amd64", "--tag", tag, "."], cwd=root
    )
    if build.returncode != 0:
        raise SystemExit("docker build failed")
    inner = shlex.join(docker_image_command(tag))
    # Run the image's own command, then report the container cgroup's CPU usage
    # (every process, user + system) on stderr, as the board measures it.
    wrapper = (
        f"{inner}; status=$?; "
        "echo BR_CPU_USEC=$(grep usage_usec /sys/fs/cgroup/cpu.stat | cut -d' ' -f2) >&2; "
        "exit $status"
    )
    data = data_dir.resolve()
    command = [
        "docker", "run", *RUN_FLAGS,
        "--volume", f"{data / 'commits'}:/data/commits:ro",
        "--volume", f"{data / 'tests.json'}:/data/tests.json:ro",
        "--entrypoint", "/bin/sh", tag, "-c", wrapper,
    ]
    start = time.perf_counter()
    result = subprocess.run(command, capture_output=True)
    elapsed = time.perf_counter() - start
    stderr = result.stderr.decode("utf-8", "replace")
    cpu = None
    for line in stderr.splitlines():
        if line.startswith("BR_CPU_USEC="):
            value = line.split("=", 1)[1].strip()
            cpu = int(value) / 1e6 if value.isdigit() else None
        else:
            sys.stderr.write(line + "\n")
    if result.returncode != 0:
        raise SystemExit(f"container exited {result.returncode}")
    if elapsed > RUN_TIME_LIMIT_SECONDS:
        log(f"WARNING: run took {elapsed:.1f}s, over the {RUN_TIME_LIMIT_SECONDS}s limit")
    if cpu is None:
        log("WARNING: could not read cgroup CPU; using wall time")
        return result.stdout, elapsed, "wall"
    return result.stdout, cpu, "cgroup"


def per_commit_report(stdout: bytes, tests: list[dict], answers: dict) -> list[dict]:
    duration = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    rows = []
    for line in stdout.decode("utf-8").splitlines():
        value = json.loads(line)
        workers = [w for w in value["workers"] if w]
        selected = {n for w in workers for n in w}
        cost = (max(sum(duration[n] for n in w) for w in workers) + 2000 * len(workers)) if workers else 0
        failing = answers[value["commit"]]["failing"]
        missed = sorted(set(failing) - selected)
        rows.append({
            "commit": value["commit"], "kind": answers[value["commit"]].get("kind"),
            "cost_ms": cost, "workers": len(workers), "selected": len(selected),
            "failing": len(failing), "missed": missed,
        })
    return sorted(rows, key=lambda r: (not r["missed"], -r["cost_ms"]))


def cmd_eval(options) -> int:
    root = worktree_root()
    data_dir = Path(options.data).resolve() if options.data else root / "sample"
    answers_path = Path(options.answers).resolve() if options.answers else data_dir / "answers.json"
    tests = json.loads((data_dir / "tests.json").read_text("utf-8"))["tests"]
    answers = json.loads(answers_path.read_text("utf-8"))["commits"]

    branch = options.label or git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    if options.docker:
        tag = "br-" + "".join(c if c.isalnum() or c in "-_." else "-" for c in branch.lower())
        stdout, cpu_seconds, cpu_source = run_docker(root, data_dir, tag)
    else:
        stdout, cpu_seconds, cpu_source = run_local(root, data_dir)

    out_path = root / "output.jsonl"
    out_path.write_bytes(stdout)
    scorer = load_scorer(root)
    evaluation = scorer.evaluate_run(stdout, tests, answers)
    if not evaluation["ok"]:
        print(f"Rejected: {evaluation['code']} on line {evaluation['line']}")
        return 1

    simulated = evaluation["simulated_milliseconds"] / 1000
    misses = evaluation["missed_commits"]
    total = simulated + cpu_seconds

    if options.verbose:
        print(f"{'commit':8} {'kind':8} {'cost s':>8} {'wkrs':>4} {'sel':>5} {'fail':>4}  missed")
        for row in per_commit_report(stdout, tests, answers):
            print(f"{row['commit']:8} {str(row['kind']):8} {row['cost_ms']/1000:8.3f} "
                  f"{row['workers']:4} {row['selected']:5} {row['failing']:4}  "
                  f"{row['missed'] if row['missed'] else ''}")
    print(f"Simulated: {simulated:.3f} s   Misses: {len(misses)} {misses if misses else ''}")
    print(f"CPU:       {cpu_seconds:.3f} s ({cpu_source})   Total: {total:.3f} s")

    if options.no_log:
        return 0
    sha = git("rev-parse", "--short", "HEAD", cwd=root)
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", cwd=root))
    record = {
        "time": dt.datetime.now().isoformat(timespec="seconds"),
        "branch": branch, "sha": sha, "dirty": dirty,
        "mode": "docker" if options.docker else "local",
        "data": data_dir.name if options.data else "sample",
        "simulated_seconds": round(simulated, 3), "misses": len(misses),
        "missed_commits": misses, "cpu_seconds": round(cpu_seconds, 3),
        "cpu_source": cpu_source, "total_seconds": round(total, 3),
        "note": options.message or "",
    }
    with leaderboard_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    if dirty:
        log("note: logged with dirty=true; commit before claiming a result")
    return 0


# ---------------------------------------------------------------- board


def read_board() -> list[dict]:
    path = leaderboard_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def rank_key(record: dict) -> tuple:
    # Docker results are authoritative; within a mode, lower total wins.
    return (record["mode"] != "docker", record["total_seconds"])


def cmd_board(options) -> int:
    records = read_board()
    if options.data:
        records = [r for r in records if r.get("data") == options.data]
    if not options.all:
        best: dict[tuple, dict] = {}
        for r in records:
            key = (r["branch"], r["mode"], r.get("data"))
            if key not in best or r["total_seconds"] < best[key]["total_seconds"]:
                best[key] = r
        records = list(best.values())
    records.sort(key=rank_key)
    print(f"{'total':>9} {'sim':>9} {'cpu':>7} {'miss':>4} {'mode':6} {'data':8} "
          f"{'branch':26} {'sha':9} note")
    for r in records:
        sha = r["sha"] + ("*" if r["dirty"] else "")
        print(f"{r['total_seconds']:9.3f} {r['simulated_seconds']:9.3f} {r['cpu_seconds']:7.3f} "
              f"{r['misses']:4} {r['mode']:6} {str(r.get('data')):8} {r['branch']:26} {sha:9} "
              f"{r.get('note', '')}")
    return 0


# ---------------------------------------------------------------- oracle


def cmd_oracle(options) -> int:
    """Lower bound: run exactly the failing tests, best split. Uses answers, so it is a
    yardstick only; a selector must never read answers."""
    root = worktree_root()
    data_dir = Path(options.data).resolve() if options.data else root / "sample"
    tests = json.loads((data_dir / "tests.json").read_text("utf-8"))["tests"]
    answers = json.loads((data_dir / "answers.json").read_text("utf-8"))["commits"]
    duration = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    total = 0
    for commit, answer in sorted(answers.items()):
        loads = sorted((duration[n] for n in answer["failing"]), reverse=True)
        best = 0
        for k in range(1, min(8, len(loads)) + 1):
            bins = [0] * k
            for load in loads:
                bins[bins.index(min(bins))] += load
            cost = max(bins) + 2000 * k
            best = cost if best == 0 else min(best, cost)
        total += best
    everything = sum(duration.values())
    print(f"oracle simulated: {total/1000:.3f} s over {len(answers)} commits "
          f"(all tests on one worker would be {everything/1000:.3f} s per commit)")
    return 0


# ---------------------------------------------------------------- verify


def data_sets(root: Path) -> list[Path]:
    """sample plus every labelled held-out set under validation/sets/ (in root or main)."""
    sets = {root / "sample"}
    for base in (root, main_root()):
        sets |= {p.parent for p in (base / "validation" / "sets").glob("*/answers.json")}
    return sorted(sets, key=lambda p: (p.name != "sample", p.name))


def cmd_verify(options) -> int:
    """Docker-evaluate any ref on sample + held-out sets in a throwaway worktree.
    Exit 0 only if every set is valid with 0 misses."""
    root = main_root()
    sha = git("rev-parse", "--short", options.ref, cwd=root)
    path = root / ".worktrees" / f"_verify-{sha}"
    if not path.exists():
        git("worktree", "add", "--detach", str(path), sha, cwd=root)
    ok = True
    try:
        if options.local:  # docker builds maps in the image; host runs need them built here
            subprocess.run([sys.executable, str(root / "harness" / "br.py"), "maps"], cwd=path,
                           capture_output=True)
        for data in data_sets(path):
            command = [sys.executable, str(root / "harness" / "br.py"), "eval",
                       "--label", options.ref, "-m", options.message or f"verify {options.ref}"]
            if not options.local:
                command.append("--docker")
            if data.name != "sample":
                command += ["--data", str(data)]
            log(f"verify {options.ref}@{sha} on {data.name}")
            result = subprocess.run(command, cwd=path, capture_output=True, text=True)
            print(f"[{data.name}] " + " | ".join(result.stdout.strip().splitlines()[-2:]))
            if result.returncode != 0 or "Misses: 0" not in result.stdout:
                ok = False
                sys.stderr.write(result.stderr[-2000:])
    finally:
        if not options.keep:
            git("worktree", "remove", "--force", str(path), cwd=root, check=False)
    print(f"VERIFY {'PASS' if ok else 'FAIL'} {options.ref} {sha}")
    return 0 if ok else 1


# ---------------------------------------------------------------- submissions


def submissions_path() -> Path:
    return harness_state_dir() / "submissions.jsonl"


def read_submissions() -> list[dict]:
    path = submissions_path()
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]


def cmd_submission(options) -> int:
    """Machine log of practice submissions, used to respect the 1-per-hour limit."""
    records = read_submissions()
    now = dt.datetime.now()
    if options.action == "add":
        full = git("rev-parse", options.sha, cwd=main_root())
        records.append({"time": now.isoformat(timespec="seconds"), "sha": full,
                        "ref": options.ref or "", "expect": options.expect,
                        "purpose": options.note or "", "score": None})
    elif options.action == "score":
        matches = [r for r in records if r["sha"].startswith(options.sha)]
        if not matches:
            log(f"no submission with sha {options.sha}")
            return 1
        matches[-1]["score"] = options.score
    if options.action in ("add", "score"):
        submissions_path().write_text("".join(json.dumps(r) + "\n" for r in records), "utf-8")
    for r in records:
        print(f"{r['time']}  {r['sha'][:9]}  {r.get('ref',''):28} expect={r.get('expect')}  "
              f"score={r.get('score')}  {r.get('purpose','')}")
    last = max((dt.datetime.fromisoformat(r["time"]) for r in records), default=None)
    if last is None:
        print("SLOT OPEN (no submissions logged)")
    else:
        ready = last + dt.timedelta(minutes=61)
        print(f"SLOT {'OPEN' if now >= ready else 'CLOSED until ' + ready.strftime('%H:%M:%S')}"
              f" (last {last.strftime('%H:%M:%S')})")
    return 0


# ---------------------------------------------------------------- maps


def cmd_maps(options) -> int:
    """Run build/run_all.py against the shared sqlglot into <worktree>/.maps."""
    root = worktree_root()
    sqlglot = main_root() / ".cache" / "sqlglot"
    if not sqlglot.exists():
        log("no shared sqlglot yet; run `python harness/br.py sqlglot` first")
        return 1
    command = [sys.executable, str(root / "build" / "run_all.py"),
               "--sqlglot", str(sqlglot), "--out", str(root / ".maps")]
    if options.only:
        command += ["--only", options.only]
    return subprocess.run(command, cwd=root).returncode


# ---------------------------------------------------------------- sqlglot


def cmd_sqlglot(options) -> int:
    target = main_root() / ".cache" / "sqlglot"
    if not (target / ".git").exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", SQLGLOT_URL, str(target)], check=True)
    subprocess.run(["git", "-c", "advice.detachedHead=false", "checkout", "--quiet",
                    PINNED_SQLGLOT], cwd=target, check=True)
    log(f"sqlglot at {PINNED_SQLGLOT[:12]} in {target} (shared: treat as read-only;"
        " copy it or `git worktree add` from it to experiment)")
    print(target)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="create a strategy branch + worktree")
    p.add_argument("name")
    p.add_argument("--from", dest="base", default="main", help="base ref (default main; any sibling works)")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("list", help="branches, worktrees and their best scores")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("eval", help="score the current worktree and log it")
    p.add_argument("--docker", action="store_true", help="build and run under the scorer's limits")
    p.add_argument("--data", help="data dir with tests.json and commits/ (default: sample)")
    p.add_argument("--answers", help="answers.json (default: <data>/answers.json)")
    p.add_argument("-m", "--message", help="note for the leaderboard")
    p.add_argument("-v", "--verbose", action="store_true", help="per-commit breakdown")
    p.add_argument("--no-log", action="store_true", help="do not write to the leaderboard")
    p.add_argument("--label", help="branch name to record (default: current branch)")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("board", help="show the shared leaderboard")
    p.add_argument("--all", action="store_true", help="every run, not just best per branch")
    p.add_argument("--data", help="only runs on this data set")
    p.set_defaults(func=cmd_board)

    p = sub.add_parser("oracle", help="lower bound from the answers (yardstick only)")
    p.add_argument("--data", help="data dir with tests.json and answers.json")
    p.set_defaults(func=cmd_oracle)

    p = sub.add_parser("verify", help="docker-eval a ref on sample + held-out sets; exit 0 iff 0 misses")
    p.add_argument("ref")
    p.add_argument("--local", action="store_true", help="host python instead of docker")
    p.add_argument("--keep", action="store_true", help="keep the throwaway worktree")
    p.add_argument("-m", "--message")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("submission", help="log practice submissions; shows whether the hourly slot is open")
    p.add_argument("action", nargs="?", default="status", choices=["status", "add", "score"])
    p.add_argument("sha", nargs="?")
    p.add_argument("score", nargs="?", type=float)
    p.add_argument("--ref")
    p.add_argument("--expect", type=float)
    p.add_argument("--note")
    p.set_defaults(func=cmd_submission)

    p = sub.add_parser("maps", help="run build/ steps locally into <worktree>/.maps")
    p.add_argument("--only", help="just this layer's build step")
    p.set_defaults(func=cmd_maps)

    p = sub.add_parser("sqlglot", help="clone sqlglot at the pinned commit into .cache/")
    p.set_defaults(func=cmd_sqlglot)

    options = parser.parse_args(argv)
    return options.func(options)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
