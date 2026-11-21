#!/usr/bin/env python3
"""Build step for the triage layer: static maps of sqlglot's test suite.

    python build/triage.py --sqlglot DIR --out DIR

Writes, from the pinned sqlglot checkout only (no tests.json, no answers):

  triage.json
    fixtures   {fixture path: [test ids that read it]}   (any non-.py file under tests/)
    fixture_modules {fixture path: [test modules that reference it]}
    patterns   [[glob, [test ids]]]   path templates and directory references, for new files
    fallback   [test modules that read fixtures through names we could not resolve]
    tests      {test id: [defining module, qualname, first line, last line]}
    importers  {module path: [test module paths that import it, transitively]}
  src/<path>   copies of every tests/**/*.py and sqlglot/**/*.py, so the runtime can
               rebuild the post-patch file and compare ASTs.

How a test is linked to a fixture: we evaluate the string expressions each test can
reach (its own body, unittest lifecycle methods of its class and bases, class bodies,
module-level calls, and every function it calls or references in tests/, with constant
arguments substituted into f-string parameters, e.g. check_file("simplify") ->
"optimizer/simplify.sql"). A string names a fixture when it equals a path suffix of the
fixture (with or without the extension) or one of its directories. Unresolvable
f-strings become glob patterns. Everything errs toward selecting more.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import shutil
import sys
from pathlib import Path

LIFECYCLE = {"setUp", "tearDown", "setUpClass", "tearDownClass", "asyncSetUp",
             "asyncTearDown", "__init__", "run", "__call__", "debug", "setUp_",
             "addCleanup", "addClassCleanup", "subTest"}
MODULE_LIFECYCLE = {"setUpModule", "tearDownModule", "load_tests"}
WILD = "\x00"  # placeholder for an unknown piece of a string, rendered as '*'
DYN = "\x01dynamic"  # marker: the code lists a directory, so it may read any file in it
DYNAMIC = {"listdir", "glob", "rglob", "iglob", "iterdir", "scandir"}  # plus os.walk
MAX_DEPTH = 12


def module_name(rel: str) -> str:
    name = rel[:-3].replace("/", ".")
    return name[: -len(".__init__")] if name.endswith(".__init__") else name


class Module:
    def __init__(self, rel: str, tree: ast.Module):
        self.rel = rel
        self.name = module_name(rel)
        self.tree = tree
        self.functions: dict[str, ast.AST] = {}  # module-level functions
        self.classes: dict[str, ast.ClassDef] = {}
        self.constants: dict[str, list[ast.AST]] = {}  # module-level name -> value nodes
        self.imports: dict[str, tuple[str, str | None]] = {}  # local name -> (module, attr)
        self.toplevel_calls: list[ast.AST] = []  # module-level statements that run code
        package = self.name if rel.endswith("__init__.py") else self.name.rpartition(".")[0]
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
                if node.name in MODULE_LIFECYCLE:
                    self.toplevel_calls.append(node)
            elif isinstance(node, ast.ClassDef):
                self.classes[node.name] = node
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports[alias.asname or alias.name.split(".")[0]] = (
                        alias.name if alias.asname else alias.name.split(".")[0], None)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".")
                    parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                    base = ".".join(p for p in parts + ([node.module] if node.module else []) if p)
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = (base, alias.name)
            else:
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        for name in ast.walk(target):
                            if isinstance(name, ast.Name) and node.value is not None:
                                self.constants.setdefault(name.id, []).append(node.value)
                if any(isinstance(n, ast.Call) for n in ast.walk(node)):
                    self.toplevel_calls.append(node)


class Analyzer:
    def __init__(self, root: Path):
        self.root = root
        self.modules: dict[str, Module] = {}  # dotted name -> Module
        for path in sorted((root / "tests").rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_text("utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            module = Module(rel, tree)
            self.modules[module.name] = module
        self.memo: dict[tuple, frozenset[str]] = {}
        self.stack: set[tuple] = set()

    # ----------------------------------------------------------- resolution

    def resolve_name(self, module: Module, name: str, depth: int = 0):
        """A name in a module -> ("func", Module, node) | ("class", Module, node) |
        ("const", Module, name) | None."""
        if depth > 5:
            return None
        if name in module.functions:
            return ("func", module, module.functions[name])
        if name in module.classes:
            return ("class", module, module.classes[name])
        if name in module.constants:
            return ("const", module, name)
        if name in module.imports:
            target, attr = module.imports[name]
            if attr is None:
                return ("module", self.modules.get(target), None) if target in self.modules else None
            if f"{target}.{attr}" in self.modules:
                return ("module", self.modules[f"{target}.{attr}"], None)
            other = self.modules.get(target)
            if other is not None:
                return self.resolve_name(other, attr, depth + 1)
        return None

    def mro(self, module: Module, cls: ast.ClassDef, depth: int = 0) -> list[tuple[Module, ast.ClassDef]]:
        out = [(module, cls)]
        if depth > 10:
            return out
        for base in cls.bases:
            found = None
            if isinstance(base, ast.Name):
                found = self.resolve_name(module, base.id)
            elif isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
                owner = self.resolve_name(module, base.value.id)
                if owner and owner[0] == "module" and owner[1]:
                    found = self.resolve_name(owner[1], base.attr)
            if found and found[0] == "class":
                for item in self.mro(found[1], found[2], depth + 1):
                    if item not in out:
                        out.append(item)
        return out

    def find_method(self, module: Module, cls: ast.ClassDef, name: str):
        for owner_module, owner in self.mro(module, cls):
            for node in owner.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                    return owner_module, owner, node
        return None

    # ----------------------------------------------------------- string evaluation

    def render(self, node: ast.AST, env: dict[str, str | None]) -> str | None:
        """Best-effort string value of an expression; WILD marks unknown parts."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant):
                    parts.append(str(value.value))
                elif isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name) \
                        and env.get(value.value.id) is not None:
                    parts.append(env[value.value.id])
                else:
                    parts.append(WILD)
            return "".join(parts)
        if isinstance(node, ast.Name) and env.get(node.id) is not None:
            return env[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.render(node.left, env), self.render(node.right, env)
            if left is None and right is None:
                return None
            return (left if left is not None else WILD) + (right if right is not None else WILD)
        return None

    def strings_of(self, module: Module, node: ast.AST, env: dict[str, str | None],
                   cls: tuple[Module, ast.ClassDef] | None, depth: int) -> set[str]:
        """Every string an AST subtree can produce, following calls and references."""
        out: set[str] = set()
        callees = {id(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)}
        # literal pieces of an f-string are not strings of their own
        pieces = {id(v) for j in ast.walk(node) if isinstance(j, ast.JoinedStr) for v in j.values}
        for sub in ast.walk(node):
            if id(sub) in pieces:
                continue
            if id(sub) in callees and isinstance(sub, (ast.Name, ast.Attribute)):
                name = sub.id if isinstance(sub, ast.Name) else sub.attr
                if name in DYNAMIC or (name == "walk" and isinstance(sub, ast.Attribute)
                                       and isinstance(sub.value, ast.Name) and sub.value.id == "os"):
                    out.add(DYN)
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                out.add(sub.value)
            elif isinstance(sub, (ast.JoinedStr, ast.BinOp)):
                value = self.render(sub, env)
                if value is not None:
                    out.add(value)
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if sub.id in env:
                    if env[sub.id] is not None:
                        out.add(env[sub.id])
                    continue
                out |= self.reference(module, sub.id, None, cls, depth)
            elif isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                if sub.value.id in ("self", "cls") and cls is not None:
                    found = self.find_method(cls[0], cls[1], sub.attr)
                    if found:
                        out |= self.function_strings(found[0], found[2], {}, (found[0], found[1]), depth + 1)
                else:
                    owner = self.resolve_name(module, sub.value.id) if sub.value.id not in env else None
                    if owner and owner[0] == "module" and owner[1]:
                        out |= self.reference(owner[1], sub.attr, None, None, depth)
                    elif owner and owner[0] == "class":
                        found = self.find_method(owner[1], owner[2], sub.attr)
                        if found:
                            out |= self.function_strings(found[0], found[2], {}, (found[0], found[1]), depth + 1)
            if isinstance(sub, ast.Call):
                out |= self.call(module, sub, env, cls, depth)
        return out

    def reference(self, module: Module, name: str, call: ast.Call | None,
                  cls, depth: int, env=None) -> set[str]:
        found = self.resolve_name(module, name)
        if not found:
            return set()
        kind, owner, node = found
        if kind == "func":
            return self.function_strings(owner, node, self.bind(node, call, env or {}), None, depth + 1)
        if kind == "const":
            key = ("const", owner.name, name)
            if key in self.stack or depth > MAX_DEPTH:
                return set()
            self.stack.add(key)
            try:
                out: set[str] = set()
                for value in owner.constants[name]:
                    out |= self.strings_of(owner, value, {}, None, depth + 1)
                return out
            finally:
                self.stack.discard(key)
        if kind == "class":
            # Instantiating or referencing a helper class: include its lifecycle code.
            out = set()
            for method in ("__init__", "setUp", "setUpClass"):
                hit = self.find_method(owner, node, method)
                if hit:
                    out |= self.function_strings(hit[0], hit[2], {}, (hit[0], hit[1]), depth + 1)
            return out
        return set()

    def call(self, module: Module, call: ast.Call, env, cls, depth: int) -> set[str]:
        func = call.func
        if isinstance(func, ast.Name) and func.id not in env:
            return self.reference(module, func.id, call, cls, depth, env)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in ("self", "cls") and cls is not None:
                found = self.find_method(cls[0], cls[1], func.attr)
                if found:
                    return self.function_strings(found[0], found[2], self.bind(found[2], call, env, True),
                                                 (found[0], found[1]), depth + 1)
                return set()
            owner = self.resolve_name(module, func.value.id) if func.value.id not in env else None
            if owner and owner[0] == "module" and owner[1]:
                return self.reference(owner[1], func.attr, call, None, depth, env)
            if owner and owner[0] == "class":
                found = self.find_method(owner[1], owner[2], func.attr)
                if found:
                    return self.function_strings(found[0], found[2], self.bind(found[2], call, env, True),
                                                 (found[0], found[1]), depth + 1)
        return set()

    def bind(self, func: ast.AST, call: ast.Call | None, env, method: bool = False) -> dict:
        params = [a.arg for a in func.args.posonlyargs + func.args.args]
        if method and params and params[0] in ("self", "cls"):
            params = params[1:]
        bound: dict[str, str | None] = {p: None for p in params + [a.arg for a in func.args.kwonlyargs]}
        if call is None:
            return bound
        for name, arg in zip(params, call.args):
            if isinstance(arg, ast.Starred):
                break
            bound[name] = self.render(arg, env)
        for keyword in call.keywords:
            if keyword.arg in bound:
                bound[keyword.arg] = self.render(keyword.value, env)
        return bound

    def function_strings(self, module: Module, func: ast.AST, env: dict, cls, depth: int) -> frozenset[str]:
        key = (module.name, id(func), tuple(sorted((k, v) for k, v in env.items() if v is not None)))
        if key in self.memo:
            return self.memo[key]
        if key in self.stack or depth > MAX_DEPTH:
            return frozenset()
        self.stack.add(key)
        try:
            out: set[str] = set()
            for part in [*func.decorator_list, *func.args.defaults, *func.args.kw_defaults, *func.body]:
                if part is not None:
                    out |= self.strings_of(module, part, env, cls, depth)
            # pytest-style fixtures requested by argument name
            for arg in func.args.args:
                if arg.arg in module.functions and arg.arg not in ("self", "cls"):
                    out |= self.function_strings(module, module.functions[arg.arg], {}, None, depth + 1)
            result = frozenset(out)
        finally:
            self.stack.discard(key)
        self.memo[key] = result
        return result

    def module_strings(self, module: Module) -> set[str]:
        out: set[str] = set()
        for node in module.toplevel_calls:
            out |= self.strings_of(module, node, {}, None, 0)
        return out

    def class_strings(self, module: Module, cls: ast.ClassDef) -> set[str]:
        out: set[str] = set()
        for owner_module, owner in self.mro(module, cls):
            out |= self.module_strings(owner_module)
            for dec in owner.decorator_list:
                out |= self.strings_of(owner_module, dec, {}, None, 0)
            for node in owner.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in LIFECYCLE:
                        out |= self.function_strings(owner_module, node, {}, (owner_module, owner), 0)
                else:
                    out |= self.strings_of(owner_module, node, {}, (owner_module, owner), 0)
        return out

    # ----------------------------------------------------------- test enumeration

    def tests(self):
        """Yield (test id, test module, defining module, class, method node)."""
        for module in self.modules.values():
            base = os.path.basename(module.rel)
            if not (base.startswith("test_") or base.endswith("_test.py")):
                continue
            for cls in module.classes.values():
                if not cls.name.startswith("Test") and not self.is_testcase(module, cls):
                    continue
                seen = set()
                for owner_module, owner in self.mro(module, cls):
                    for node in owner.body:
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                                and node.name.startswith("test") and node.name not in seen:
                            seen.add(node.name)
                            yield (f"{module.rel}::{cls.name}::{node.name}", module, owner_module,
                                   owner, node)
            for func in module.functions.values():
                if func.name.startswith("test"):
                    yield (f"{module.rel}::{func.name}", module, module, None, func)

    def is_testcase(self, module: Module, cls: ast.ClassDef) -> bool:
        for _, owner in self.mro(module, cls):
            for base in owner.bases:
                text = ast.unparse(base)
                if text.endswith("TestCase"):
                    return True
        return False

    def importers(self) -> dict[str, list[str]]:
        direct: dict[str, set[str]] = {}
        for module in self.modules.values():
            for target, attr in module.imports.values():
                for name in (target, f"{target}.{attr}" if attr else None):
                    # importing a.b.c also imports packages a and a.b
                    while name:
                        if name in self.modules and name != module.name:
                            direct.setdefault(self.modules[name].rel, set()).add(module.rel)
                        name = name.rpartition(".")[0]
        out = {}
        for rel in {m.rel for m in self.modules.values()}:
            seen, todo = set(), [rel]
            while todo:
                for nxt in direct.get(todo.pop(), ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        todo.append(nxt)
            seen.discard(rel)
            out[rel] = sorted(seen)
        return out


def fixture_keys(rel_fixture: str) -> tuple[set[str], set[str]]:
    """Strings that name a fixture file: path suffixes, with or without extension.
    Also returns the directory names (as path suffixes) that contain it."""
    parts = rel_fixture.split("/")  # relative to tests/
    names, dirs = set(), set()
    for i in range(len(parts)):
        suffix = "/".join(parts[i:])
        names.add(suffix)
        stem = suffix
        while "." in stem.rsplit("/", 1)[-1]:
            stem = stem.rsplit(".", 1)[0]
            names.add(stem)
    for j in range(1, len(parts)):
        for i in range(j):
            dirs.add("/".join(parts[i:j]))
    return names, dirs


def normalize(value: str) -> str:
    value = value.replace("\\", "/").strip()
    while value.startswith(("./", "/")):
        value = value[1:] if value.startswith("/") else value[2:]
    return value.rstrip("/")


ROOTS = {"fixtures", "tests/fixtures", "tests"}


def matches(strings: set[str], names: set[str], dirs: set[str], full: str) -> bool:
    dynamic = DYN in strings
    for raw in strings:
        if raw == DYN:
            continue
        if not raw or len(raw) > 300:
            continue
        value = normalize(raw)
        if not value:
            continue
        if WILD in value:
            pattern = value.replace(WILD, "*")
            if not pathlike(value):
                continue
            if fnmatch.fnmatchcase(full, pattern) or fnmatch.fnmatchcase(full, "*/" + pattern) \
                    or fnmatch.fnmatchcase(full, "*/" + pattern + ".*") \
                    or fnmatch.fnmatchcase(full, pattern + "/*") or fnmatch.fnmatchcase(full, "*/" + pattern + "/*"):
                return True
            continue
        if value in ROOTS:
            # the fixture root itself is named everywhere (FIXTURES_DIR); it only means
            # "any fixture" when the same code lists directories
            if dynamic:
                return True
            continue
        for candidate in {value, value.removeprefix("tests/"), value.removeprefix("fixtures/"),
                          value.removeprefix("tests/fixtures/")}:
            if candidate in names:
                return True
            # A directory: "optimizer/tpc-h/" names everything in it. A bare word that is
            # also some fixture's own name ("optimizer" -> optimizer.sql) only names the
            # directory when the code lists directories.
            if candidate in dirs and candidate not in ROOTS and (
                    dynamic or "/" in raw or candidate not in FILE_KEYS):
                return True
    return False


EXTENSIONS: tuple[str, ...] = ()
DIRS: set[str] = set()  # directory names under tests/ that hold fixtures
FILE_KEYS: set[str] = set()  # every string that names some fixture file


def pathlike(value: str) -> bool:
    """A template with unknown parts that can name a fixture: no whitespace, and its
    literal text ends in a fixture extension or names a fixture directory."""
    if any(c.isspace() for c in value):
        return False
    literal = value.replace(WILD, "/")
    if any(piece.endswith(EXTENSIONS) for piece in literal.split("/") if piece) and EXTENSIONS:
        return True
    return any(piece in DIRS for piece in literal.split("/"))


def main() -> int:
    global EXTENSIONS
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlglot", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    options = parser.parse_args()
    root: Path = options.sqlglot.resolve()
    out: Path = options.out
    out.mkdir(parents=True, exist_ok=True)

    analyzer = Analyzer(root)
    fixtures = sorted(p.relative_to(root).as_posix() for p in (root / "tests").rglob("*")
                      if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts)
    EXTENSIONS = tuple(sorted({"." + f.rsplit(".", 1)[-1] for f in fixtures if "." in f.rsplit("/", 1)[-1]}))
    keyed = {f: fixture_keys(f[len("tests/"):]) for f in fixtures}
    for names, _ in keyed.values():
        FILE_KEYS.update(names)
    DIRS.update(part for f in fixtures for part in f.split("/")[1:-1] if part not in ROOTS)

    tests: dict[str, list] = {}
    readers: dict[str, set[str]] = {f: set() for f in fixtures}
    module_refs: dict[str, set[str]] = {f: set() for f in fixtures}
    patterns: dict[str, set[str]] = {}
    fallback: set[str] = set()
    class_cache: dict[tuple, set[str]] = {}
    test_rows = list(analyzer.tests())
    for test_id, module, owner_module, owner, node in test_rows:
        tests[test_id] = [owner_module.rel, (owner.name + "." if owner else "") + node.name,
                          node.lineno, node.end_lineno]
        cls_key = (module.name, owner and owner.name, test_id.split("::")[1])
        if cls_key not in class_cache:
            if owner is not None:
                cls = module.classes[test_id.split("::")[1]]
                class_cache[cls_key] = analyzer.class_strings(module, cls)
            else:
                class_cache[cls_key] = analyzer.module_strings(module)
        cls_ctx = None
        if owner is not None:
            cls_ctx = (module, module.classes[test_id.split("::")[1]])
        strings = set(class_cache[cls_key])
        strings |= analyzer.function_strings(owner_module, node, analyzer.bind(node, None, {}, owner is not None),
                                             cls_ctx, 0)
        hit_any = False
        for fixture, (names, dirs) in keyed.items():
            if matches(strings, names, dirs, fixture[len("tests/"):]):
                readers[fixture].add(test_id)
                module_refs[fixture].add(module.rel)
                hit_any = True
        for raw in strings:
            value = normalize(raw)
            if WILD in value and pathlike(value):
                patterns.setdefault(value.replace(WILD, "*"), set()).add(test_id)
            elif DYN in strings and value in ROOTS | DIRS:
                patterns.setdefault(value + "/*", set()).add(test_id)
        if hit_any or any("fixtures" in s for s in strings):
            fallback.add(module.rel)

    data = {
        "fixtures": {f: sorted(v) for f, v in readers.items()},
        "fixture_modules": {f: sorted(v) for f, v in module_refs.items()},
        "patterns": sorted([p, sorted(v)] for p, v in patterns.items()),
        "fallback": sorted(fallback),
        "tests": tests,
        "importers": analyzer.importers(),
        "records": {},
    }
    try:  # record-level narrowing of lifecycle-loaded fixtures (strat/gap-tests)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import triage_records
        data["records"] = triage_records.build(
            analyzer, keyed, matches, test_rows, lambda m: print(m, file=sys.stderr))
        for fixture, entry in list(data["records"].items()):
            # every test the entry covers must be a static reader already
            if not set(entry["tests"]) <= readers[fixture]:
                print(f"[triage] records for {fixture} cover non-readers; dropped", file=sys.stderr)
                data["records"].pop(fixture)
    except Exception as error:  # never break the build; no records means old behaviour
        print(f"[triage] records failed: {error!r}", file=sys.stderr)
        data["records"] = {}
    (out / "triage.json").write_text(json.dumps(data, indent=0, sort_keys=True), "utf-8")

    src = out / "src"
    if src.exists():
        shutil.rmtree(src)
    for top in ("tests", "sqlglot"):
        for path in (root / top).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            target = src / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

    for fixture in data["records"]:
        target = src / fixture
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / fixture, target)

    unread = [f for f, v in readers.items() if not v]
    print(f"[triage] {len(tests)} static tests, {len(fixtures)} fixtures, "
          f"{len(unread)} with no static reader, {len(patterns)} patterns", file=sys.stderr)
    for f in unread[:20]:
        print(f"[triage]   no reader: {f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
