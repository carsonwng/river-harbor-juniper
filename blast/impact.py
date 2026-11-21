"""Layer 3: map deferred source changes to the tests that can fail.

Owner: strat/impact.

select(changes, ctx) gets every FileChange triage deferred and returns the test
numbers to run. Maps come from build/impact.py (per-test coverage of the pinned
sqlglot, an import-time probe, class/name usage); without them, every test.

Per changed sqlglot .py file:
  1. Rebuild the new file from the pinned source and the hunks; a patch that does
     not apply, or a new file that does not compile, selects every test.
  2. Identical ASTs (comments, blank lines, formatting) select nothing.
  3. Every edited line is placed in the old (removed lines) or new (added lines)
     AST and handled by where it sits:
     - inside a function body: the tests that ran the enclosing statement (its
       header for compound statements). A wholly new statement uses its previous
       surviving sibling, or the enclosing block. Lines that also ran while some
       module was imported add that module's users (everyone for core sqlglot).
     - at class or module level (runs at import): the names it binds. A name that
       import-time code reads propagates, so the scope's users; otherwise the tests
       that ran code loading the name, intersected with the scope's users.
     - lambda bodies inside class-level tables: the tests that ran the lambda.
  Anything unexpected selects every test.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import astcache
from .context import Context
from .patch import FileChange

_CACHE: dict[str, "Maps | None"] = {}
CODING = re.compile(r"^[ \t\f]*#.*?coding[:=]")
VERBOSE = os.environ.get("BR_VERBOSE") == "1"


class Unsafe(Exception):
    """Fall back to every test for this change."""


def _log(message: str) -> None:
    if VERBOSE:
        print(f"[impact] {message}", file=sys.stderr)


# ---------------------------------------------------------------- maps


class Maps:
    def __init__(self, root: Path, ctx: Context) -> None:
        self.root = root
        meta = json.loads((root / "meta.json").read_text("utf-8"))
        self.meta = meta
        self.sqlglot = self._find_sqlglot(meta.get("sqlglot"))
        self.tests = meta["tests"]
        self._sets_raw: list[str] | None = None
        self._sets: dict[int, int] = {}
        self._files: dict[str, dict | None] = {}
        self._names = self._classes = self._modules = None
        # bit index -> test number; tests the map does not know always run
        self.number = [ctx.by_id.get(t) for t in self.tests]
        known = {n for n in self.number if n is not None}
        self.always = {n for n in ctx.all_tests if n not in known}
        self.always |= {ctx.by_id[t] for t in meta.get("unsure", []) if t in ctx.by_id}
        self.ALL = (1 << len(self.tests)) - 1
        self.seconds = ctx.seconds

    @staticmethod
    def _find_sqlglot(recorded: str | None) -> Path:
        for candidate in (os.environ.get("BR_SQLGLOT"), recorded, "/opt/sqlglot",
                          str(Path(__file__).resolve().parent.parent / ".cache" / "sqlglot")):
            if candidate and (Path(candidate) / "sqlglot" / "__init__.py").exists():
                return Path(candidate)
        raise Unsafe("no sqlglot source")

    def _json(self, name: str):
        return json.loads((self.root / name).read_text("utf-8"))

    def bits(self, index: int) -> int:
        got = self._sets.get(index)
        if got is None:
            if self._sets_raw is None:
                self._sets_raw = self._json("sets.json")
            got = self._sets[index] = int(self._sets_raw[index], 16)
        return got

    def file(self, rel: str) -> dict | None:
        if rel not in self._files:
            path = self.root / "lines" / (rel.replace("/", "__") + ".json")
            self._files[rel] = json.loads(path.read_text("utf-8")) if path.exists() else None
        return self._files[rel]

    def relprof(self) -> dict | None:
        """Relation profile of comparison sites (build/impact.py relprof_post)."""
        if not hasattr(self, "_relprof"):
            path = self.root / "relprof.json"
            data = None
            try:
                if path.exists():
                    data = json.loads(path.read_text("utf-8"))
                    if data.get("version") != 1 or not data.get("complete"):
                        data = None
            except Exception:  # noqa: BLE001
                data = None
            if data is not None:
                pos = {t: i for i, t in enumerate(self.tests)}
                unsure = 0
                for t in data.get("unsure", []):
                    if t in pos:
                        unsure |= 1 << pos[t]
                data["unsure_bits"] = unsure
            self._relprof = data
        return self._relprof

    def lambdas(self) -> dict | None:
        """{rel: [first line of each lambda that runs during import]} or None."""
        if not hasattr(self, "_lambdas"):
            data = None
            try:
                path = self.root / "lambdas.json"
                if path.exists():
                    raw = json.loads(path.read_text("utf-8"))
                    if raw.get("version") == 1 and raw.get("complete") and not raw.get("errors"):
                        data = raw["started"]
            except Exception:  # noqa: BLE001
                data = None
            self._lambdas = data
        return self._lambdas

    def code_readers(self) -> int | None:
        """Bits of tests that ran code reading code objects or source, or None."""
        if not hasattr(self, "_code_readers"):
            bits = None
            try:
                raw = json.loads((self.root / "code_readers.json").read_text("utf-8"))
                if raw.get("version") == 1:
                    pos = {t: i for i, t in enumerate(self.tests)}
                    bits = 0
                    for t in raw["tests"]:
                        if t not in pos:
                            raise ValueError(t)
                        bits |= 1 << pos[t]
            except Exception:  # noqa: BLE001
                bits = None
            self._code_readers = bits
        return self._code_readers

    def import_graph(self) -> dict | None:
        """{"core": [...], "loaded": {module: [...]}, "errors": [...]} or None."""
        try:
            raw = json.loads((self.root / "imports.json").read_text("utf-8"))
            return raw if raw.get("version") == 1 else None
        except Exception:  # noqa: BLE001
            return None

    def import_errors(self) -> set[str] | None:
        """Modules that fail to import at the pin (lambda probe), or None."""
        try:
            raw = json.loads((self.root / "lambdas.json").read_text("utf-8"))
            return {e.split(":", 1)[0] for e in raw.get("errors", [])}
        except Exception:  # noqa: BLE001
            return None

    def names(self) -> dict:
        if self._names is None:
            self._names = self._json("names.json")
        return self._names

    def classes(self) -> dict:
        if self._classes is None:
            self._classes = self._json("classes.json")
        return self._classes

    def modules(self) -> dict:
        if self._modules is None:
            self._modules = self._json("modules.json")
        return self._modules

    def module_users(self, module: str | None) -> int:
        if not module:
            return self.ALL
        index = self.modules()["users"].get(module)
        return self.ALL if index is None else self.bits(index)

    def class_users(self, module: str | None, qualname: str) -> int:
        index = self.classes().get(f"{module}:{qualname}")
        if index is None:
            # not a class we saw at build time (e.g. defined under a condition)
            return self.module_users(module)
        return self.bits(index)

    def to_tests(self, bits: int) -> set[int]:
        out = set(self.always)
        number = self.number
        while bits:
            low = bits & -bits
            n = number[low.bit_length() - 1]
            if n is not None:
                out.add(n)
            bits ^= low
        return out


def _maps(ctx: Context) -> Maps | None:
    root = ctx.layer_maps("impact")
    key = str(root)
    if key not in _CACHE:
        try:
            _CACHE[key] = Maps(root, ctx) if (root / "meta.json").exists() else None
        except Exception as exc:  # noqa: BLE001
            _log(f"maps unusable: {exc!r}")
            _CACHE[key] = None
    return _CACHE[key]


# ---------------------------------------------------------------- entry


def select(changes: list[FileChange], ctx: Context) -> set[int]:
    if not changes:
        return set()
    maps = _maps(ctx)
    if maps is None:
        return ctx.all_tests
    bits = 0
    maps.pruned = False
    maps.import_sources = {}
    for change in changes:
        try:
            bits |= _change_bits(change, maps)
        except Unsafe as exc:
            _log(f"{change.path}: all tests ({exc})")
            return ctx.all_tests
        except Exception as exc:  # noqa: BLE001
            _log(f"{change.path}: all tests (error {exc!r})")
            return ctx.all_tests
        if bits == maps.ALL:
            return ctx.all_tests
    if maps.import_sources and not _imports_cleanly(maps):
        return ctx.all_tests
    if not bits and maps.pruned and maps.always:
        return set(maps.always)  # tests the profile never ran
    return maps.to_tests(bits) if bits else set()


# ---------------------------------------------------------------- patch -> files


def _apply(old_lines: list[str], change: FileChange):
    """New file lines, plus maps between old and new line numbers (1-based)."""
    new: list[str] = []
    new_to_old: dict[int, int] = {}
    cursor = 1  # next old line to copy
    for hunk in change.hunks:
        start = hunk.old_start if hunk.old_count else hunk.old_start + 1
        if start < cursor:
            raise Unsafe("overlapping hunks")
        while cursor < start:
            new.append(old_lines[cursor - 1])
            new_to_old[len(new)] = cursor
            cursor += 1
        removed = dict(hunk.removed)
        added = dict(hunk.added)
        old_no, new_no = start, hunk.new_start if hunk.new_count else hunk.new_start + 1
        old_end = start + hunk.old_count
        new_end = new_no + hunk.new_count
        if len(new) + 1 != new_no:
            raise Unsafe("hunk offsets disagree")
        while old_no < old_end or new_no < new_end:
            if old_no in removed:
                if old_no > len(old_lines) or old_lines[old_no - 1].rstrip("\r\n") != removed[old_no]:
                    raise Unsafe("removed line does not match source")
                old_no += 1
            elif new_no in added:
                new.append(added[new_no])
                new_no += 1
            else:
                if old_no > len(old_lines):
                    raise Unsafe("context beyond end of file")
                new.append(old_lines[old_no - 1].rstrip("\r\n"))
                new_to_old[len(new)] = old_no
                old_no += 1
                new_no += 1
        cursor = old_end
    while cursor <= len(old_lines):
        new.append(old_lines[cursor - 1].rstrip("\r\n"))
        new_to_old[len(new)] = cursor
        cursor += 1
    return new, new_to_old


# ---------------------------------------------------------------- AST helpers

BLOCK_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")


def _blocks(node):
    for name in BLOCK_FIELDS:
        value = getattr(node, name, None)
        if isinstance(value, list) and value:
            yield value


def _children(node):
    """Statement-like children (statements, except handlers, match cases)."""
    for block in _blocks(node):
        for child in block:
            yield child


def _span(node) -> tuple[int, int]:
    first = node.lineno
    for deco in getattr(node, "decorator_list", ()) or ():
        first = min(first, deco.lineno)
    return first, node.end_lineno


def _chain(tree: ast.Module, line: int) -> list:
    """Nested statement-like nodes containing `line`, outermost first."""
    chain = []
    nodes = tree.body
    while True:
        for node in nodes:
            lo, hi = _span(node)
            if lo <= line <= hi:
                chain.append(node)
                nodes = list(_children(node))
                break
        else:
            return chain


def _first_body_line(node) -> int | None:
    body = getattr(node, "body", None)
    if isinstance(body, list) and body:
        return _span(body[0])[0]
    return None


def _is_docstring(node, parent) -> bool:
    body = getattr(parent, "body", None)
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str) and isinstance(body, list)
            and body and body[0] is node)


FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _bound(node) -> set[str] | None:
    """Names a class/module-level statement binds or mutates; None = unknown."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return None
    names: set[str] = set()

    def target(t):
        if isinstance(t, ast.Name):
            names.add(t.id)
        elif isinstance(t, ast.Attribute):
            names.add(t.attr)
            root = t.value
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if isinstance(root, ast.Name):
                names.add(root.id)
        elif isinstance(t, ast.Subscript):
            target(t.value)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                target(e)
        elif isinstance(t, ast.Starred):
            target(t.value)

    if isinstance(node, ast.Assign):
        for t in node.targets:
            target(t)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        target(node.target)
    elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        func = node.value.func  # KEYWORDS.update(...), register(...)
        if isinstance(func, ast.Attribute):
            target(func.value)
        else:
            return None
    elif isinstance(node, ast.Delete):
        for t in node.targets:
            target(t)
    elif isinstance(node, ast.Pass):
        return set()
    else:
        return None  # if/try/for/with at import time: treat the scope as touched
    if not names or any(n.startswith("__") and n.endswith("__") for n in names):
        return None
    return names


