#!/usr/bin/env python3
"""Build the impact layer's maps from the pinned sqlglot checkout.

    python build/impact.py --sqlglot DIR --out DIR

Three passes, all on the pinned commit (never on patches):

1. Import probe: for `sqlglot` itself ("core") and every other sqlglot module,
   a fresh interpreter records which sqlglot lines run during that import and
   which modules it loads.
2. Coverage: the whole test suite under pytest-xdist with build/impact_lib/
   br_cov_plugin.py, which records per test the (file, line) pairs it executes
   and the sqlglot classes it instantiates (with their MROs).
3. Post-processing into compact maps (bitsets over a fixed test order):
   - lines/<file>.json : test-time line -> test set, import-time line -> modules
   - names.json        : name -> tests that ran code loading it, and whether
                         import-time code loads it (so its value propagates)
   - classes.json      : class -> tests that use it or a subclass
   - modules.json      : module -> tests that use it (or anything importing it)
   - sets.json         : the deduplicated bitsets (hex) the above index into

blast/impact.py reads these at run time. Maps are only as good as the run: tests
that failed or were not seen are listed so the selector always includes them.
"""

from __future__ import annotations

import argparse
import ast
import dis
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE / "impact_lib"
VERSION = 1
CO_NEWLOCALS = 0x2
LOAD_OPS = {"LOAD_ATTR", "LOAD_GLOBAL", "LOAD_NAME", "LOAD_METHOD", "LOAD_SUPER_ATTR",
            "LOAD_FROM_DICT_OR_GLOBALS", "LOAD_FROM_DICT_OR_DEREF", "LOAD_DEREF",
            "LOAD_CLOSURE", "IMPORT_FROM", "IMPORT_NAME", "DELETE_ATTR", "DELETE_NAME",
            "DELETE_GLOBAL", "LOAD_FAST_LOAD_FAST"}
STORE_OPS = {"STORE_NAME", "STORE_GLOBAL", "STORE_ATTR"}


