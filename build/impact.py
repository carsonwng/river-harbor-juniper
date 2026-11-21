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
                 extra: dict | None = None, deadline: float = 0.0) -> list[dict]:
    """One instrumented suite pass. mode "fork": every test in its own fork of the
    post-collection process (br_cov_plugin.py, BR_IMPACT_ISOLATE). `extra`: more
    environment (BR_IMPACT_PROFILE=1 adds the relation and attribute profiles,
    br_prof.py; BR_IMPACT_STATE checks the state tests leave behind). Past
    `deadline` (epoch seconds) the remaining tests are reported failed without
    running, so the pass still ends with usable results (they become unsure)."""
    cov_dir = work / ("cov-fork" if mode == "fork" else "cov")
    if cov_dir.exists():
        shutil.rmtree(cov_dir)
    cov_dir.mkdir(parents=True, exist_ok=True)
    env = dict(env, BR_IMPACT_ROOT=str(root), BR_IMPACT_OUT=str(cov_dir),
               BR_IMPACT_ISOLATE="fork" if mode == "fork" else "none",
               BR_IMPACT_DEADLINE=str(deadline or 0), **(extra or {}))
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "br_cov_plugin", "-q",
               "-n", str(jobs), "--dist", "load"]
    if env.get("BR_IMPACT_ORDER"):
        command += ["--maxschedchunk", "1"]
    if env.get("BR_IMPACT_PROFILE") == "1":
        command.append("--assert=plain")  # test modules must come through br_prof's import hook
    command.append("tests")
    start = time.time()
    # hard stop well after the deadline (a hung worker): no results from this pass
    hard = max(60.0, deadline - time.time() + 120) if deadline else None
    try:
        proc = subprocess.run(command, cwd=str(root), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors="replace", timeout=hard)
    except subprocess.TimeoutExpired:
        log(f"pytest ({mode}) killed after {time.time() - start:.1f}s: no results")
        return []
    tail = "\n".join(proc.stdout.strip().splitlines()[-3:])
    log(f"pytest ({mode}) exit {proc.returncode} in {time.time() - start:.1f}s: {tail}")
    return [json.loads(p.read_text("utf-8")) for p in sorted(cov_dir.glob("cov-*.json"))]