# ---------------------------------------------------------------- per file


class FileInfo:
    def __init__(self, maps: Maps, rel: str, source: str) -> None:
        self.maps = maps
        self.rel = rel
        self.data = maps.file(rel) or {"lines": {}, "import": {}, "module": None}
        self.module = self.data.get("module")
        self.line_sets = self.data["lines"]
        self.import_lines = self.data["import"]
        self.code_lines = astcache.code_lines(rel, source)

    def cov(self, lo: int, hi: int) -> int:
        bits = 0
        for line in range(lo, hi + 1):
            index = self.line_sets.get(str(line))
            if index is not None:
                bits |= self.maps.bits(index)
        return bits

    def has_code(self, lo: int, hi: int) -> bool:
        return any(l in self.code_lines for l in range(lo, hi + 1))

    def import_bits(self, lo: int, hi: int) -> int:
        """Lines of a function body that also ran during some module's import."""
        bits = 0
        for line in range(lo, hi + 1):
            modules = self.import_lines.get(str(line))
            if not modules:
                continue
            if "core" in modules:
                return self.maps.ALL
            for module in modules:
                bits |= self.maps.module_users(module)
        return bits


def _change_bits(change: FileChange, maps: Maps) -> int:
    path = change.path
    if change.binary or not change.old_path or not change.new_path:
        raise Unsafe("added, deleted or binary file")
    if change.old_path != change.new_path:
        raise Unsafe("rename")
    if not path.startswith("sqlglot/"):
        raise Unsafe("not sqlglot source")
    if not path.endswith(".py"):
        raise Unsafe("non-python file in sqlglot")
    source_path = maps.sqlglot / path
    if not source_path.exists():
        raise Unsafe("unknown file")
    old_text = source_path.read_text("utf-8")
    old_lines = old_text.splitlines()
    new_lines, new_to_old = _apply(old_lines, change)
    new_text = "\n".join(new_lines) + "\n"
    for hunk in change.hunks:
        for number, text in hunk.removed + hunk.added:
            if number <= 2 and CODING.match(text):
                raise Unsafe("encoding cookie")
    try:
        old_tree = astcache.parse(old_text)
        new_tree = astcache.parse(new_text)
        compile(new_tree, path, "exec", dont_inherit=True)  # same raise as compile(new_text)
    except SyntaxError as exc:
        raise Unsafe(f"does not compile: {exc.msg}")
    if astcache.same(old_tree, new_tree):
        _log(f"{path}: AST unchanged")
        return 0
    info = FileInfo(maps, path, old_text)
    if _runs_at_import(info, old_tree, new_tree, change):
        maps.import_sources[path] = new_text
    lam = _lambda_only_bits(info, old_tree, new_tree)
    if lam is not None:
        _log(f"{path}: only import-time lambda bodies changed")
        bits = lam
    else:
        old_to_new = {o: n for n, o in new_to_old.items()}
        bits = 0
        for hunk in change.hunks:
            for number, text in hunk.removed:
                bits |= _old_line_bits(info, old_tree, number, text, hunk, new_lines, old_to_new)
            for number, _text in hunk.added:
                bits |= _new_line_bits(info, new_tree, number, new_to_old)
            if bits == maps.ALL:
                break
    if (bits and bits != maps.ALL and _assign_only(change, old_tree, new_tree)
            and _in_tracked_classes(maps, path, change, old_tree, new_tree)):
        alt = _expr_state_bits(maps, path, new_text)
        if alt is not None:
            _log(f"{path}: expression attribute diff selects {bin(alt).count('1')} "
                 f"(base {bin(bits).count('1')})")
            return alt
    if bits:
        try:
            refined = _relprof_refine(info, old_tree, new_tree, bits, old_lines, new_lines)
        except Exception as exc:  # noqa: BLE001
            _log(f"{path}: relprof error {exc!r}")
            refined = None
        if refined is not None:
            maps.pruned = True
            bits = refined
    if (bits and path.startswith(TYPING) and _worth_a_diff(maps, bits)
            and _outside_functions(change, old_tree, new_tree)):
        alt = _typing_meta_bits(maps, path, new_text)
        if alt is not None:
            # both are sound supersets of the tests the edit can change: keep the overlap
            _log(f"{path}: typing metadata diff selects {bin(alt).count('1')} "
                 f"(base {bin(bits).count('1')})")
            if alt == maps.ALL:
                return alt
            bits &= alt
    return bits