def log(message: str) -> None:
    print(f"[impact] {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- build budget
#
# The image build has 10 minutes on 4 cores, and the final rebuilds every image on
# the scorer's machine. The Dockerfile's first RUN writes the build's start time
# (BUILD_STAMP). Every pass after the core coverage map runs only while there is
# time before DEADLINE seconds from that start (minus a reserve for postprocessing,
# the other layers and the image export); a pass that would run past it is killed
# and dropped. A dropped pass only ever makes the maps coarser: the selector falls
# back to the rules that do not need it. meta.json records which passes completed.

BUILD_STAMP = Path(os.environ.get("BR_BUILD_STAMP", "/opt/br_build_start"))
DEADLINE = float(os.environ.get("BR_BUILD_DEADLINE", "510"))   # about 8.5 min
RESERVE = float(os.environ.get("BR_BUILD_RESERVE", "75"))      # postprocess, triage, export
ASSUMED_BEFORE = 90.0  # no usable stamp: what download + pip install may have taken


def build_start() -> tuple[float, str]:
    """Wall-clock start of the image build. A stamp older than the 10-minute build
    limit cannot be this build's (a cached layer): then assume the impact build
    started ASSUMED_BEFORE seconds into the image build."""
    now = time.time()
    try:
        stamp = float(BUILD_STAMP.read_text().strip())
        if 0 <= now - stamp <= 600:
            return stamp, "stamp"
    except (OSError, ValueError):
        pass
    return now - ASSUMED_BEFORE, "assumed"


class Budget:
    def __init__(self) -> None:
        self.start, self.source = build_start()
        self.passes: dict[str, dict] = {}
        log(f"build budget: {self.left():.0f}s left before the internal deadline "
            f"({DEADLINE:.0f}s from build start, start from {self.source})")

    def elapsed(self) -> float:
        return time.time() - self.start

    def left(self) -> float:
        """Seconds a pass may still run (deadline minus reserve)."""
        return DEADLINE - RESERVE - self.elapsed()

    def record(self, name: str, status: str, seconds: float, **extra) -> None:
        self.passes[name] = {"status": status, "seconds": round(seconds, 1), **extra}
        log(f"pass {name}: {status} in {seconds:.1f}s (build at {self.elapsed():.0f}s)")


def run_pass(command: list[str], cwd: Path, env: dict, timeout: float | None) -> tuple[int | None, str]:
    """Run a suite pass in its own process group; on timeout kill the whole group
    (xdist workers, forked children) and return (None, tail)."""
    kwargs = {"start_new_session": True} if os.name == "posix" else {}
    proc = subprocess.Popen(command, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace", **kwargs)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            import signal

            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        proc.kill()
        output, _ = proc.communicate()
        return None, _tail(output)
    return proc.returncode, _tail(output)


def _tail(output: str | None) -> str:
    return " | ".join((output or "").strip().splitlines()[-3:])


# ---------------------------------------------------------------- passes


def module_names(root: Path) -> list[str]:
    names = []
    for path in sorted((root / "sqlglot").rglob("*.py")):
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        name = ".".join(parts)
        if name != "sqlglot.__main__":
            names.append(name)
    return names


def run_probes(root: Path, work: Path, env: dict, jobs: int) -> dict[str, dict]:
    probe_dir = work / "probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    modules = ["core"] + [m for m in module_names(root) if m != "sqlglot"]
    chunks = [modules[i::jobs] for i in range(jobs)]

    def one(chunk: list[str]) -> None:
        subprocess.run([sys.executable, str(LIB / "br_import_probe.py"), str(root),
                        str(probe_dir)] + chunk, cwd=str(root), env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)

    with ThreadPoolExecutor(jobs) as pool:
        list(pool.map(one, chunks))
    results = {}
    for module in modules:
        try:
            results[module] = json.loads((probe_dir / f"{module}.json").read_text("utf-8"))
        except Exception:  # noqa: BLE001
            results[module] = {"module": module, "error": "probe failed", "loaded": [],
                               "lines": {}}
    failed = [m for m, r in results.items() if r.get("error")]
    if failed:
        log(f"probe errors in {len(failed)} modules: {failed[:8]}")
    return results


def run_coverage(root: Path, work: Path, env: dict, jobs: int, mode: str = "none",
                 timeout: float | None = None, relprof: bool = False) -> list[dict] | None:
    """One instrumented suite pass. mode "fork": every test in its own fork of the
    post-collection process (br_cov_plugin.py, BR_IMPACT_ISOLATE). None: timed out."""
    cov_dir = work / ("cov-fork" if mode == "fork" else "cov")
    if cov_dir.exists():
        shutil.rmtree(cov_dir)
    cov_dir.mkdir(parents=True)
    env = dict(env, BR_IMPACT_ROOT=str(root), BR_IMPACT_OUT=str(cov_dir),
               BR_IMPACT_ISOLATE="fork" if mode == "fork" else "none")
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "br_cov_plugin", "-q",
               "-n", str(jobs), "--dist", "load", "tests"]
    if relprof:
        # the relation profile rides along in the same (in-process) pass: its
        # import hook rewrites comparisons in place, positions kept, so coverage
        # sees the same lines; it writes relprof-*.json next to cov-*.json
        command[7:7] = ["-p", "br_relprof_plugin"]
    start = time.time()
    code, tail = run_pass(command, root, env, timeout)
    log(f"pytest ({mode}) exit {code} in {time.time() - start:.1f}s: {tail}")
    if code is None:
        return None
    return [json.loads(p.read_text("utf-8")) for p in sorted(cov_dir.glob("cov-*.json"))]


def run_relprof(root: Path, work: Path, env: dict, jobs: int,
                timeout: float | None = None) -> list[dict] | None:
    """Suite pass on a throwaway copy of the checkout with every single-operator
    comparison in sqlglot/ recorded by br_relprof_plugin (source files unchanged;
    the plugin rewrites the AST at import)."""
    rp_dir = work / "relprof"
    if rp_dir.exists():
        shutil.rmtree(rp_dir)
    rp_dir.mkdir(parents=True)
    copy = Path(tempfile.mkdtemp(prefix="br-relprof-src-")) / "sqlglot"
    shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc",
                                                              ".pytest_cache"))
    try:
        env = dict(env, BR_IMPACT_ROOT=str(copy), BR_IMPACT_OUT=str(rp_dir),
                   PYTHONPATH=os.pathsep.join([str(copy), str(LIB)]))
        command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p",
                   "br_relprof_plugin", "-q", "-n", str(jobs), "--dist", "load", "tests"]
        start = time.time()
        code, tail = run_pass(command, copy, env, timeout)
        log(f"relprof pytest exit {code} in {time.time() - start:.1f}s: {tail}")
        if code is None:
            return None
    finally:
        shutil.rmtree(copy.parent, ignore_errors=True)
    return [json.loads(p.read_text("utf-8")) for p in sorted(rp_dir.glob("relprof-*.json"))]


