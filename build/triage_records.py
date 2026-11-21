"""Record-level narrowing of fixtures loaded in a unittest lifecycle method.

Owner: strat/gap-tests. Used by build/triage.py.

Some fixtures are read once per class, in setUpClass, and the parsed records are kept
on the class:

    cls.tpcds_sqls = list(load_sql_fixture_pairs("optimizer/tpc-ds/tpc-ds.sql"))

The plain string analysis then links the fixture to *every* test of the class, because
setUpClass runs for all of them. Here we find out which tests actually use the stored
records, and which fields of each record they use:

    for args, sql, _ in sqls: ...         -> fields 0 and 1, never field 2

For each such fixture this writes a `records` entry to triage.json:

  attr, loader [module, function], arg   how the records are built
  tests  {test id: {"use": null | "all" | [field, ...],
                    "touch": [data attributes it touches],
                    "mutate": [data attributes it may mutate]}}

The runtime (blast/triage.py) rebuilds the post-patch fixture, runs the loader on the old
and new text, and selects only the tests that use a changed field, plus tests that share
mutable class state with them (fixpoint), plus every test of the class if the loader
raises (setUpClass would fail). Anything this analysis does not understand counts as
"all", which is exactly the old behaviour.
"""

from __future__ import annotations

import ast
import sys

LOADER_WRAPS = {"list", "tuple"}
OPAQUE_CALLS = {"getattr", "setattr", "vars", "delattr", "hasattr"}
MAX_DEPTH = 8
ALL = "all"