# ---------------------------------------------------------------- import-time lambdas
#
# (strat/gap-mix) A class- or module-level statement runs at import. When the old
# and the new statement are the same AST except inside the bodies of lambdas, the
# import runs the same code and binds the same values, except that those lambdas
# carry new code: creating a lambda does not evaluate its body. So until a lambda
# is called the patched program is the pinned one, and a test can only change by
# calling one of the edited lambdas, which runs its body: the tests that ran the
# old body's lines. This needs the lambda not to run during import itself, which
# build/impact_lib/br_lambda_probe.py records (no record: no narrowing). The
# add/remove line rules otherwise take the whole statement's names, which for an
# import-time table read by a metaclass is every user of the class or module.


def _blank_bodies(tree, keep_lambdas: set[int]):
    """Copy of a module with every function body, and the body of every lambda
    that does not run at import, replaced: what is left runs at import."""
    import copy

    tree = copy.deepcopy(tree)
    for node in ast.walk(tree):
        if isinstance(node, FUNC):
            node.body = [ast.Pass()]
        elif isinstance(node, ast.Lambda) and node.lineno not in keep_lambdas:
            node.body = ast.Constant(None)
    return tree


def _runs_at_import(info: "FileInfo", old_tree, new_tree, change: FileChange) -> bool:
    """The edit changes code that runs while the module is imported: anything
    outside function and lambda bodies, or a removed line that ran at import."""
    for hunk in change.hunks:
        for number, _text in hunk.removed:
            if str(number) in info.import_lines:
                return True
    started = info.maps.lambdas()
    at_import = set(started.get(info.rel, ())) if started is not None else None
    if at_import is None:
        return True
    # == ast.dump(_blank_bodies(old_tree, at_import)) != ast.dump(_blank_bodies(new_tree, at_import))
    return not astcache.same(old_tree, new_tree, funcs=True, lambdas=at_import)