def relprof_post(out: Path, cov_workers: list[dict], rp_workers: list[dict]) -> None:
    """relprof.json: per comparison site "rel:line:col" the union of relation
    signatures over every evaluation in the whole pass (tests, collection, and
    outside any test), and the tests that evaluated it (bitset over meta tests)."""
    meta = json.loads((out / "meta.json").read_text("utf-8"))
    tests = meta["tests"]
    pos = {t: i for i, t in enumerate(tests)}
    by_class: dict[str, int] = {}
    by_module: dict[str, int] = {}
    for t, i in pos.items():
        by_class[t.rsplit("::", 1)[0]] = by_class.get(t.rsplit("::", 1)[0], 0) | (1 << i)
        mod = t.split("::", 1)[0]
        by_module[mod] = by_module.get(mod, 0) | (1 << i)
    seen: set[str] = set()
    outcomes: dict[str, str] = {}
    sigs: dict[str, set[int]] = {}
    evals: dict[str, int] = {}
    values: dict[str, set[str]] = {}
    opaque: set[str] = set()
    for w in rp_workers:
        wtests = w["tests"]
        sites = w["sites"]
        seen.update(t for t in wtests if not t.startswith("collect::"))
        outcomes.update(w["outcomes"])
        expand: dict[int, int] = {}

        def bits_for(code: int) -> int:
            got = expand.get(code)
            if got is None:
                idx, phase = divmod(code, 4)
                key = wtests[idx]
                if phase == 3:
                    got = by_module.get(key[len("collect::"):], 0)
                elif phase == 1:
                    got = 1 << pos[key] if key in pos else 0
                else:
                    got = by_class.get(key.rsplit("::", 1)[0], 0) | (
                        1 << pos[key] if key in pos else 0)
                expand[code] = got
            return got

        for site in sites:  # instrumented (compiled) sites, evaluated or not
            sigs.setdefault(site, set())
        for item in w["outside"]:
            site = sites[item >> 16]
            sigs.setdefault(site, set()).add(item & 0xFFFF)
        for sid, vals in w.get("values", {}).items():
            site = sites[int(sid)]
            if site in opaque:
                continue
            bucket = values.setdefault(site, set())
            bucket.update(json.dumps(v) for v in vals)
        for sid in w.get("opaque", []):
            opaque.add(sites[sid])
            values.pop(sites[sid], None)
        for item, codes in w["records"].items():
            item = int(item)
            site = sites[item >> 16]
            sigs.setdefault(site, set()).add(item & 0xFFFF)
            acc = evals.get(site, 0)
            for code in codes:
                acc |= bits_for(code)
            evals[site] = acc
    for site in [s for s, v in values.items() if len(v) > 256]:
        opaque.add(site)  # too many distinct values to be worth re-evaluating
        del values[site]
    cov_outcomes: dict[str, str] = {}
    for w in cov_workers:
        cov_outcomes.update(w["outcomes"])
    missing = sorted(set(tests) - seen)
    spawned: set[str] = set()
    for w in rp_workers:
        for key in w.get("spawned", []):
            if key.startswith("<outside>"):
                log(f"relprof: child process outside any test: {key}")
                missing.append("<a child process started outside any test>")
            elif key.startswith("collect::"):
                mod = key[len("collect::"):]
                spawned.update(t for t in tests if t.split("::", 1)[0] == mod)
            else:
                spawned.add(key)
    # tests whose profile may be cut short or differ from the coverage pass
    unsure = sorted(t for t in tests if outcomes.get(t, "passed") not in ("passed", "skipped")
                    or outcomes.get(t, "passed") != cov_outcomes.get(t, "passed")
                    or t in spawned)
    set_index: dict[int, int] = {}
    set_items: list[str] = []

    def add(bits: int) -> int:
        got = set_index.get(bits)
        if got is None:
            got = set_index[bits] = len(set_items)
            set_items.append(format(bits, "x"))
        return got

    data = {"version": 1, "complete": not missing, "missing": missing[:50],
            "unsure": unsure,
            "sites": {s: [sorted(v), add(evals.get(s, 0))] for s, v in sorted(sigs.items())},
            # literal-comparison sites: distinct values of the non-literal side
            # (tagged [type, value]); null when some value was not recordable
            "values": {**{s: None for s in sorted(opaque)},
                       **{s: [json.loads(x) for x in sorted(v)]
                          for s, v in sorted(values.items()) if s not in opaque}},
            "sets": set_items}
    (out / "relprof.json").write_text(json.dumps(data, separators=(",", ":")), "utf-8")
    log(f"relprof: {len(sigs)} evaluated sites, {len(missing)} tests missing, "
        f"{len(unsure)} unsure: {unsure[:5]}")


# ---------------------------------------------------------------- code analysis


def iter_code(code):
    yield code
    for const in code.co_consts:
        if hasattr(const, "co_code"):
            yield from iter_code(const)


SKIP_OPS = {"NOP", "EXTENDED_ARG", "CACHE", "COPY_FREE_VARS", "RESUME"}


def code_loads(code) -> tuple[set[str], set[str], set[str]]:
    """Names a code object loads, stores, and loads only to access an attribute
    of (`exp.Foo.bar`, `Snowflake.Tokenizer`): the last is how a class gets used
    without an instance, unlike isinstance(x, exp.Foo)."""
    loads, stores, attr = set(), set(), set()
    pending = None
    for ins in dis.get_instructions(code):
        if ins.opname in SKIP_OPS:
            continue
        if pending is not None and ins.opname == "LOAD_ATTR":
            attr.add(pending)
        pending = None
        if ins.opname in LOAD_OPS:
            if isinstance(ins.argval, str):
                loads.add(ins.argval)
                pending = ins.argval
            elif isinstance(ins.argval, tuple):
                loads.update(v for v in ins.argval if isinstance(v, str))
        elif ins.opname in STORE_OPS and isinstance(ins.argval, str):
            stores.add(ins.argval)
    for const in code.co_consts:
        if isinstance(const, str) and const.isidentifier():
            loads.add(const)
        elif isinstance(const, tuple):
            loads.update(c for c in const if isinstance(c, str) and c.isidentifier())
    return loads, stores, attr


def bound_names(tree: ast.AST) -> set[str]:
    """Names bound at module or class scope anywhere in a file."""
    names: set[str] = set()

    def visit_body(body):
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(stmt.name)
                continue
            if isinstance(stmt, ast.ClassDef):
                names.add(stmt.name)
                visit_body(stmt.body)
                continue
            for node in ast.walk(stmt):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    names.add(node.id)
                elif isinstance(node, ast.alias):
                    names.add((node.asname or node.name).split(".")[0])
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)

    visit_body(tree.body)
    return names


# ---------------------------------------------------------------- main


