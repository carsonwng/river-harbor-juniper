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


def run_coverage(root: Path, work: Path, env: dict, jobs: int, mode: str = "none") -> list[dict]:
    """One instrumented suite pass. mode "fork": every test in its own fork of the
    post-collection process (br_cov_plugin.py, BR_IMPACT_ISOLATE)."""
    cov_dir = work / ("cov-fork" if mode == "fork" else "cov")
    cov_dir.mkdir(parents=True, exist_ok=True)
    env = dict(env, BR_IMPACT_ROOT=str(root), BR_IMPACT_OUT=str(cov_dir),
               BR_IMPACT_ISOLATE="fork" if mode == "fork" else "none")
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "br_cov_plugin", "-q",
               "-n", str(jobs), "--dist", "load", "tests"]
    start = time.time()
    proc = subprocess.run(command, cwd=str(root), env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors="replace")
    tail = "\n".join(proc.stdout.strip().splitlines()[-3:])
    log(f"pytest ({mode}) exit {proc.returncode} in {time.time() - start:.1f}s: {tail}")
    return [json.loads(p.read_text("utf-8")) for p in sorted(cov_dir.glob("cov-*.json"))]


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


def static_importers(root: Path, trees: dict, deps: dict, sqlglot_modules: set[str],
                     by_module: dict[str, int]) -> dict[str, int]:
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

    start = time.time()
    if options.reuse and (work / "probe.json").exists():
        probes = json.loads((work / "probe.json").read_text("utf-8"))
    else:
        probes = run_probes(root, work, env, options.jobs)
        (work / "probe.json").write_text(json.dumps(probes), "utf-8")
    log(f"import probe: {len(probes)} modules in {time.time() - start:.1f}s")

    workers = []
    modes = ["none", "fork"] if options.isolate == "both" else [options.isolate]
    for mode in modes:
        start = time.time()
        cov_dir = work / ("cov-fork" if mode == "fork" else "cov")
        if options.reuse and list(cov_dir.glob("cov-*.json")):
            got = [json.loads(p.read_text("utf-8")) for p in sorted(cov_dir.glob("cov-*.json"))]
        else:
            got = run_coverage(root, work, env, options.jobs, mode)
        log(f"coverage ({mode}): {len(got)} workers in {time.time() - start:.1f}s")
        if not got:
            log(f"coverage ({mode}) produced nothing; using the other passes only")
            continue
        for w in got:
            w["mode"] = mode
        workers.extend(got)
    if not workers:
        log("no coverage results; writing no maps (selector falls back to all tests)")
        return 0

    start = time.time()
    postprocess(root, out, probes, workers)
    log(f"postprocess in {time.time() - start:.1f}s")
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
    importers = static_importers(root, trees, deps, set(module_names(root)), by_module)
    module_users: dict[str, int] = {}
    for module in module_names(root):
        if module in core_modules:
            module_users[module] = ALL
            continue
        bits = direct.get(module, 0)
        for other, loaded in deps.items():
            if module in loaded:
                bits |= direct.get(other, 0)
        module_users[module] = bits | importers.get(module, 0)

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