def _imports_cleanly(maps: Maps) -> bool:
    """Every sqlglot module that imports at the pin still imports with the new
    sources (blast/impact_import.py). False, and so every test, on any doubt."""
    from . import impact_import

    modules = None
    graph = maps.import_graph()
    if graph is not None:
        edited = set()
        for rel in maps.import_sources:
            parts = rel[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            edited.add(".".join(parts))
        if not edited & set(graph["core"]):
            modules = sorted(edited | set(graph["errors"]) |
                             {m for m, loaded in graph["loaded"].items() if edited & set(loaded)})
    try:
        failed = impact_import.check(maps.sqlglot, maps.import_sources, modules)
    except Exception as exc:  # noqa: BLE001
        _log(f"import check failed: {exc!r}")
        return False
    pinned = maps.import_errors()
    new = sorted(m for m in failed if pinned is None or m not in pinned)
    if new:
        _log(f"import fails with the patch: {new[:5]}")
        return False
    return True


def _pair_import_level(old_list, new_list, out) -> bool:
    """Pair module/class-level statements; differing non-def statements go to out.
    False when the bodies do not pair up or a function or class header differs."""
    if len(old_list) != len(new_list):
        return False
    for a, b in zip(old_list, new_list):
        if type(a) is not type(b):
            return False
        if astcache.same(a, b):
            continue
        if isinstance(a, ast.ClassDef):
            if not astcache.same(_header_copy(a), _header_copy(b)):
                return False
            if not _pair_import_level(a.body, b.body, out):
                return False
        elif isinstance(a, FUNC):
            return False
        else:
            out.append((a, b))
    return True


_NO_LINES: frozenset = frozenset()


def _lambda_only_bits(info: "FileInfo", old_tree, new_tree) -> int | None:
    started = info.maps.lambdas()
    readers = info.maps.code_readers()
    if started is None or readers is None:
        return None
    at_import = set(started.get(info.rel, ()))
    pairs: list = []
    if not _pair_import_level(old_tree.body, new_tree.body, pairs) or not pairs:
        return None
    bits = 0
    for a, b in pairs:
        la = [n for n in ast.walk(a) if isinstance(n, ast.Lambda)]
        lb = [n for n in ast.walk(b) if isinstance(n, ast.Lambda)]
        if not la or len(la) != len(lb):
            return None
        # every lambda body blanked on both sides (was: deepcopy, blank, ast.dump)
        if not astcache.same(a, b, lambdas=_NO_LINES):
            return None  # something outside a lambda body changed
        for x, y in zip(la, lb):
            if astcache.same(x.body, y.body):
                continue
            if x.lineno in at_import:
                return None  # some lambda starting on this line runs during import
            bits |= info.cov(x.body.lineno, x.body.end_lineno)
    # a test that reads code objects or source can see the new code without a call
    return bits | readers


def _scope(chain: list, upto: int) -> list[str]:
    """Class qualname parts enclosing chain[upto] (no functions in between)."""
    parts = []
    for node in chain[:upto]:
        if isinstance(node, ast.ClassDef):
            parts.append(node.name)
        elif isinstance(node, FUNC):
            return []  # inside a function: not a class scope
    return parts


def _scope_users(info: FileInfo, chain: list, upto: int) -> int:
    parts = _scope(chain, upto)
    if parts:
        return info.maps.class_users(info.module, ".".join(parts))
    return info.maps.module_users(info.module)


def _name_bits(info: FileInfo, names: set[str] | None, base: int) -> int:
    if names is None:
        return base
    table = info.maps.names()
    bits = 0
    for name in names:
        entry = table.get(name)
        if entry is None or entry[1]:
            return base  # unknown or read at import time: propagates
        bits |= info.maps.bits(entry[0]) & base
    return bits


def _func_index(chain: list) -> int | None:
    """Index of the innermost function in chain, or None."""
    for i in range(len(chain) - 1, -1, -1):
        if isinstance(chain[i], FUNC):
            return i
    return None


def _body_stmt_bits(info: FileInfo, chain: list, fi: int, line: int) -> int:
    """`line` lies in the body of function chain[fi]; chain[-1] is the innermost
    statement containing it."""
    node = chain[-1]
    if node is chain[fi]:
        return 0  # a blank or comment line between the function's statements
    lo, hi = _span(node)
    first = _first_body_line(node)
    if first is not None and line < first:
        hi = first - 1  # compound statement header; else:/except: lines keep the whole range
    bits = info.cov(lo, hi) if info.has_code(lo, hi) else _sibling_bits(info, chain, len(chain) - 1)
    return bits | info.import_bits(lo, hi)


def _sibling_bits(info: FileInfo, chain: list, index: int, new_to_old=None) -> int:
    """For a statement without code of its own (or wholly new): tests that reached
    its position, i.e. ran its previous sibling, or the enclosing statement."""
    node = chain[index]
    parent = chain[index - 1]
    for block in _blocks(parent):
        if node in block:
            at = block.index(node)
            for prev in reversed(block[:at]):
                lo, hi = _map_span(_span(prev), new_to_old)
                if lo is not None and info.has_code(lo, hi):
                    return info.cov(lo, hi)
    lo, hi = _map_span(_span(parent), new_to_old)
    if lo is None:
        raise Unsafe("cannot place new statement")
    return info.cov(lo, hi)


def _map_span(span, new_to_old):
    lo, hi = span
    if new_to_old is None:
        return lo, hi
    # an edited new line stands for the old lines between its surviving neighbours
    if lo in new_to_old:
        old_lo = new_to_old[lo]
    else:
        prev = lo - 1
        while prev > 0 and prev not in new_to_old:
            prev -= 1
        old_lo = new_to_old[prev] + 1 if prev > 0 else 1
    if hi in new_to_old:
        old_hi = new_to_old[hi]
    else:
        nxt, limit = hi + 1, max(new_to_old, default=0)
        while nxt <= limit and nxt not in new_to_old:
            nxt += 1
        old_hi = new_to_old[nxt] - 1 if nxt <= limit else max(new_to_old.values(), default=1)
    if old_lo > old_hi:  # pure insertion: the old lines around it
        old_lo, old_hi = old_hi, old_lo
    return old_lo, old_hi


def _lambda_bits(info: FileInfo, stmt, line: int, old_text: str, new_text: str | None) -> int | None:
    """A class-level edit confined to one lambda body: tests that ran the lambda."""
    if new_text is None:
        return None
    a, b = old_text, new_text
    p = 0
    while p < min(len(a), len(b)) and a[p] == b[p]:
        p += 1
    s = 0
    while s < min(len(a), len(b)) - p and a[-1 - s] == b[-1 - s]:
        s += 1
    lo_col, hi_col = p, len(a) - s  # changed columns in the old line
    best = None
    for node in ast.walk(stmt):
        if isinstance(node, ast.Lambda):
            body = node.body
            start = (body.lineno, body.col_offset)
            end = (body.end_lineno, body.end_col_offset)
            if start <= (line, lo_col) and (line, hi_col) <= end:
                if best is None or (node.lineno, node.col_offset) > (best.lineno, best.col_offset):
                    best = node
    if best is None:
        return None
    lo, hi = best.body.lineno, best.body.end_lineno
    return info.cov(lo, hi)


def _old_line_bits(info, tree, line, text, hunk, new_lines, old_to_new) -> int:
    chain = _chain(tree, line)
    if not chain:
        return 0  # blank or comment line at module level
    fi = _func_index(chain)
    if fi is not None:
        func = chain[fi]
        first = _first_body_line(func)
        if first is not None and line >= first:
            if len(chain) - 1 > fi and _is_docstring(chain[fi + 1], func) and len(chain) == fi + 2:
                return info.cov(*_span(func)) | _doc_bits(info)
            return _body_stmt_bits(info, chain, fi, line)
        # decorator, signature or defaults of a function
        bits = info.cov(*_span(func))
        if fi == 0 or not any(isinstance(n, FUNC) for n in chain[:fi]):
            base = _scope_users(info, chain, fi)
            names = info.maps.names()
            entry = names.get(func.name)
            if entry is None or entry[1]:
                bits |= base
            else:
                bits |= info.maps.bits(entry[0]) & base
        else:
            bits |= info.cov(*_span(chain[fi - 1]))  # nested def: runs with its parent
        return bits
    # class or module level
    stmt = chain[-1]
    index = len(chain) - 1
    if isinstance(stmt, ast.ClassDef):
        first = _first_body_line(stmt)
        if first is None or line < first:  # class header: bases, decorators, keywords
            parts = _scope(chain, index) + [stmt.name]
            return info.maps.class_users(info.module, ".".join(parts))
        # a line between statements of the class body (comment); nothing
        return 0
    parent = chain[-2] if len(chain) > 1 else tree
    if _is_docstring(stmt, parent):
        return _doc_bits(info) | (_scope_users(info, chain, index) if _doc_propagates(info) else 0)
    # one-line edits inside a lambda of a class-level table
    if len(hunk.removed) == len(hunk.added) and len(hunk.removed) >= 1:
        offset = [n for n, _ in hunk.removed].index(line)
        replaced = hunk.added[offset][1]
        lam = _lambda_bits(info, stmt, line, text, replaced)
        if lam is not None:
            return lam
    base = _scope_users(info, chain, index)
    return _name_bits(info, _bound(stmt), base)


def _doc_bits(info: FileInfo) -> int:
    entry = info.maps.names().get("__doc__")
    return info.maps.bits(entry[0]) if entry else 0


def _doc_propagates(info: FileInfo) -> bool:
    entry = info.maps.names().get("__doc__")
    return entry is None or bool(entry[1])


def _new_line_bits(info, tree, line, new_to_old) -> int:
    chain = _chain(tree, line)
    if not chain:
        return 0
    # the innermost statement that survives from the old file
    survivor = None
    for i in range(len(chain) - 1, -1, -1):
        lo, hi = _span(chain[i])
        if any(n in new_to_old for n in range(lo, hi + 1)):
            survivor = i
            break
    fi = _func_index(chain)
    if survivor is None:
        # a wholly new top-level statement
        node = chain[0]
        if isinstance(node, ast.ClassDef):
            return info.maps.module_users(info.module)
        names = _bound(node)
        return _name_bits(info, names, info.maps.module_users(info.module))
    if survivor == len(chain) - 1:
        # added line inside an existing statement: same as editing it
        node = chain[survivor]
        if fi is not None:
            first = _first_body_line(chain[fi])
            if first is not None and line >= first:
                lo, hi = _map_span(_span(node), new_to_old)
                child_first = _first_body_line(node) if survivor > fi else None
                if child_first is not None and line < child_first:
                    lo, hi = _map_span((_span(node)[0], child_first - 1), new_to_old)
                if lo is None:
                    raise Unsafe("cannot map statement")
                return info.cov(lo, hi) | info.import_bits(lo, hi)
            lo, hi = _map_span(_span(chain[fi]), new_to_old)
            return info.cov(lo, hi) | _scope_users(info, chain, fi)
        if isinstance(node, ast.ClassDef):
            parts = _scope(chain, survivor) + [node.name]
            return info.maps.class_users(info.module, ".".join(parts))
        return _name_bits(info, _bound(node), _scope_users(info, chain, survivor))
    # chain[survivor + 1] is wholly new
    new_node = chain[survivor + 1]
    in_func = fi is not None and fi <= survivor
    if in_func:
        if isinstance(new_node, ast.ClassDef):
            return _sibling_bits(info, chain, survivor + 1, new_to_old)
        return _sibling_bits(info, chain, survivor + 1, new_to_old)
    # new statement in a class (or module-level compound) body
    if isinstance(new_node, (ast.ClassDef,) + FUNC):
        return _scope_users(info, chain, survivor + 1)
    if _is_docstring(new_node, chain[survivor]):
        return _doc_bits(info) | _scope_users(info, chain, survivor + 1)
    return _name_bits(info, _bound(new_node), _scope_users(info, chain, survivor + 1))


# ---------------------------------------------------------------- expression class attributes
#
# An edit confined to class-level assignments changes the program only through
# the values those statements bind at import. The edited sqlglot is imported once
# (build/impact_lib/br_state.py diff, from strat/impact-attrs) and every class
# attribute, class-level dict entry and module global is compared with the
# pristine fingerprints from the build: that is the whole import-time effect,
# after __init_subclass__ (required_args), merged subclass tables, derived module
# tables. When every change is a plain data attribute of a sqlglot Expr class
# (arg_types, required_args, is_var_len_args, _sql_names, ...), the tests that can
# see it are the ones that read that attribute of that class at run time: the
# coverage pass records every such read through a descriptor (br_cov_plugin.py),
# for the class it was read through and the class that defined the value. Any
# other change (a module global, a parser or generator table, a new or removed
# attribute or class), an entry the profile marks unsafe, or a failed diff keeps
# the base selection; an edit that breaks an import selects every test.


def _assign_line(tree, line: int) -> bool:
    """The line is in a class-level assignment statement (not in a function, a
    lambda, a class header or under a class-level if/for), or blank/comment."""
    chain = _chain(tree, line)
    if not chain:
        return True
    if any(isinstance(n, FUNC) for n in chain):
        return False
    stmt = chain[-1]
    if isinstance(stmt, ast.ClassDef):
        first = _first_body_line(stmt)
        return first is not None and line >= first
    outer = chain[:-1]
    return bool(outer) and all(isinstance(n, ast.ClassDef) for n in outer)         and isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign))