def relprof_post(out: Path, cov_workers: list[dict], rp_workers: list[dict],
                 unprofiled: list[str] | None = None) -> None:
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
            # tests run without the relation instrumentation (over their time
            # budget): no site they cover can be proven inert
            "unprofiled": sorted(unprofiled or ()),
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
    jobs = max(1, int(os.environ.get("BR_IMPACT_JOBS", "4")))
    chunks = [mods[i::jobs] for i in range(jobs) if mods[i::jobs]]

    def one(i_chunk):
        i, chunk = i_chunk
        part = work / f"test_imports-{i}.json"
        subprocess.run([sys.executable, str(LIB / "br_test_imports.py"), str(root), str(part)] + chunk,
                       cwd=str(root), env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=300)
        return json.loads(part.read_text("utf-8"))

    try:
        merged: dict = {}
        with ThreadPoolExecutor(len(chunks)) as pool:
            for got in pool.map(one, enumerate(chunks)):
                merged.update(got)
        out.write_text(json.dumps(merged), "utf-8")
        return merged
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
    build_start = time.time()
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlglot", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("BR_IMPACT_JOBS", "4")))
    parser.add_argument("--work", type=Path, help="keep raw results here (default: temp)")
    parser.add_argument("--reuse", action="store_true", help="reuse raw results in --work")
    parser.add_argument("--isolate", choices=("none", "fork", "both"),
                        default=os.environ.get("BR_IMPACT_ISOLATE", "both"),
                        help="coverage pass(es): in-process, forked per test, or the union")
    parser.add_argument("--profile", choices=("0", "1"),
                        default=os.environ.get("BR_IMPACT_PROFILE", "1"),
                        help="record the relation and attribute profiles in the forked pass")
    # wall-clock budget of the whole impact build (the image build has 10 min for
    # everything): the in-process pass must end by 45% of it, the forked pass by
    # 85%; tests not run by then are reported failed (unsure, always selected)
    parser.add_argument("--budget", type=float,
                        default=float(os.environ.get("BR_IMPACT_BUDGET", "400")))
    options = parser.parse_args()
    root = options.sqlglot.resolve()
    out = options.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = (options.work or Path(tempfile.mkdtemp(prefix="br-impact-"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    profile = options.profile == "1" and options.isolate in ("fork", "both") and hasattr(os, "fork")

    env = dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE="1", SKIP_INTEGRATION="0",
               PYTHONPATH=os.pathsep.join([str(root), str(LIB)]))
    env.pop("PYTEST_ADDOPTS", None)
    for stale in ("relprof.json", "dependent.json"):
        (out / stale).unlink(missing_ok=True)
    if (out / "attrs").exists():
        shutil.rmtree(out / "attrs")

    start = time.time()
    if options.reuse and (work / "probe.json").exists():
        probes = json.loads((work / "probe.json").read_text("utf-8"))
    else:
        probes = run_probes(root, work, env, options.jobs)
        (work / "probe.json").write_text(json.dumps(probes), "utf-8")
    log(f"import probe: {len(probes)} modules in {time.time() - start:.1f}s")

    sys.path.insert(0, str(LIB))
    import br_attr_build

    tracked = None
    if profile:
        start = time.time()
        try:
            tracked = br_attr_build.prepare(root, work, out, env)
        except Exception as exc:  # noqa: BLE001  attribute profile off, relprof still on
            log(f"attribute state failed: {exc!r}")
            shutil.rmtree(out / "attrs", ignore_errors=True)
        log(f"attribute state and tracked names in {time.time() - start:.1f}s")

    # the per-test-module import probe runs alongside the suite passes
    def _imports() -> dict:
        t0 = time.time()
        if options.reuse and (work / "test_imports.json").exists():
            got = json.loads((work / "test_imports.json").read_text("utf-8"))
        else:
            got = run_test_imports(root, work, env)
        log(f"test import probe: {len(got)} test modules in {time.time() - t0:.1f}s")
        return got

    imports_pool = ThreadPoolExecutor(1)
    imports_future = imports_pool.submit(_imports)
    workers = []
    modes = ["none", "fork"] if options.isolate == "both" else [options.isolate]
    for mode in modes:
        start = time.time()
        cov_dir = work / ("cov-fork" if mode == "fork" else "cov")
        share = 0.5 if mode == "none" and len(modes) == 2 else 0.85
        deadline = build_start + options.budget * share
        extra = {}
        if mode == "none":
            extra["PYTHONHASHSEED"] = "0"  # the state check compares with build fingerprints
            if tracked is not None:
                extra["BR_IMPACT_STATE"] = str(work / "state-check.json")
            if profile and len(modes) == 2:
                # relation profile in the shared-process pass: its verdict is global
                # (every evaluation of the site in the whole suite), so a cache filled
                # by one test is still seen evaluated by that test (the check against
                # a forked relation profile is in strategies/impact-combo.md)
                extra.update(BR_IMPACT_PROFILE="1", BR_PROF_RELPROF="1")
        elif profile and tracked is not None:
            # attribute profile in the forked pass: per-test readers need isolation
            extra.update(BR_IMPACT_PROFILE="1", BR_PROF_RELPROF="0", PYTHONHASHSEED="0")
        if mode == "fork":
            seconds: dict = {}
            for w in workers:
                for nodeid, d in (w.get("durations") or {}).items():
                    seconds[nodeid] = max(seconds.get(nodeid, 0.0), d)
            if seconds:
                (work / "order.json").write_text(json.dumps(seconds), "utf-8")
                extra["BR_IMPACT_ORDER"] = str(work / "order.json")
            if tracked is not None and profile:
                extra["BR_ATTR_TRACKED"] = str(tracked)
        if options.reuse and list(cov_dir.glob("cov-*.json")):
            got = [json.loads(p.read_text("utf-8")) for p in sorted(cov_dir.glob("cov-*.json"))]
        else:
            got = run_coverage(root, work, env, options.jobs, mode, extra, deadline)
        log(f"coverage ({mode}): {len(got)} workers in {time.time() - start:.1f}s")
        if not got:
            log(f"coverage ({mode}) produced nothing; using the other passes only")
            if mode == "fork":
                profile = False
            continue
        for w in got:
            w["mode"] = mode
        workers.extend(got)
    if not workers:
        log("no coverage results; writing no maps (selector falls back to all tests)")
        return 0

    test_imports = imports_future.result()
    start = time.time()
    shape = postprocess(root, out, probes, workers, test_imports)
    log(f"postprocess in {time.time() - start:.1f}s")

    fork_dir = work / "cov-fork"
    unprofiled: dict[str, int] = {}
    for w in workers:
        if w.get("mode") == "fork":
            for t, lv in (w.get("unprofiled") or {}).items():
                unprofiled[t] = max(unprofiled.get(t, 0), lv)
    if unprofiled:
        log(f"tests profiled at a lower level (over budget): {unprofiled}")
    if profile:
        # relation profile of comparison sites, recorded in the forked pass
        start = time.time()
        try:
            rp_workers = [json.loads(p.read_text("utf-8"))
                          for p in sorted((work / "cov").glob("relprof-*.json"))]
            if not rp_workers:
                raise RuntimeError("no relprof results")
            if any(w.get("state_changes") is None for w in workers if w.get("mode") == "none"):
                # the run-time state check finds the registries whose comparisons
                # depend on test order; without it there is no sound relation map
                raise RuntimeError("no run-time state check in the in-process pass")
            relprof_post(out, [w for w in workers if w.get("mode") == "fork"] or workers, rp_workers)
            log(f"relprof postprocess in {time.time() - start:.1f}s")
        except Exception as exc:  # noqa: BLE001  the selector works without it
            log(f"relprof failed, no relprof map: {exc!r}")
            (out / "relprof.json").unlink(missing_ok=True)
        # attribute profile, recorded in the forked pass
        if tracked is not None:
            start = time.time()
            try:
                at_workers = [json.loads(p.read_text("utf-8")) for p in sorted(fork_dir.glob("attr-*.json"))]
                state_changes: list = []
                for w in workers:
                    if w.get("mode") == "none":
                        got = w.get("state_changes")
                        state_changes.extend(got if got is not None else [["no state check"]])
                if not any(w.get("mode") == "none" for w in workers):
                    state_changes.append(["no in-process pass: no state check"])
                pos, by_class, by_module, ALL = shape
                br_attr_build.postprocess(out, at_workers or None, pos, by_class, by_module, ALL,
                                          state_changes,
                                          [t for t, lv in unprofiled.items() if lv >= 2])
                log(f"attribute profile postprocess in {time.time() - start:.1f}s")
            except Exception as exc:  # noqa: BLE001
                log(f"attribute profile failed: {exc!r}")
                shutil.rmtree(out / "attrs", ignore_errors=True)
    if not options.work:
        shutil.rmtree(work, ignore_errors=True)
    for dirpath, _dirs, files in os.walk(out):
        os.chmod(dirpath, 0o755)
        for name in files:
            os.chmod(os.path.join(dirpath, name), 0o644)
    log(f"impact build in {time.time() - build_start:.1f}s")
    return 0


def postprocess(root: Path, out: Path, probes: dict, workers: list[dict],
                test_imports: dict | None = None):
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
    dependent_out: list = []  # for the profiles: what a polluted run may evaluate
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
            dependent_out.append(_dependent_entry(trees.get(rel), rel, qual, func_lines, func_loads,
                                                  bits))
    # Class attributes the in-process pass found changed at its end (registries that
    # tests clear and refill, _Dialect._classes): a function that loads one may
    # evaluate its comparisons on operands that depend on the order of tests (the
    # one site where a forked relation profile saw a relation the shared-process
    # one did not: `len(DIALECT_MODULE_NAMES) != len(cls._classes)`). Their sites
    # are "state" entries: no relation verdict there.
    state_names = set()
    for w in workers:
        for change in w.get("state_changes") or ():
            if change and change[0] == "class" and not str(change[2]).startswith("__"):
                state_names.add(change[2])
    if state_names:
        for (rel, qual), loads in func_loads.items():
            if loads & state_names and (rel, qual) not in {(d["rel"], d["qual"]) for d in dependent_out}:
                entry = _dependent_entry(trees.get(rel), rel, qual, func_lines, func_loads, 0)
                entry["kind"] = "state"
                dependent_out.append(entry)
        log(f"run-time changed class attributes {sorted(state_names)}: "
            f"{sum(1 for d in dependent_out if d.get('kind') == 'state')} functions without relation verdicts")

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
    (out / "meta.json").write_text(json.dumps(
        {"version": VERSION, "sqlglot": str(root), "tests": tests, "unsure": unsure,
         "python": sys.version}, separators=(",", ":")), "utf-8")
    (out / "dependent.json").write_text(json.dumps(
        [dict(d, bits=sets.add(d["bits"])) for d in dependent_out]
        if fork_line_bits and has_ref else None, separators=(",", ":")), "utf-8")
    (out / "sets.json").write_text(json.dumps(
        [format(b, "x") for b in sets.items], separators=(",", ":")), "utf-8")
    log(f"{len(sets.items)} distinct sets, {len(names)} names, {len(class_users)} classes")
    return pos, by_class, by_module, ALL


def _dependent_entry(tree, rel: str, qual: str, func_lines: dict, func_loads: dict, bits: int) -> dict:
    """A state-dependent function for the profile rules of blast/impact.py: its
    line span (nested code included), the names it loads, and whether it reads
    attributes dynamically (getattr/vars/__dict__/hasattr on something that is not
    provably a module object), in which case any attribute may be read."""
    lines: set[int] = set()
    loads: set[str] = set()
    for (r, q), ls in func_lines.items():
        if r == rel and (q == qual or q.startswith(qual + ".")):
            lines |= ls
            loads |= func_loads.get((r, q), set())
    node = _find_def(tree, qual) if tree is not None else None
    lo, hi = (node.lineno, node.end_lineno) if node is not None else (min(lines), max(lines))
    dynamic = node is None or _dynamic_reads(node)
    return {"rel": rel, "qual": qual, "lo": lo, "hi": hi, "loads": sorted(loads),
            "dynamic": dynamic, "bits": bits}


def _find_def(tree, qual: str):
    parts = qual.split(".")
    nodes = tree.body
    found = None
    i = 0
    while i < len(parts):
        part = parts[i]
        if part == "<locals>":
            i += 1
            continue
        found = next((n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                          ast.ClassDef)) and n.name == part), None)
        if found is None:
            return None
        nodes = found.body
        i += 1
    return found if isinstance(found, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def _dynamic_reads(func) -> bool:
    """vars, `.__dict__`, or getattr/hasattr with a computed name on anything but a
    local bound only to importlib.import_module(...) (a module: its globals are
    not class attributes)."""
    modules: set[str] = set()
    others: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            is_mod = (isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                      and node.value.func.attr == "import_module")
            for t in node.targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name):
                        (modules if is_mod and isinstance(t, ast.Name) else others).add(sub.id)
        elif isinstance(node, (ast.For, ast.AugAssign, ast.AnnAssign, ast.withitem, ast.NamedExpr)):
            target = getattr(node, "target", None) or getattr(node, "optional_vars", None)
            for sub in ast.walk(target) if target is not None else ():
                if isinstance(sub, ast.Name):
                    others.add(sub.id)
    safe = modules - others
    for node in ast.walk(func):
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("getattr", "hasattr", "vars"):
            first = node.args[0] if node.args else None
            if not (isinstance(first, ast.Name) and first.id in safe):
                name = node.args[1] if len(node.args) > 1 else None
                if node.func.id == "vars" or not (isinstance(name, ast.Constant)
                                                  and isinstance(name.value, str)):
                    return True  # a constant name is a plain load (code_loads has it)
    return False


if __name__ == "__main__":
    sys.exit(main())