def _is_self(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id in ("self", "cls")


def _parents(func: ast.AST) -> dict[int, ast.AST]:
    out = {}
    for node in ast.walk(func):
        for child in ast.iter_child_nodes(node):
            out[id(child)] = node
    return out


class Unknown(Exception):
    """The analysis cannot bound the use; the caller treats it as 'all'."""


class Usage:
    """Field usage of one attribute `attr` by one test, through everything it calls."""

    def __init__(self, analyzer, attr: str, data_attrs: set[str]):
        self.analyzer = analyzer
        self.attr = attr
        self.data_attrs = data_attrs
        self.fields: set[int] | str | None = None  # None: never used
        self.touch: set[str] = set()
        self.mutate: set[str] = set()
        self.seen: set[tuple] = set()

    def add(self, fields):
        if fields == ALL or self.fields == ALL:
            self.fields = ALL
        else:
            self.fields = (self.fields or set()) | set(fields)

    # -------------------------------------------------------------- walking

    def function(self, module, func: ast.AST, cls, aliases: frozenset[str], depth: int):
        key = (id(func), aliases, cls and id(cls[1]))
        if key in self.seen:
            return
        if depth > MAX_DEPTH:
            raise Unknown("depth")
        self.seen.add(key)
        parents = _parents(func)
        loaded = {n.id for n in ast.walk(func) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        for node in ast.walk(func):
            # dynamic attribute access on the instance: cannot bound
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in OPAQUE_CALLS:
                if any(_is_self(a) for a in node.args):
                    raise Unknown("dynamic attribute access")
            if isinstance(node, ast.Attribute) and node.attr == "__dict__":
                raise Unknown("__dict__")
            if isinstance(node, ast.Name) and node.id in ("self", "cls") and isinstance(node.ctx, ast.Load):
                parent = parents.get(id(node))
                if not (isinstance(parent, ast.Attribute) and parent.value is node):
                    raise Unknown("self escapes")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "super":
                raise Unknown("super()")
            # data attributes: touched / mutated
            if isinstance(node, ast.Attribute) and _is_self(node.value) and node.attr in self.data_attrs:
                self.touch.add(node.attr)
                if self._mutates(node, parents):
                    self.mutate.add(node.attr)
            # uses of the record list
            if (isinstance(node, ast.Attribute) and _is_self(node.value) and node.attr == self.attr
                    and isinstance(node.ctx, ast.Load)) or \
                    (isinstance(node, ast.Name) and node.id in aliases and isinstance(node.ctx, ast.Load)):
                self.use(module, node, parents, loaded, cls, depth)
            # calls into other code of the test suite
            if isinstance(node, ast.Call):
                self.call(module, node, cls, aliases, depth)

    def _mutates(self, node: ast.Attribute, parents) -> bool:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            return True
        parent = parents.get(id(node))
        if isinstance(parent, ast.Subscript) and parent.value is node:
            return isinstance(parent.ctx, (ast.Store, ast.Del))
        if isinstance(parent, ast.Attribute) and parent.value is node:
            grand = parents.get(id(parent))
            if isinstance(parent.ctx, (ast.Store, ast.Del)):
                return True
            return isinstance(grand, ast.Call) and grand.func is parent  # method call
        if isinstance(parent, ast.Call) and node in parent.args + [k.value for k in parent.keywords]:
            return True  # handed to other code
        if isinstance(parent, (ast.AugAssign,)):
            return True
        return False

    def call(self, module, call: ast.Call, cls, aliases, depth: int):
        """Follow a call into tests/ code, binding the record list to parameters."""
        target = self._resolve(module, call.func, cls)
        if target is None:
            return
        owner_module, func, owner_cls, is_method = target
        params = [a.arg for a in func.args.posonlyargs + func.args.args]
        if is_method and params and params[0] in ("self", "cls"):
            params = params[1:]
        bound = set()
        for name, arg in zip(params, call.args):
            if self._is_records(arg, aliases):
                bound.add(name)
        for keyword in call.keywords:
            if keyword.arg and self._is_records(keyword.value, aliases):
                bound.add(keyword.arg)
        if is_method or bound:
            self.function(owner_module, func, owner_cls if is_method else None, frozenset(bound), depth + 1)

    def _is_records(self, node, aliases) -> bool:
        return (isinstance(node, ast.Attribute) and _is_self(node.value) and node.attr == self.attr)             or (isinstance(node, ast.Name) and node.id in aliases)

    def _resolve(self, module, func_node, cls):
        a = self.analyzer
        if isinstance(func_node, ast.Attribute) and _is_self(func_node.value) and cls is not None:
            found = a.find_method(cls[0], cls[1], func_node.attr)
            if found:
                return found[0], found[2], (found[0], cls[1]), True
            return None
        if isinstance(func_node, ast.Name):
            found = a.resolve_name(module, func_node.id)
            if found and found[0] == "func":
                return found[1], found[2], None, False
        if isinstance(func_node, ast.Attribute) and isinstance(func_node.value, ast.Name):
            owner = a.resolve_name(module, func_node.value.id)
            if owner and owner[0] == "module" and owner[1]:
                found = a.resolve_name(owner[1], func_node.attr)
                if found and found[0] == "func":
                    return found[1], found[2], None, False
        return None

    def use(self, module, node, parents, loaded, cls, depth):
        """Fields of the records that an occurrence of the list can read."""
        parent = parents.get(id(node))
        # for x in L / comprehension over L
        if isinstance(parent, (ast.For, ast.comprehension)) and parent.iter is node:
            self.add(self._fields(parent.target, loaded))
            return
        # for i, x in enumerate(L)
        if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) \
                and parent.func.id == "enumerate" and parent.args and parent.args[0] is node:
            loop = parents.get(id(parent))
            if isinstance(loop, (ast.For, ast.comprehension)) and loop.iter is parent \
                    and isinstance(loop.target, ast.Tuple) and len(loop.target.elts) == 2:
                self.add(self._fields(loop.target.elts[1], loaded))
                return
            raise Unknown("enumerate")
        # L[i] unpacked into a tuple, possibly inside `a if c else b`
        if isinstance(parent, ast.Subscript) and parent.value is node and isinstance(parent.ctx, ast.Load):
            expr = parent
            up = parents.get(id(expr))
            while isinstance(up, ast.IfExp) and expr is not up.test:
                expr, up = up, parents.get(id(up))
            if isinstance(up, ast.Assign) and up.value is expr and len(up.targets) == 1:
                self.add(self._fields(up.targets[0], loaded))
                return
            raise Unknown("subscript")
        # handed to a function of the test suite: analysed with the parameter as alias
        if isinstance(parent, ast.Call) and node in parent.args and \
                self._resolve(module, parent.func, cls) is not None:
            return  # self.call() follows it
        if isinstance(parent, ast.keyword):
            call = parents.get(id(parent))
            if isinstance(call, ast.Call) and self._resolve(module, call.func, cls) is not None:
                return
        raise Unknown(f"use in {type(parent).__name__}")

    @staticmethod
    def _fields(target, loaded) -> set[int] | str:
        if isinstance(target, (ast.Tuple, ast.List)) and not any(isinstance(e, ast.Starred) for e in target.elts):
            out = set()
            for j, elt in enumerate(target.elts):
                if isinstance(elt, ast.Name) and elt.id not in loaded:
                    continue  # bound, never read
                out.add(j)
            return out
        return ALL


def _record_statements(analyzer, module, cls):
    """(lifecycle function, statement, attr, loader name node, fixture string) in `cls`."""
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in ("setUpClass", "setUp"):
            continue
        for stmt in node.body:
            if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1):
                continue
            target = stmt.targets[0]
            if not (isinstance(target, ast.Attribute) and _is_self(target.value)):
                continue
            value = stmt.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
                    and value.func.id in LOADER_WRAPS and len(value.args) == 1 and not value.keywords:
                value = value.args[0]
            if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and len(value.args) == 1
                    and not value.keywords and isinstance(value.args[0], ast.Constant)
                    and isinstance(value.args[0].value, str)):
                continue
            found = analyzer.resolve_name(module, value.func.id)
            if not (found and found[0] == "func"):
                continue
            yield node, stmt, target.attr, (found[1], found[2]), value.args[0].value