def _assign_only(change: FileChange, old_tree, new_tree) -> bool:
    if os.environ.get("BR_IMPACT_EXPRATTR", "1") != "1":
        return False
    seen = False
    for hunk in change.hunks:
        for number, _ in hunk.removed:
            seen = True
            if not _assign_line(old_tree, number):
                return False
        for number, _ in hunk.added:
            seen = True
            if not _assign_line(new_tree, number):
                return False
    return seen


def _exprattr(maps: Maps) -> dict | None:
    if not hasattr(maps, "_exprattr"):
        data = None
        try:
            path = maps.root / "exprattr.json"
            if path.exists():
                data = json.loads(path.read_text("utf-8"))
                if data.get("version") != 1 or "*" in data.get("unsafe", ()):
                    data = None
                else:
                    data["tracked"] = set(data["tracked"])
                    data["unsafe"] = set(data["unsafe"])
        except Exception:  # noqa: BLE001
            data = None
        maps._exprattr = data
    return maps._exprattr


def _in_tracked_classes(maps: Maps, rel: str, change: FileChange, old_tree, new_tree) -> bool:
    """Every edited line sits in a class the attribute profile tracks (a sqlglot Expr
    class with plain data attributes). Otherwise the state diff is not run: a changed
    attribute of any other class is untracked, so the diff could only keep the base
    selection, and skipping it saves the edited sqlglot's import (about 0.3-1 s of CPU
    per commit). Skipping always keeps the base selection, so this never drops a test."""
    profile = _exprattr(maps)
    if profile is None:
        return False
    classes = profile.get("tracked_classes")
    if classes is None:
        classes = profile["tracked_classes"] = {e.split("|", 1)[0] for e in profile["tracked"]}
    module = rel[:-3].replace("/", ".")
    if module.endswith(".__init__"):
        module = module[: -len(".__init__")]
    seen = False
    for tree, lines in ((old_tree, [n for h in change.hunks for n, _ in h.removed]),
                        (new_tree, [n for h in change.hunks for n, _ in h.added])):
        for line in lines:
            names = [n.name for n in _chain(tree, line) if isinstance(n, ast.ClassDef)]
            if not names:
                continue  # blank or comment line (_assign_only placed it)
            if f"{module}:{'.'.join(names)}" not in classes:
                return False
            seen = True
    return seen


