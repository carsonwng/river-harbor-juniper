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


def run_coverage(root: Path, work: Path, env: dict, jobs: int) -> list[dict]:
    cov_dir = work / "cov"
    cov_dir.mkdir(parents=True, exist_ok=True)
    env = dict(env, BR_IMPACT_ROOT=str(root), BR_IMPACT_OUT=str(cov_dir))
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "br_cov_plugin", "-q",
               "-n", str(jobs), "--dist", "load", "tests"]
    start = time.time()
    proc = subprocess.run(command, cwd=str(root), env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors="replace")
    tail = "\n".join(proc.stdout.strip().splitlines()[-3:])
    log(f"pytest exit {proc.returncode} in {time.time() - start:.1f}s: {tail}")
    return [json.loads(p.read_text("utf-8")) for p in sorted(cov_dir.glob("cov-*.json"))]


def run_relprof(root: Path, work: Path, env: dict, jobs: int) -> list[dict]:
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
        # the image build has 10 minutes: past this, give up on the profile (the
        # selector then keeps the coverage behaviour)
        limit = float(os.environ.get("BR_RELPROF_TIMEOUT", 420 if jobs >= 4 else 1500))
        try:
            proc = subprocess.run(command, cwd=str(copy), env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, errors="replace",
                                  timeout=limit)
        except subprocess.TimeoutExpired:
            log(f"relprof pass exceeded {limit:.0f}s; no relprof map")
            return []
        tail = "\n".join(proc.stdout.strip().splitlines()[-3:])
        log(f"relprof pytest exit {proc.returncode} in {time.time() - start:.1f}s: {tail}")
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

    start = time.time()
    if options.reuse and (work / "probe.json").exists():
        probes = json.loads((work / "probe.json").read_text("utf-8"))
    else:
        probes = run_probes(root, work, env, options.jobs)
        (work / "probe.json").write_text(json.dumps(probes), "utf-8")
    log(f"import probe: {len(probes)} modules in {time.time() - start:.1f}s")

    start = time.time()
    if options.reuse and list((work / "cov").glob("cov-*.json")):
        workers = [json.loads(p.read_text("utf-8")) for p in sorted((work / "cov").glob("cov-*.json"))]
    else:
        workers = run_coverage(root, work, env, options.jobs)
    log(f"coverage: {len(workers)} workers in {time.time() - start:.1f}s")
    if not workers:
        log("no coverage results; writing no maps (selector falls back to all tests)")
        return 0

    start = time.time()
    postprocess(root, out, probes, workers)
    log(f"postprocess in {time.time() - start:.1f}s")

    # lambdas that run during import, and pinned import failures (strat/gap-mix;
    # blast/impact.py _lambda_only_bits, _imports_cleanly)
    start = time.time()
    try:
        subprocess.run([sys.executable, str(LIB / "br_lambda_probe.py"), str(root),
                        str(out / "lambdas.json")], cwd=str(root), env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=True)
        log(f"lambda probe in {time.time() - start:.1f}s")
    except Exception as exc:  # noqa: BLE001  the selector works without it
        log(f"lambda probe failed, no lambdas map: {exc!r}")
        (out / "lambdas.json").unlink(missing_ok=True)

    # relation profile of comparison sites (second, separate suite pass)
    start = time.time()
    try:
        if options.reuse and list((work / "relprof").glob("relprof-*.json")):
            rp_workers = [json.loads(p.read_text("utf-8"))
                          for p in sorted((work / "relprof").glob("relprof-*.json"))]
        else:
            rp_workers = run_relprof(root, work, env, options.jobs)
        log(f"relprof pass: {len(rp_workers)} workers in {time.time() - start:.1f}s")
        if not rp_workers:
            raise RuntimeError("no relprof results")
        start = time.time()
        relprof_post(out, workers, rp_workers)
        log(f"relprof postprocess in {time.time() - start:.1f}s")
    except Exception as exc:  # noqa: BLE001  the selector works without it
        log(f"relprof failed, no relprof map: {exc!r}")
        (out / "relprof.json").unlink(missing_ok=True)
    # bytecode for the pinned sources, after every suite pass: the run-time import
    # check (blast/impact_import.py) then only compiles the edited files. A stale
    # or missing .pyc only costs time, never correctness.
    try:
        if root.as_posix() != "/opt/sqlglot":  # only the image's own copy, never a shared clone
            raise RuntimeError("not the image's sqlglot; skipped")
        subprocess.run([sys.executable, "-m", "compileall", "-q", "-j", "0", str(root / "sqlglot")],
                       cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=180)
    except Exception as exc:  # noqa: BLE001
        log(f"compileall: {exc!r}")
    if not options.work:
        shutil.rmtree(work, ignore_errors=True)
    for dirpath, _dirs, files in os.walk(out):
        os.chmod(dirpath, 0o755)
        for name in files:
            os.chmod(os.path.join(dirpath, name), 0o644)
    return 0


def postprocess(root: Path, out: Path, probes: dict, workers: list[dict]) -> None:
    # ---- tests and per-line test sets
    nodeids: set[str] = set()
    outcomes: dict[str, str] = {}
    for w in workers:
        nodeids.update(t for t in w["tests"] if not t.startswith("collect::"))
        outcomes.update(w["outcomes"])
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

        for rel, lines in w["files"].items():
            target = line_bits.setdefault(rel, {})
            for line, codes in lines.items():
                acc = target.get(int(line), 0)
                for code in codes:
                    acc |= bits_for(code)
                target[int(line)] = acc

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
    name_import: set[str] = set()
    loaded_anywhere: set[str] = set()
    attr_readers: dict[str, int] = {}
    qual_exec: dict[tuple[str, str], int] = {}  # (rel, co_qualname) -> exec bits (functions)
    file_exec: dict[str, int] = {}  # rel -> tests running any function line
    trees: dict[str, ast.AST] = {}
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
        il = imp_lines.get(rel, {})
        for co in iter_code(code):
            lines = {l for _, _, l in co.co_lines() if l}
            is_func = bool(co.co_flags & CO_NEWLOCALS)
            loads, stores, attr = code_loads(co)
            bits = 0
            for l in lines:
                bits |= lb.get(l, 0)
            if rel.startswith("sqlglot/"):
                if is_func:
                    key = (rel, co.co_qualname)
                    qual_exec[key] = qual_exec.get(key, 0) | bits
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
            bits |= name_readers.get(registry, 0)
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
         "core": sorted(core_modules)}, separators=(",", ":")), "utf-8")
    (out / "sets.json").write_text(json.dumps(
        [format(b, "x") for b in sets.items], separators=(",", ":")), "utf-8")
    (out / "meta.json").write_text(json.dumps(
        {"version": VERSION, "sqlglot": str(root), "tests": tests, "unsure": unsure,
         "python": sys.version}, separators=(",", ":")), "utf-8")
    log(f"{len(sets.items)} distinct sets, {len(names)} names, {len(class_users)} classes")


if __name__ == "__main__":
    sys.exit(main())