def _attribute_sites(analyzer, attr: str):
    stores, foreign = 0, 0
    for module in analyzer.modules.values():
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Attribute) and node.attr == attr:
                if not _is_self(node.value):
                    foreign += 1
                elif isinstance(node.ctx, (ast.Store, ast.Del)):
                    stores += 1
    return stores, foreign


def _data_attrs(analyzer, module, cls) -> set[str]:
    out = set()
    for owner_module, owner in analyzer.mro(module, cls):
        for node in ast.walk(owner):
            if isinstance(node, ast.Attribute) and _is_self(node.value) and isinstance(node.ctx, ast.Store):
                out.add(node.attr)
        for node in owner.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    if isinstance(t, ast.Name):
                        out.add(t.id)
    return out


def _strip_subtest_labels(module):
    """Blank the arguments of `self.subTest(...)`: they are labels, never paths.
    Returns what to put back."""
    out = []
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)                 and _is_self(node.func.value) and node.func.attr == "subTest":
            out.append((node, node.args, node.keywords))
            node.args, node.keywords = [], []
    return out


def names_file(strings, names, dirs, full) -> bool:
    """Does any string open this file? Like triage.matches, except that a directory on
    its own (no listing call) does not count: to open a file in it the code has to add a
    file name, and that name is a constant (a name match), an f-string or concatenation
    (a rendered pattern, matched against the file), or comes from a listing (DYN)."""
    import fnmatch
    sys_mod = sys.modules.get("triage") or sys.modules.get("__main__")
    WILD, DYN, normalize, pathlike = sys_mod.WILD, sys_mod.DYN, sys_mod.normalize, sys_mod.pathlike
    dynamic = DYN in strings
    for raw in strings:
        if raw == DYN or not raw or len(raw) > 300:
            continue
        value = normalize(raw)
        if not value:
            continue
        if WILD in value:
            if not pathlike(value):
                continue
            pattern = value.replace(WILD, "*")
            forms = [pattern, "*/" + pattern, "*/" + pattern + ".*"]
            if dynamic:
                forms += [pattern + "/*", "*/" + pattern + "/*"]
            if any(fnmatch.fnmatchcase(full, f) for f in forms):
                return True
            continue
        for candidate in {value, value.removeprefix("tests/"), value.removeprefix("fixtures/"),
                          value.removeprefix("tests/fixtures/")}:
            if candidate in names:
                return True
            if dynamic and candidate in dirs:
                return True
        if dynamic and value in ("fixtures", "tests/fixtures", "tests"):
            return True
    return False