def run_test_imports(root: Path, work: Path, env: dict) -> dict[str, list[str] | None]:
    """Dynamic part of the importers map: sqlglot modules each test module loads
    when imported (br_test_imports.py, one fork per test module)."""
    out = work / "test_imports.json"
    mods = []
    for path in sorted((root / "tests").rglob("test_*.py")):
        parts = list(path.relative_to(root).with_suffix("").parts)
        mods.append(".".join(parts))
    if not hasattr(os, "fork"):
        return {}
    try:
        subprocess.run([sys.executable, str(LIB / "br_test_imports.py"), str(root), str(out)] + mods,
                       cwd=str(root), env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=300)
        return json.loads(out.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log(f"test import probe failed: {exc!r}")
        return {}


def static_importers(root: Path, trees: dict, deps: dict, sqlglot_modules: set[str],
                     by_module: dict[str, int], dynamic: dict | None = None) -> dict[str, int]:
    """sqlglot module -> tests whose test file imports it, directly or through other
    test modules and sqlglot's own imports (probe). If an edit makes a module's
    import raise, these tests fail at collection ("the file cannot be imported")
    even if they never run its code. Function-level imports count too."""
    def module_of(rel: str) -> str:
        parts = list(Path(rel).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    graph: dict[str, set[str]] = {}
    for rel, tree in trees.items():
        if not rel.startswith("tests/"):
            continue
        name = module_of(rel)
        package = name if rel.endswith("__init__.py") else name.rpartition(".")[0]
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    pkg = package.split(".")
                    pkg = pkg[:len(pkg) - node.level + 1]
                    base = ".".join(pkg + ([base] if base else []))
                found.add(base)
                for alias in node.names:
                    found.add(f"{base}.{alias.name}")
        graph[name] = found
    tests_files = {module_of(t): bits for t, bits in by_module.items()}
    out: dict[str, int] = {}
    for test_mod, bits in tests_files.items():
        seen: set[str] = set()
        stack = [test_mod]
        while stack:
            mod = stack.pop()
            if mod in seen:
                continue
            seen.add(mod)
            # importing a.b.c imports a and a.b first
            parts = mod.split(".")
            for i in range(1, len(parts)):
                stack.append(".".join(parts[:i]))
            if mod in graph:
                stack.extend(graph[mod])
            elif mod in sqlglot_modules:
                stack.extend(deps.get(mod, ()))
        for mod in (dynamic or {}).get(test_mod) or ():
            seen.add(mod)
            seen.update(deps.get(mod, ()))
        for mod in seen:
            if mod in sqlglot_modules:
                out[mod] = out.get(mod, 0) | bits
    return out


class Sets:
    def __init__(self) -> None:
        self.index: dict[int, int] = {}
        self.items: list[int] = []

    def add(self, bits: int) -> int:
        found = self.index.get(bits)
        if found is None:
            found = self.index[bits] = len(self.items)
            self.items.append(bits)
        return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlglot", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("BR_IMPACT_JOBS", "4")))
    parser.add_argument("--work", type=Path, help="keep raw results here (default: temp)")
    parser.add_argument("--reuse", action="store_true", help="reuse raw results in --work")
    parser.add_argument("--isolate", choices=("none", "fork", "both"),
                        default=os.environ.get("BR_IMPACT_ISOLATE", "both"),
                        help="coverage pass(es): in-process, forked per test, or the union")
    options = parser.parse_args()
    root = options.sqlglot.resolve()
    out = options.out
    out.mkdir(parents=True, exist_ok=True)
    work = options.work or Path(tempfile.mkdtemp(prefix="br-impact-"))
    work.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE="1", SKIP_INTEGRATION="0",
               PYTHONPATH=os.pathsep.join([str(root), str(LIB)]))
    env.pop("PYTEST_ADDOPTS", None)

    budget = Budget()
    MIN_PASS = 30.0
    sys.path.insert(0, str(LIB))
    import br_attr_build

    def cached(sub: str, pattern: str) -> list[dict] | None:
        if options.reuse and list((work / sub).glob(pattern)):
            return [json.loads(p.read_text("utf-8")) for p in sorted((work / sub).glob(pattern))]
        return None

    # ---- core (always): import probe, in-process coverage
    start = time.time()
    if options.reuse and (work / "probe.json").exists():
        probes = json.loads((work / "probe.json").read_text("utf-8"))
    else:
        probes = run_probes(root, work, env, options.jobs)
        (work / "probe.json").write_text(json.dumps(probes), "utf-8")
    budget.record("probe", "done", time.time() - start, modules=len(probes))

    workers: list[dict] = []
    modes = ["none", "fork"] if options.isolate == "both" else [options.isolate]
    if modes[0] == "fork" and not hasattr(os, "fork"):
        modes = ["none"]

    relprof_mode = os.environ.get("BR_IMPACT_RELPROF", "separate")  # separate | cov | off

    def coverage(mode: str, timeout: float | None) -> None:
        start = time.time()
        got = cached("cov-fork" if mode == "fork" else "cov", "cov-*.json")
        if got is None:
            got = run_coverage(root, work, env, options.jobs, mode, timeout,
                               relprof=mode == "none" and relprof_mode == "cov")
        name = f"coverage-{'fork' if mode == 'fork' else 'inproc'}"
        if not got:
            budget.record(name, "timeout" if got is None else "empty", time.time() - start)
            return
        for w in got:
            w["mode"] = mode
        workers.extend(got)
        budget.record(name, "done", time.time() - start, workers=len(got))

    # the core map: only cut short just before the hard limit (then no maps at
    # all, and the selector runs every test; the build never fails)
    coverage(modes[0], max(DEADLINE - 30 - budget.elapsed(), MIN_PASS))
    if not workers:
        log("no coverage results; writing no maps (selector falls back to all tests)")
        return 0
    # later passes run the slowest test classes first (br_order.py), so the long
    # tests do not leave three workers idle at the end of a pass
    durations: dict[str, float] = {}
    for w in workers:
        durations.update(w.get("durations", {}))
    if durations:
        (work / "durations.json").write_text(json.dumps(durations), "utf-8")
        env["BR_ORDER_FILE"] = str(work / "durations.json")
        env["PYTEST_PLUGINS"] = ",".join(p for p in (env.get("PYTEST_PLUGINS"), "br_order") if p)

    # ---- cheap soundness inputs, side by side: test-module imports (crash users)
    # and the pristine import-time state (the run-time import-failure check)
    def imports_job() -> dict:
        start = time.time()
        if options.reuse and (work / "test_imports.json").exists():
            got = json.loads((work / "test_imports.json").read_text("utf-8"))
        else:
            got = run_test_imports(root, work, env)
        budget.record("test-imports", "done" if got else "empty", time.time() - start,
                      modules=len(got))
        return got

    def state_job() -> None:
        start = time.time()
        try:
            br_attr_build.prepare(root, work, out, env)
            budget.record("state", "done", time.time() - start)
        except Exception as exc:  # noqa: BLE001
            log(f"import-time state failed: {exc!r}")
            shutil.rmtree(out / "attrs", ignore_errors=True)
            budget.record("state", "failed", time.time() - start)

    with ThreadPoolExecutor(2) as pool:
        imports_future = pool.submit(imports_job)
        pool.submit(state_job).result()
        test_imports = imports_future.result()

    # ---- optional passes, in priority order, while the budget lasts
    # (BR_IMPACT_ORDER). Default: relprof first (the largest measured gain), then
    # the forked coverage pass (strat/impact-isolate: closes the cross-test cache
    # holes of 6 functions; without it the map is the in-process one, as in
    # strat/impact-base), then the attribute/key read profile.
    rp_workers = None
    attr_workers = None

    def fork_pass() -> None:
        if len(modes) < 2:
            return
        left = budget.left()
        if left < MIN_PASS and not options.reuse:
            budget.record("coverage-fork", "skipped", 0.0)
        else:
            coverage("fork", left)

    def relprof_pass() -> None:
        nonlocal rp_workers
        if relprof_mode == "cov" and modes[0] == "none":
            rp_workers = [json.loads(p.read_text("utf-8"))
                          for p in sorted((work / "cov").glob("relprof-*.json"))] or None
            budget.passes["relprof"] = {"status": "done (in coverage-inproc)" if rp_workers
                                        else "empty", "seconds": 0.0}
            return
        if relprof_mode != "separate":
            return
        start = time.time()
        left = budget.left()
        rp_workers = cached("relprof", "relprof-*.json")
        if rp_workers is None and left >= MIN_PASS:
            rp_workers = run_relprof(root, work, env, options.jobs, left)
            budget.record("relprof", "done" if rp_workers else
                          ("timeout" if rp_workers is None else "empty"), time.time() - start)
        elif rp_workers is None:
            budget.record("relprof", "skipped", 0.0)
        else:
            budget.record("relprof", "done", time.time() - start, reused=True)

    def attrs_pass() -> None:
        nonlocal attr_workers
        if os.environ.get("BR_IMPACT_ATTRS", "1") != "1" or \
                not (out / "attrs" / "state.json").exists():
            return
        start = time.time()
        left = budget.left()
        if left >= MIN_PASS or options.reuse:
            try:
                attr_workers = br_attr_build.profile(root, work, env, options.jobs, options.reuse,
                                                     run_pass, left)
            except Exception as exc:  # noqa: BLE001
                log(f"attribute profile failed: {exc!r}")
            budget.record("attrs", "done" if attr_workers else "timeout/failed",
                          time.time() - start)
        else:
            budget.record("attrs", "skipped", 0.0)

    # A pass that cannot finish in the time left is not started (the next, maybe
    # cheaper, one gets the time). Its expected length is the core pass's time
    # times the overhead ratio measured for it on this suite (4 jobs).
    RATIO = {"relprof": 1.9, "fork": 1.8, "attrs": 1.6}
    core_seconds = budget.passes.get("coverage-inproc", {}).get("seconds", 0.0)

    def fits(name: str) -> bool:
        if options.reuse or not core_seconds or (name == "relprof" and relprof_mode != "separate"):
            return True
        need, left = RATIO[name] * core_seconds, budget.left()
        if need > left * 1.1:
            label = {"fork": "coverage-fork"}.get(name, name)
            budget.record(label, f"skipped (expected {need:.0f}s, {left:.0f}s left)", 0.0)
            return False
        return True

    passes = {"relprof": relprof_pass, "fork": fork_pass, "attrs": attrs_pass}
    for name in os.environ.get("BR_IMPACT_ORDER", "relprof,fork,attrs").split(","):
        name = name.strip()
        if name in passes and (name != "fork" or len(modes) > 1) and fits(name):
            passes.pop(name)()

    # ---- postprocess whatever completed
    start = time.time()
    postprocess(root, out, probes, workers, test_imports, attr_workers,
                passes=budget.passes)
    log(f"postprocess in {time.time() - start:.1f}s")
    start = time.time()
    try:
        if not rp_workers:
            raise RuntimeError("no relprof results")
        relprof_post(out, workers, rp_workers)
        log(f"relprof postprocess in {time.time() - start:.1f}s")
    except Exception as exc:  # noqa: BLE001  the selector works without it
        log(f"no relprof map: {exc!r}")
        (out / "relprof.json").unlink(missing_ok=True)
    meta = json.loads((out / "meta.json").read_text("utf-8"))
    meta["passes"] = budget.passes
    meta["build_seconds_at_impact_end"] = round(budget.elapsed(), 1)
    meta["build_start_source"] = budget.source
    (out / "meta.json").write_text(json.dumps(meta, separators=(",", ":")), "utf-8")
    log(f"impact maps done at {budget.elapsed():.0f}s of the build; passes: "
        + ", ".join(f"{k}={v['status']}({v['seconds']}s)" for k, v in budget.passes.items()))
    if not options.work:
        shutil.rmtree(work, ignore_errors=True)
    for dirpath, _dirs, files in os.walk(out):
        os.chmod(dirpath, 0o755)
        for name in files:
            os.chmod(os.path.join(dirpath, name), 0o644)
    return 0


def postprocess(root: Path, out: Path, probes: dict, workers: list[dict],
                test_imports: dict | None = None, attr_workers: list[dict] | None = None,
                passes: dict | None = None) -> None:
    # ---- tests and per-line test sets
    nodeids: set[str] = set()
    outcomes: dict[str, str] = {}
    for w in workers:
        nodeids.update(t for t in w["tests"] if not t.startswith("collect::"))
        for nodeid, outcome in w["outcomes"].items():
            # a test that did not pass in any pass stays unsure
            if outcomes.get(nodeid, "passed") in ("passed", "skipped"):
                outcomes[nodeid] = outcome
    tests = sorted(nodeids)
    pos = {t: i for i, t in enumerate(tests)}
    by_class: dict[str, int] = {}
    by_module: dict[str, int] = {}
    for t, i in pos.items():
        by_class[t.rsplit("::", 1)[0]] = by_class.get(t.rsplit("::", 1)[0], 0) | (1 << i)
        mod = t.split("::", 1)[0]
        by_module[mod] = by_module.get(mod, 0) | (1 << i)
    ALL = (1 << len(tests)) - 1

    line_bits: dict[str, dict[int, int]] = {}
    # In-process passes only (all passes when there is none). Used for one coarse
    # rule: "a test that reads a class registry may use every class in it". In a
    # forked pass every test that builds the generator dispatch reads EXPR_CLASSES
    # (generator._build_dispatch, cached per process otherwise), which would make
    # every generating test a user of every expression class although the dispatch
    # only stores generator methods. Direct evidence (lines, instances, methods,
    # attribute and name reads) comes from every pass.
    ref_line_bits: dict[str, dict[int, int]] = {}
    fork_line_bits: dict[str, dict[int, int]] = {}
    has_ref = any(w.get("mode") != "fork" for w in workers)
    for w in workers:
        wtests = w["tests"]
        expand: dict[int, int] = {}

        def bits_for(code: int) -> int:
            got = expand.get(code)
            if got is None:
                idx, phase = divmod(code, 4)
                key = wtests[idx]
                if phase == 3:  # collection: every test of that module
                    got = by_module.get(key[len("collect::"):], 0)
                elif phase == 1:
                    got = 1 << pos[key]
                else:  # setup/teardown may run class fixtures: the whole class
                    got = by_class.get(key.rsplit("::", 1)[0], 0) | (1 << pos[key])
                expand[code] = got
            return got

        ref = not has_ref or w.get("mode") != "fork"
        for rel, lines in w["files"].items():
            target = line_bits.setdefault(rel, {})
            ref_target = ref_line_bits.setdefault(rel, {})
            for line, codes in lines.items():
                acc = 0
                for code in codes:
                    acc |= bits_for(code)
                target[int(line)] = target.get(int(line), 0) | acc
                if ref:
                    ref_target[int(line)] = ref_target.get(int(line), 0) | acc
                if w.get("mode") == "fork":
                    fork_target = fork_line_bits.setdefault(rel, {})
                    fork_target[int(line)] = fork_target.get(int(line), 0) | acc

    # tests that did not pass under coverage: their coverage may be cut short
    unsure = sorted(t for t in tests if outcomes.get(t, "passed") not in ("passed", "skipped"))
    log(f"{len(tests)} tests covered, {len(unsure)} not passing: {unsure[:5]}")

    # ---- instantiated classes per test
    inst: dict[str, int] = {}
    for w in workers:
        for idx, keys in w["types"].items():
            key = w["tests"][int(idx)]
            if key.startswith("collect::"):
                bits = by_module.get(key[len("collect::"):], 0)
            else:
                bits = by_class.get(key.rsplit("::", 1)[0], 0) if False else (1 << pos[key])
            for k in keys:
                inst[k] = inst.get(k, 0) | bits

    # ---- import-time lines
    core = probes.get("core", {})
    core_modules = set(core.get("loaded", []))
    imp_lines: dict[str, dict[int, list[str]]] = {}
    deps: dict[str, set[str]] = {}
    for module, probe in probes.items():
        for rel, lines in probe.get("lines", {}).items():
            target = imp_lines.setdefault(rel, {})
            for line in lines:
                target.setdefault(line, []).append(module)
        if module != "core":
            deps[module] = set(probe.get("loaded", []))

    # ---- code objects: lines, loads, qualnames
    files = sorted({p.relative_to(root).as_posix() for p in (root / "sqlglot").rglob("*.py")} |
                   {p.relative_to(root).as_posix() for p in (root / "tests").rglob("*.py")})
    candidate_names: set[str] = set()
    name_readers: dict[str, int] = {}
    ref_name_readers: dict[str, int] = {}
    name_import: set[str] = set()
    loaded_anywhere: set[str] = set()
    attr_readers: dict[str, int] = {}
    qual_exec: dict[tuple[str, str], int] = {}  # (rel, co_qualname) -> exec bits (functions)
    file_exec: dict[str, int] = {}  # rel -> tests running any function line
    trees: dict[str, ast.AST] = {}
    func_lines: dict[tuple[str, str], set[int]] = {}
    func_loads: dict[tuple[str, str], set[str]] = {}
    for rel in files:
        source = (root / rel).read_text("utf-8")
        try:
            tree = ast.parse(source)
            code = compile(source, str(root / rel), "exec", dont_inherit=True)
        except SyntaxError:
            continue
        trees[rel] = tree
        if rel.startswith("sqlglot/"):
            candidate_names |= bound_names(tree)
        lb = line_bits.get(rel, {})
        rlb = ref_line_bits.get(rel, {})
        il = imp_lines.get(rel, {})
        for co in iter_code(code):
            lines = {l for _, _, l in co.co_lines() if l}
            is_func = bool(co.co_flags & CO_NEWLOCALS)
            loads, stores, attr = code_loads(co)
            bits = ref_bits = 0
            for l in lines:
                bits |= lb.get(l, 0)
                ref_bits |= rlb.get(l, 0)
            if ref_bits:
                for name in loads:
                    ref_name_readers[name] = ref_name_readers.get(name, 0) | ref_bits
            if rel.startswith("sqlglot/"):
                if is_func:
                    key = (rel, co.co_qualname)
                    qual_exec[key] = qual_exec.get(key, 0) | bits
                    func_lines.setdefault(key, set()).update(lines)
                    func_loads.setdefault(key, set()).update(loads)
                    file_exec[rel] = file_exec.get(rel, 0) | bits
                    if any(l in il for l in lines):
                        name_import.update(loads)
                else:
                    # module or class body: runs at import. A class body that rebinds
                    # a name it reads (TRANSFORMS = {**Base.TRANSFORMS}) only passes it
                    # down to a subclass, which the class rules already cover.
                    name_import.update(loads - stores)
            loaded_anywhere.update(loads)
            if bits:
                for name in loads:
                    name_readers[name] = name_readers.get(name, 0) | bits
                for name in attr:
                    attr_readers[name] = attr_readers.get(name, 0) | bits

    # ---- state-dependent functions (needs an in-process and a forked pass)
    # A function with a line some test ran in the shared process but not when it
    # ran alone depends on state other tests leave behind: Dialect._try_load runs
    # for every dialect lookup after TestDialectEntryPoints cleared
    # Dialect._classes, in whichever tests follow it in that process. The labelling
    # run's order and layout are unknown, so every line of such a function counts
    # for every test that ran it, or ran a function that calls it by name (the
    # callers take the polluted branch: Dialect.get, __getitem__, classes).
    if fork_line_bits and has_ref:
        dependent = []
        for (rel, qual), lines in func_lines.items():
            ref, fork = ref_line_bits.get(rel, {}), fork_line_bits.get(rel, {})
            il = imp_lines.get(rel, {})
            # lines that also run at import: the shared process attributes them to
            # the first importer, the forked pass to nobody (the probe maps them)
            if any(l not in il and ref.get(l, 0) & ~fork.get(l, 0) for l in lines):
                dependent.append((rel, qual))
        for rel, qual in dependent:
            name = qual.rsplit(".", 1)[-1]
            bits = qual_exec.get((rel, qual), 0)
            for key, loads in func_loads.items():
                if name in loads and key != (rel, qual):
                    bits |= qual_exec.get(key, 0)
            target = line_bits.setdefault(rel, {})
            for l in func_lines[(rel, qual)]:
                target[l] = target.get(l, 0) | bits
            log(f"state-dependent function {rel}:{qual}: {bin(bits).count('1')} tests")

    # ---- classes
    sys.path.insert(0, str(root))
    import importlib
    import inspect

    for module in module_names(root):
        try:
            importlib.import_module(module)
        except Exception:  # noqa: BLE001
            pass
    classes: dict[str, type] = {}

    def walk(obj, seen):
        for value in list(vars(obj).values()):
            if inspect.isclass(value) and id(value) not in seen:
                mod = getattr(value, "__module__", "") or ""
                if mod.startswith("sqlglot"):
                    seen.add(id(value))
                    classes[f"{mod}:{value.__qualname__}"] = value
                    walk(value, seen)

    seen: set[int] = set()
    for name, mod in list(sys.modules.items()):
        if name.startswith("sqlglot") and mod is not None:
            walk(mod, seen)
    # module-level containers holding classes (ALL_FUNCTIONS, EXPR_CLASSES, ...):
    # a test that reads one may iterate over every class in it
    registries: dict[str, set[str]] = {}
    for name, mod in list(sys.modules.items()):
        if not name.startswith("sqlglot") or mod is None:
            continue
        for gname, value in list(vars(mod).items()):
            if isinstance(value, dict):
                items = list(value.keys()) + list(value.values())
            elif isinstance(value, (list, tuple, set, frozenset)):
                items = list(value)
            else:
                continue
            for item in items:
                item = getattr(item, "__self__", item)
                if inspect.isclass(item):
                    mod_name = getattr(item, "__module__", "") or ""
                    if mod_name.startswith("sqlglot"):
                        registries.setdefault(f"{mod_name}:{item.__qualname__}", set()).add(gname)
    name_count: dict[str, int] = {}
    for key, klass in classes.items():
        name_count[klass.__name__] = name_count.get(klass.__name__, 0) + 1

    def module_file(mod: str) -> str | None:
        m = sys.modules.get(mod)
        f = getattr(m, "__file__", None)
        if not f:
            return None
        try:
            return Path(f).resolve().relative_to(root).as_posix()
        except ValueError:
            return None

    # methods per (rel, class qualname)
    methods: dict[tuple[str, str], int] = {}
    for (rel, qual), bits in qual_exec.items():
        parts = qual.split(".")
        for i in range(1, len(parts)):
            if parts[i - 1] == "<locals>":
                continue
            prefix = ".".join(parts[:i])
            methods[(rel, prefix)] = methods.get((rel, prefix), 0) | bits

    def subclasses(klass) -> set[type]:
        found, stack = set(), [klass]
        while stack:
            k = stack.pop()
            try:
                subs = type.__subclasses__(k) if isinstance(k, type) else []
            except TypeError:
                subs = []
            for s in subs:
                if s not in found:
                    found.add(s)
                    stack.append(s)
        return found

    class_users: dict[str, int] = {}
    for key, klass in classes.items():
        bits = inst.get(key, 0)
        for k in {klass} | subclasses(klass):
            mod = getattr(k, "__module__", "") or ""
            rel = module_file(mod)
            if rel:
                bits |= methods.get((rel, k.__qualname__), 0)
            bits |= inst.get(f"{mod}:{k.__qualname__}", 0)
        # references by name: the class itself, or its outermost class when the
        # short name is shared (nested Tokenizer/Parser/Generator classes)
        qual = klass.__qualname__
        if name_count.get(klass.__name__, 0) == 1 or "." not in qual:
            bits |= attr_readers.get(klass.__name__, 0)
        if "." in qual:
            outer = qual.split(".")[0]
            bits |= attr_readers.get(outer, 0)
        for registry in registries.get(key, ()):
            bits |= ref_name_readers.get(registry, 0)
        class_users[key] = bits

    # ---- modules
    direct: dict[str, int] = {}
    for module in module_names(root):
        rel = module_file(module) or (module.replace(".", "/") + ".py")
        bits = file_exec.get(rel, 0)
        for key, klass_bits in class_users.items():
            if key.split(":", 1)[0] == module:
                bits |= klass_bits
        direct[module] = bits
    importers = static_importers(root, trees, deps, set(module_names(root)), by_module,
                                 test_imports)
    module_users: dict[str, int] = {}
    for module in module_names(root):
        if module in core_modules:
            module_users[module] = ALL
            continue
        bits = direct.get(module, 0)
        for other, loaded in deps.items():
            if module in loaded:
                bits |= direct.get(other, 0)
        module_users[module] = bits

    # ---- write
    sets = Sets()
    lines_dir = out / "lines"
    if lines_dir.exists():
        shutil.rmtree(lines_dir)
    lines_dir.mkdir(parents=True)
    rel_to_module = {}
    for module in module_names(root):
        rel = module_file(module) or (module.replace(".", "/") + ".py")
        rel_to_module[rel] = module
    for rel in files:
        if not rel.startswith("sqlglot/"):
            continue
        data = {"module": rel_to_module.get(rel),
                "lines": {str(l): sets.add(b) for l, b in sorted(line_bits.get(rel, {}).items())},
                "import": {str(l): sorted(set(m)) for l, m in sorted(imp_lines.get(rel, {}).items())}}
        (lines_dir / (rel.replace("/", "__") + ".json")).write_text(
            json.dumps(data, separators=(",", ":")), "utf-8")
    # a name no code loads by name is read dynamically (getattr(self, f"{x}_sql")):
    # treat it as propagating
    names = {n: [sets.add(name_readers.get(n, 0)),
                 int(n in name_import or n not in loaded_anywhere)]
             for n in sorted(candidate_names)}
    (out / "names.json").write_text(json.dumps(names, separators=(",", ":")), "utf-8")
    (out / "classes.json").write_text(json.dumps(
        {k: sets.add(v) for k, v in sorted(class_users.items())}, separators=(",", ":")), "utf-8")
    (out / "modules.json").write_text(json.dumps(
        {"users": {k: sets.add(v) for k, v in sorted(module_users.items())},
         "importers": {k: sets.add(v | module_users.get(k, 0))
                       for k, v in sorted(importers.items())},
         "core": sorted(core_modules)}, separators=(",", ":")), "utf-8")
    if (out / "attrs").is_dir():
        import br_attr_build

        br_attr_build.postprocess(out, attr_workers, pos, by_class, by_module, ALL, sets)
    (out / "sets.json").write_text(json.dumps(
        [format(b, "x") for b in sets.items], separators=(",", ":")), "utf-8")
    (out / "meta.json").write_text(json.dumps(
        {"version": VERSION, "sqlglot": str(root), "tests": tests, "unsure": unsure,
         "python": sys.version, "passes": passes or {}}, separators=(",", ":")), "utf-8")
    log(f"{len(sets.items)} distinct sets, {len(names)} names, {len(class_users)} classes")


if __name__ == "__main__":
    sys.exit(main())