def _expr_state_bits(maps: Maps, rel: str, new_text: str) -> int | None:
    profile = _exprattr(maps)
    state = maps.root / "attrs" / "state.json"
    script = Path(__file__).resolve().parent.parent / "build" / "impact_lib" / "br_state.py"
    if profile is None or not state.exists() or not script.exists():
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.update(PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
    try:
        proc = subprocess.run(
            [sys.executable, "-W", "ignore", str(script), "diff", "--sqlglot",
             str(maps.sqlglot.resolve()), "--state", str(state.resolve()), "--rel", rel],
            input=new_text, capture_output=True, text=True, encoding="utf-8", timeout=120,
            env=env, cwd=str(state.parent.resolve()))
        lines = proc.stdout.strip().splitlines()
        if not lines:
            raise RuntimeError(f"no output, exit {proc.returncode}: {proc.stderr[-300:]}")
        result = json.loads(lines[-1])
    except Exception as exc:  # noqa: BLE001
        _log(f"{rel}: state diff failed ({exc!r})")
        return None
    if result.get("import_failed"):
        _log(f"{rel}: {result.get('error')}: every test")
        return maps.ALL
    changes = result.get("changes")
    if changes is None or result.get("volatile"):
        _log(f"{rel}: state diff unusable: {result.get('error') or result.get('volatile')}")
        return None
    _log(f"{rel}: import-time changes {changes[:8]}")
    if not changes:
        return None  # nothing at import changed: the base rules decide
    reads, tracked, unsafe = profile["reads"], profile["tracked"], profile["unsafe"]
    bits = 0
    for ch in changes:
        if ch[0] != "class" or ch[4] not in ("value", "attr"):
            return None
        entry = f"{ch[1]}|{ch[2]}"
        if entry not in tracked or entry in unsafe or f"{ch[1]}|*" in unsafe:
            return None
        index = reads.get(entry)
        if index is not None:
            bits |= maps.bits(index)
    return bits


# ---------------------------------------------------------------- typing metadata tables
#
# (strat/r2-ledger) sqlglot.typing.* modules build EXPRESSION_METADATA tables
# (expression class -> annotation spec); every dialect class copies one, and the
# optimizer's TypeAnnotator looks entries up by the class of the node it annotates.
# The base rules give a module-level edit there every user of the module, or every
# test for sqlglot/typing/__init__.py, which the core import loads. An edit outside
# every function body changes the program only through what the module binds at
# import. br_meta.py imports the edited sqlglot once and compares (1) the whole
# import-time state with the pristine snapshot (br_state.py) and (2) every metadata
# table entry by entry. When the only differences are table entries, a test can see
# the edit only by looking up one of the changed keys, by reading a table as a whole
# (iteration, copy to a plain dict, comparison), or by reading code objects or source:
# the coverage pass records the first two per test (br_cov_plugin), code_readers.json
# the third. Anything else (another binding changed, a key that is not an expression
# class, a missing map, a failed diff) keeps the base selection; an edit that breaks
# an import selects every test.

TYPING = "sqlglot/typing/"
DIFF_WORTH_MS = 2000  # the diff child costs about 1 s of CPU; skip it when it cannot pay


def _worth_a_diff(maps: Maps, bits: int) -> bool:
    """The base selection's cost lower bound (largest test, or the load spread over 8
    workers, plus one worker) exceeds the cheapest non-empty selection (one worker)
    by more than DIFF_WORTH_MS. A CPU trade-off only: skipping keeps the base selection."""
    durations = [round(maps.seconds.get(t, 0) * 1000) for t in maps.to_tests(bits)]
    if not durations:
        return False
    bound = max(max(durations), -(-sum(durations) // 8)) + 2000
    return bound - 2000 > DIFF_WORTH_MS


def _outside_functions(change: FileChange, old_tree, new_tree) -> bool:
    seen = False
    for tree, lines in ((old_tree, [n for h in change.hunks for n, _ in h.removed]),
                        (new_tree, [n for h in change.hunks for n, _ in h.added])):
        for line in lines:
            seen = True
            if any(isinstance(n, FUNC) for n in _chain(tree, line)):
                return False
    return seen


def _metaread(maps: Maps) -> dict | None:
    if not hasattr(maps, "_metaread"):
        data = None
        try:
            path = maps.root / "metaread.json"
            if path.exists():
                raw = json.loads(path.read_text("utf-8"))
                if raw.get("version") == 2:
                    data = raw["reads"]
        except Exception:  # noqa: BLE001
            data = None
        maps._metaread = data
    return maps._metaread


def _typing_meta_bits(maps: Maps, rel: str, new_text: str) -> int | None:
    reads = _metaread(maps)
    readers = maps.code_readers()
    state = maps.root / "attrs" / "state.json"
    pristine = maps.root / "attrs" / "meta.json"
    script = Path(__file__).resolve().parent.parent / "build" / "impact_lib" / "br_meta.py"
    if reads is None or readers is None or not state.exists() or not pristine.exists()             or not script.exists():
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.update(PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
    try:
        proc = subprocess.run(
            [sys.executable, "-W", "ignore", str(script), "diff", "--sqlglot",
             str(maps.sqlglot.resolve()), "--state", str(state.resolve()),
             "--meta", str(pristine.resolve()), "--rel", rel],
            input=new_text, capture_output=True, text=True, encoding="utf-8", timeout=120,
            env=env, cwd=str(state.parent.resolve()))
        lines = proc.stdout.strip().splitlines()
        if not lines:
            raise RuntimeError(f"no output, exit {proc.returncode}: {proc.stderr[-300:]}")
        result = json.loads(lines[-1])
    except Exception as exc:  # noqa: BLE001
        _log(f"{rel}: typing metadata diff failed ({exc!r})")
        return None
    if result.get("import_failed"):
        _log(f"{rel}: {result.get('error')}: every test")
        return maps.ALL
    if result.get("foreign") or result.get("bad_keys") or "changed" not in result:
        _log(f"{rel}: typing metadata diff not confined to tables: {result.get('foreign', [])[:4]}")
        return None
    _log(f"{rel}: typing metadata entries changed {result['changed'][:8]}")
    # whole-table reads of a table no binding names ("?": a copy a test made) count
    # for every table; so does every read of a changed key through such a copy
    entries = {"?|*"}
    for binding, key in result["changed"]:
        entries.update((f"{binding}|{key}", f"{binding}|*", f"?|{key}"))
    bits = readers
    for entry in entries:
        index = reads.get(entry)
        if index is not None:
            bits |= maps.bits(index)
    return bits


# ---------------------------------------------------------------- relation profile
#
# An edit that only swaps the operator of single-operator comparisons (same
# operands, every other AST node identical) can change a run only at an evaluation
# of an edited comparison where the old and the new operator give different
# results: until the first such evaluation the patched program is the pinned one,
# step for step. build/impact.py records, for every comparison site, the relation
# signature of every evaluation in a whole suite pass (each operator's result on
# the operands: false, true, raises, or unknown when evaluating it would not be
# provably pure). If every recorded signature at every edited site gives the same
# known result (true or false) under both operators, no evaluation can be the
# first divergence, and no test can change, except the tests the profile does not
# vouch for (failed or behaved differently in the profile pass, started a child
# process, or were not in it). Everything else keeps the coverage selection.

_OP_INDEX = {ast.Is: 0, ast.IsNot: 1, ast.Eq: 2, ast.NotEq: 3, ast.Lt: 4, ast.LtE: 5,
             ast.Gt: 6, ast.GtE: 7}
_T, _F = 1, 0


_IN, _NOT_IN = 8, 9


def _op_index(op) -> int | None:
    if type(op) in _OP_INDEX:
        return _OP_INDEX[type(op)]
    return {ast.In: _IN, ast.NotIn: _NOT_IN}.get(type(op))


def _literal(node):
    """(True, value) for a literal expression, else (False, None)."""
    try:
        return True, ast.literal_eval(node)
    except Exception:  # noqa: BLE001
        return False, None


class _Edit:
    """One edited single-operator comparison: old node, old/new operator, and for a
    changed literal the side holding it and its old/new values."""

    def __init__(self, node, old_op, new_op, side=None, old_lit=None, new_lit=None):
        self.node, self.old_op, self.new_op = node, old_op, new_op
        self.side, self.old_lit, self.new_lit = side, old_lit, new_lit
        self.part = None


def _header_copy(node):
    """Shallow copy of a statement with its statement blocks emptied."""
    import copy

    head = copy.copy(node)
    for name in BLOCK_FIELDS:
        if isinstance(getattr(head, name, None), list):
            setattr(head, name, [])
    return head


def _text(lines: list[str], node) -> str:
    lo, hi = _span(node)
    return "\n".join(lines[lo - 1:hi])


def _differing(old_list, new_list, old_lines, new_lines, out) -> bool:
    """Pair two statement lists; append to `out` the (old, new, is_header) parts
    whose source differs, descending into compound statements whose blocks line
    up. A statement's AST depends only on its own source text, so equal text
    means equal AST. False when the lists cannot be paired."""
    if len(old_list) != len(new_list):
        return False
    for a, b in zip(old_list, new_list):
        if type(a) is not type(b):
            return False
        if _text(old_lines, a) == _text(new_lines, b):
            continue
        blocks_a = [getattr(a, n, None) for n in BLOCK_FIELDS]
        blocks_b = [getattr(b, n, None) for n in BLOCK_FIELDS]
        if any(isinstance(x, list) and x for x in blocks_a) and \
                all(isinstance(x, list) == isinstance(y, list) for x, y in zip(blocks_a, blocks_b)):
            out.append((a, b, True))
            for x, y in zip(blocks_a, blocks_b):
                if isinstance(x, list):
                    # except handlers and match cases are not statements: compare whole
                    if x and not isinstance(x[0], ast.stmt):
                        if len(x) != len(y):
                            return False
                        out.extend((p, q, False) for p, q in zip(x, y)
                                   if not astcache.same(p, q))
                    elif not _differing(x, y, old_lines, new_lines, out):
                        return False
        else:
            out.append((a, b, False))
    return True


def _walk_part(node, header: bool):
    """ast.walk over a statement, or over its header only (no statement blocks)."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        for name, value in ast.iter_fields(n):
            if header and n is node and name in BLOCK_FIELDS:
                continue
            if isinstance(value, list):
                stack.extend(v for v in reversed(value) if isinstance(v, ast.AST))
            elif isinstance(value, ast.AST):
                stack.append(value)


def _compare_edits(old_tree, new_tree, old_lines=None, new_lines=None):
    """Edits when the new tree is the old one with only the operator and/or a
    literal operand of single-operator comparisons changed; None otherwise."""
    if old_lines is None:
        parts = [(old_tree, new_tree, False)]
    else:
        parts = []
        if not _differing(old_tree.body, new_tree.body, old_lines, new_lines, parts):
            return None
    edits = []
    swaps = []
    for pa, pb, header in parts:
        old_cmp = [n for n in _walk_part(pa, header) if isinstance(n, ast.Compare)]
        new_cmp = [n for n in _walk_part(pb, header) if isinstance(n, ast.Compare)]
        if len(old_cmp) != len(new_cmp):
            return None
        for a, b in zip(old_cmp, new_cmp):
            if astcache.same(a, b):
                continue
            edit = _one_edit(a, b)
            if edit is None:
                return None
            edit.part = (pa, header)
            edits.append(edit)
            swaps.append((a, b))
    if not edits:
        return None
    # each differing part must be exactly the old one with those comparisons replaced
    saved = [(a, a.left, a.ops, a.comparators) for a, _b in swaps]
    for a, b in swaps:
        a.left, a.ops, a.comparators = b.left, b.ops, b.comparators
    try:
        same = all(astcache.same(_header_copy(pa) if header else pa,
                                 _header_copy(pb) if header else pb)
                   for pa, pb, header in parts)
    finally:
        for node, left, ops, comparators in saved:
            node.left, node.ops, node.comparators = left, ops, comparators
    return edits if same else None


def _one_edit(a, b):
    if len(a.ops) != 1 or len(b.ops) != 1:
        return None  # chained comparisons are not profiled
    o, n = _op_index(a.ops[0]), _op_index(b.ops[0])
    if o is None or n is None:
        return None
    sides = [(a.left, b.left), (a.comparators[0], b.comparators[0])]
    changed = [i for i, (x, y) in enumerate(sides) if not astcache.same(x, y)]
    if not changed:
        if o >= _IN or n >= _IN:
            return None  # in <-> not in always disagree when evaluated
        return _Edit(a, o, n)
    if len(changed) != 1:
        return None
    side = changed[0]
    other = sides[1 - side]
    ok_old, old_lit = _literal(sides[side][0])
    ok_new, new_lit = _literal(sides[side][1])
    if not (ok_old and ok_new) or _literal(other[0])[0]:
        return None  # only a literal facing a non-literal is value-profiled
    if (o >= _IN) != (n >= _IN) or (o >= _IN and side != 1):
        return None
    return _Edit(a, o, n, side, old_lit, new_lit)


_SINGLETONS = (None, True, False, Ellipsis)
_APPLY = (lambda x, y: x is y, lambda x, y: x is not y, lambda x, y: x == y,
          lambda x, y: x != y, lambda x, y: x < y, lambda x, y: x <= y,
          lambda x, y: x > y, lambda x, y: x >= y, lambda x, y: x in y,
          lambda x, y: x not in y)


def _decode(tagged):
    tag, value = tagged
    if tag == "str" and type(value) is str:
        return value
    if tag == "int" and type(value) is int:
        return value
    if tag == "float" and type(value) in (int, float):
        return float(value)
    if tag == "bool" and type(value) is bool:
        return value
    if tag == "none" and value is None:
        return None
    if tag == "tuple" and type(value) is list:
        return tuple(_decode(v) for v in value)
    raise Unsafe(f"bad value record {tagged!r}")


def _is_safe_literal(value) -> bool:
    return any(value is s for s in _SINGLETONS)


def _values_agree(edit: _Edit, values) -> bool:
    """Every recorded value of the non-literal side gives the same boolean result
    under the old comparison and the new one."""
    if values is None:
        return False
    if edit.old_op in (0, 1) or edit.new_op in (0, 1):
        # identity against a literal is only decided by value for singletons
        if not (_is_safe_literal(edit.old_lit) and _is_safe_literal(edit.new_lit)):
            return False
    old_f, new_f = _APPLY[edit.old_op], _APPLY[edit.new_op]
    for tagged in values:
        v = _decode(tagged)
        try:
            if edit.side == 1:
                old, new = old_f(v, edit.old_lit), new_f(v, edit.new_lit)
            else:
                old, new = old_f(edit.old_lit, v), new_f(edit.new_lit, v)
        except Exception:  # noqa: BLE001
            return False
        if type(old) is not bool or old is not new:
            return False
    return True


def _parents(tree):
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


# (parent type, field) through which a child expression is always evaluated when
# the parent is; anything else (BoolOp tails, IfExp branches, lambdas,
# comprehensions, ...) may skip it
_ALWAYS = {(ast.If, "test"), (ast.While, "test"), (ast.Return, "value"),
           (ast.Assign, "value"), (ast.AnnAssign, "value"), (ast.AugAssign, "value"),
           (ast.Expr, "value"), (ast.Assert, "test"), (ast.Call, "args"),
           (ast.Call, "func"), (ast.keyword, "value"), (ast.UnaryOp, "operand"),
           (ast.IfExp, "test"), (ast.Attribute, "value"), (ast.Subscript, "value"),
           (ast.Subscript, "slice"), (ast.Tuple, "elts"), (ast.List, "elts"),
           (ast.BinOp, "left"), (ast.BinOp, "right"), (ast.Starred, "value")}


def _unconditional(node, parents):
    """Statement containing `node`, and whether executing that statement always
    evaluates `node` (unless an earlier operand raises)."""
    always = True
    while True:
        parent = parents.get(node)
        if parent is None:
            return None, False
        field = None
        for name, value in ast.iter_fields(parent):
            if value is node or (isinstance(value, list) and any(v is node for v in value)):
                field = name
                break
        if isinstance(parent, ast.stmt):
            if (type(parent), field) not in _ALWAYS:
                always = False
            return parent, always
        if isinstance(parent, ast.BoolOp):
            if parent.values[0] is not node:
                always = False
        elif (type(parent), field) not in _ALWAYS:
            always = False
        node = parent


def _relprof_refine(info: FileInfo, old_tree, new_tree, bits: int, old_lines=None,
                    new_lines=None) -> int | None:
    """Bits to keep when the profile proves the edit inert, else None."""
    edits = _compare_edits(old_tree, new_tree, old_lines, new_lines)
    if not edits:
        return None
    rp = info.maps.relprof()
    if rp is None:
        return None
    sites = rp["sites"]
    for edit in edits:
        node, old_op, new_op = edit.node, edit.old_op, edit.new_op
        part, header = edit.part
        parents = {}
        if part is not old_tree:
            parents[part] = old_tree  # a statement's own parent only matters as non-None
        for n in _walk_part(part, header):
            for child in ast.iter_child_nodes(n):
                parents[child] = n
        # `x is <literal>` only adds a SyntaxWarning when the module is compiled:
        # printed to stderr, never an error (the pinned suite has no warning
        # filters, no -W error and no pytest config turning warnings into errors).
        key = f"{info.rel}:{node.lineno}:{node.col_offset}"
        entry = sites.get(key)
        if entry is None:
            _log(f"relprof: {key} not instrumented")
            return None
        sigs, eval_index = entry
        if edit.side is not None:
            values = rp.get("values", {})
            if key not in values or not _values_agree(edit, values[key]):
                _log(f"relprof: {key} literal change not proven by recorded values")
                return None
        else:
            for sig in sigs:
                a = (sig >> (2 * old_op)) & 3
                b = (sig >> (2 * new_op)) & 3
                if a != b or a not in (_T, _F):
                    _log(f"relprof: {key} diverges ({sig:#06x})")
                    return None
        # consistency with the coverage pass: a comparison every execution of its
        # statement evaluates must have been evaluated by every test covering it
        stmt, always = _unconditional(node, parents)
        if stmt is None:
            return None
        if always:
            lo = stmt.lineno
            first = _first_body_line(stmt)
            hi = first - 1 if first is not None and first > lo else stmt.end_lineno
            if isinstance(stmt, (ast.If, ast.While)):
                hi = stmt.test.end_lineno
            covered = info.cov(lo, hi)
            evaluated = int(rp["sets"][eval_index], 16)
            if covered & ~evaluated & ~rp["unsure_bits"]:
                _log(f"relprof: {key} covered by tests that never evaluated it")
                return None
        _log(f"relprof: {key} proven inert (op {old_op}->{new_op}, literal "
             f"{edit.old_lit!r}->{edit.new_lit!r})")
    return bits & rp["unsure_bits"]