def build(analyzer, keyed, matches, test_rows, log) -> dict:
    """test_rows: [(test id, module, owner module, owner class, method node)]."""
    out = {}
    by_class: dict[tuple, list] = {}
    for row in test_rows:
        test_id, module = row[0], row[1]
        if row[3] is None:
            continue
        cls_name = test_id.split("::")[1]
        by_class.setdefault((module.name, cls_name), []).append(row)
    subclassed = set()
    for module in analyzer.modules.values():
        for cls in module.classes.values():
            for owner_module, owner in analyzer.mro(module, cls)[1:]:
                subclassed.add((owner_module.name, owner.name))

    for (mod_name, cls_name), rows in sorted(by_class.items()):
        module = analyzer.modules[mod_name]
        cls = module.classes[cls_name]
        for lifecycle, stmt, attr, (loader_module, loader), arg in list(_record_statements(analyzer, module, cls)):
            fixtures = [f for f, (names, dirs) in keyed.items() if arg in names or arg.strip("/") in names]
            if len(fixtures) != 1:
                continue
            fixture = fixtures[0]
            why = None
            if (mod_name, cls_name) in subclassed:
                why = "class is subclassed"
            stores, foreign = _attribute_sites(analyzer, attr)
            if stores != 1 or foreign:
                why = why or f"attribute {attr} stored {stores}x, {foreign} foreign uses"
            if loader_module.rel != "tests/helpers.py" or any(
                    isinstance(n, (ast.Import, ast.ImportFrom)) and any(
                        (a.name.split(".")[0] not in ("os", "json", "re")) for a in n.names)
                    for n in loader_module.tree.body):
                why = why or "loader module is not a plain stdlib helper"
            if why:
                log(f"[triage-records] {fixture}: no narrowing ({why})")
                continue

            # Which tests still name the fixture once this statement is gone?
            index = lifecycle.body.index(stmt)
            lifecycle.body[index] = ast.Pass()
            labels = _strip_subtest_labels(module)
            analyzer.memo.clear()
            try:
                still = set()
                for test_id, tmod, owner_module, owner, node in rows:
                    strings = set(analyzer.class_strings(tmod, cls))
                    strings |= analyzer.function_strings(owner_module, node, analyzer.bind(node, None, {}, True),
                                                         (tmod, cls), 0)
                    names, dirs = keyed[fixture]
                    if names_file(strings, names, dirs, fixture[len("tests/"):]):
                        still.add(test_id)
            finally:
                lifecycle.body[index] = stmt
                for call, args, keywords in labels:
                    call.args, call.keywords = args, keywords
                analyzer.memo.clear()

            data_attrs = _data_attrs(analyzer, module, cls)
            entries = {}
            # Lifecycle code other than the loading statement. setUpClass runs once before
            # every test and tearDownClass after all of them, so only their use of the
            # records matters (it makes every test a user). setUp/tearDown run around each
            # test: what they touch counts as touched by every test.
            per_test = Usage(analyzer, attr, data_attrs)
            once = Usage(analyzer, attr, data_attrs)
            shared_unknown = False
            try:
                for owner_module, owner in analyzer.mro(module, cls):
                    for node in owner.body:
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
                                "setUp", "tearDown", "setUpClass", "tearDownClass"):
                            body = node
                            if node is lifecycle:
                                body = ast.Module(body=[s for s in node.body if s is not stmt], type_ignores=[])
                            target = per_test if node.name in ("setUp", "tearDown") else once
                            target.function(owner_module, body, (owner_module, owner), frozenset(), 0)
            except Unknown as error:
                shared_unknown = True
                log(f"[triage-records] {fixture}: lifecycle use unknown ({error})")
            for test_id, tmod, owner_module, owner, node in rows:
                usage = Usage(analyzer, attr, data_attrs)
                try:
                    usage.function(owner_module, node, (tmod, cls), frozenset(), 0)
                except Unknown:
                    usage.fields = ALL
                    usage.touch = set(data_attrs)
                    usage.mutate = set(data_attrs)
                for life in (per_test, once):
                    if life.fields is not None:
                        usage.add(life.fields)
                if shared_unknown or test_id in still:
                    usage.fields = ALL
                fields = usage.fields
                usage.touch |= per_test.touch
                usage.mutate |= per_test.mutate
                entries[test_id] = {
                    "use": fields if fields in (None, ALL) else sorted(fields),
                    "touch": sorted(usage.touch - {attr}),
                    "mutate": sorted(usage.mutate - {attr}) + ([attr] if attr in usage.mutate else []),
                }
            out[fixture] = {"attr": attr, "loader": [loader_module.rel, loader.name], "arg": arg,
                            "tests": entries}
            users = {t: e["use"] for t, e in entries.items() if e["use"] is not None}
            log(f"[triage-records] {fixture}: {attr} used by {users}")
    return out
