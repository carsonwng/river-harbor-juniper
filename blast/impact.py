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
import sys
from pathlib import Path

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
    if bits:
        return maps.to_tests(bits)
    # nothing covered: still run the tests the map cannot vouch for (unknown to the
    # build, or not passing under coverage, so their coverage may be cut short)
    # when some change is real (its AST differs)
    return set(maps.always) if any(getattr(c, "_br_ast_changed", False) for c in changes) else set()


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
        code = compile(source, rel, "exec", dont_inherit=True)
        self.code = code
        self.code_lines: set[int] = set()
        stack = [code]
        while stack:
            co = stack.pop()
            self.code_lines.update(l for _, _, l in co.co_lines() if l)
            stack.extend(c for c in co.co_consts if hasattr(c, "co_code"))

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
        old_tree = ast.parse(old_text)
        new_tree = ast.parse(new_text)
        new_code = compile(new_text, path, "exec", dont_inherit=True)
    except SyntaxError as exc:
        raise Unsafe(f"does not compile: {exc.msg}")
    if ast.dump(old_tree) == ast.dump(new_tree):
        _log(f"{path}: AST unchanged")
        return 0
    try:
        change._br_ast_changed = True
    except Exception:  # noqa: BLE001  (frozen or slotted FileChange: be safe)
        raise Unsafe("cannot mark change")
    info = FileInfo(maps, path, old_text)
    info.new_tree = new_tree
    old_to_new = {o: n for n, o in new_to_old.items()}
    # tests that read this file's source text (ast.parse, inspect, linecache):
    # recorded as line 0 by the build; any AST change can change what they see
    bits = info.cov(0, 0)
    bits |= _semantic_bits(info, new_code)
    if _touches_import_time(info, old_tree, new_tree, change):
        bits |= _subprocess_bits(info)
    for hunk in change.hunks:
        for number, text in hunk.removed:
            bits |= _old_line_bits(info, old_tree, number, text, hunk, new_lines, old_to_new)
        for number, _text in hunk.added:
            bits |= _new_line_bits(info, new_tree, number, new_to_old)
        if bits == maps.ALL:
            break
    return bits


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
    # a lambda some class body or metaclass calls while the module is imported
    # shows up as import-time lines of its body; the lambda's own first line also
    # holds the table's import-time code, so it cannot tell and is left out
    return info.cov(lo, hi) | info.import_bits(best.lineno + 1, hi)


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
    names = _bound(stmt)
    read_later = names & _later_import_loads(parent, stmt) if names and _scope(chain, index) else set()
    if read_later and not read_later <= _new_class_bindings(info, _scope(chain, index)):
        # a later statement of the same class body reads the name while the class
        # is created: removing or breaking this binding can make the module's
        # import raise, which fails every user of the module, not just the class's
        return info.maps.module_users(info.module)
    return _name_bits(info, names, base)


def _new_class_bindings(info: "FileInfo", parts: list[str]) -> set[str]:
    """Names the class at `parts` binds in its body in the new file (empty if gone)."""
    nodes = getattr(info, "new_tree", ast.Module(body=[])).body
    klass = None
    for part in parts:
        klass = next((n for n in nodes if isinstance(n, ast.ClassDef) and n.name == part), None)
        if klass is None:
            return set()
        nodes = klass.body
    out: set[str] = set()
    for node in nodes:  # plain bindings only: X.pop(...) mutates but does not bind
        if isinstance(node, FUNC + (ast.ClassDef,)):
            out.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.__dict__.get("value", 1) is not None:
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        out.add(sub.id)
    return out


def _later_import_loads(parent, stmt) -> set[str]:
    """Names loaded by the statements after `stmt` in `parent`'s body that run while
    the scope is created: everything but function and nested class bodies (which
    cannot see a class scope), keeping decorators, defaults and bases."""
    body = getattr(parent, "body", [])
    out: set[str] = set()
    try:
        later = body[body.index(stmt) + 1:]
    except ValueError:
        return out
    stack = list(later)
    while stack:
        node = stack.pop()
        if isinstance(node, FUNC + (ast.Lambda,)):
            stack.extend(getattr(node, "decorator_list", []))
            args = node.args
            stack.extend(args.defaults)
            stack.extend(d for d in args.kw_defaults if d is not None)
            continue
        if isinstance(node, ast.ClassDef):
            stack.extend(node.bases)
            stack.extend(node.decorator_list)
            stack.extend(k.value for k in node.keywords)
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            out.add(node.id)
        stack.extend(ast.iter_child_nodes(node))
    return out


# ---------------------------------------------------------------- whole-function semantics

CO_GENERATOR_LIKE = 0x20 | 0x80 | 0x100 | 0x200  # generator, coroutine, async generator


def _functions(code) -> dict[str, list]:
    out: dict[str, list] = {}
    stack = [code]
    while stack:
        co = stack.pop()
        for const in co.co_consts:
            if hasattr(const, "co_code"):
                out.setdefault(const.co_qualname, []).append(const)
                stack.append(const)
    return out


def _code_lines(co) -> set[int]:
    lines: set[int] = set()
    stack = [co]
    while stack:
        c = stack.pop()
        lines.update(l for _, _, l in c.co_lines() if l)
        stack.extend(k for k in c.co_consts if hasattr(k, "co_code"))
    return lines


def _semantic_bits(info: "FileInfo", new_code) -> int:
    """Edits that change a function as a whole, not just the statement they sit in:
    it becomes or stops being a generator/coroutine (every call changes, even calls
    that never run a body line), or a name that was global or free becomes local
    (a removed `global`, a new assignment: earlier reads now raise). Those select
    every test that ran the function, plus the readers of its name for the first."""
    old_f, new_f = _functions(info.code), _functions(new_code)
    bits = 0
    for qual in old_f.keys() & new_f.keys():
        olds, news = old_f[qual], new_f[qual]
        flags_changed = sorted(c.co_flags & CO_GENERATOR_LIKE for c in olds) != \
            sorted(c.co_flags & CO_GENERATOR_LIKE for c in news)
        old_local = set().union(*(set(c.co_varnames) | set(c.co_cellvars) | set(c.co_freevars)
                                  for c in olds))
        old_global = set().union(*(set(c.co_names) for c in olds))
        new_local = set().union(*(set(c.co_varnames) | set(c.co_cellvars) | set(c.co_freevars)
                                  for c in news))
        shadowed = (new_local - old_local) & old_global
        if not flags_changed and not shadowed:
            continue
        lines: set[int] = set()
        for co in olds:
            lines |= _code_lines(co)
        for line in lines:
            bits |= info.cov(line, line) | info.import_bits(line, line)
        if flags_changed:
            name = qual.rsplit(".", 1)[-1]
            entry = info.maps.names().get(name)
            base = info.maps.module_users(info.module)
            bits |= base if entry is None or entry[1] else info.maps.bits(entry[0]) & base
        _log(f"{info.rel}: {qual} changes as a whole (generator={flags_changed}, "
             f"shadowed={sorted(shadowed)})")
    return bits


# ---------------------------------------------------------------- import time and processes


def _import_level(tree, line: int) -> bool:
    """Whether `line` runs while its module is imported (not in a function body)."""
    chain = _chain(tree, line)
    fi = _func_index(chain)
    if fi is None:
        return True
    first = _first_body_line(chain[fi])
    return first is None or line < first  # decorators, signature, defaults


def _touches_import_time(info: "FileInfo", old_tree, new_tree, change) -> bool:
    for hunk in change.hunks:
        for number, _ in hunk.removed:
            if _import_level(old_tree, number) or info.import_lines.get(str(number)):
                return True
        for number, _ in hunk.added:
            if _import_level(new_tree, number):
                return True
    return False


def _subprocess_bits(info: "FileInfo") -> int:
    """Tests that start processes (recorded as line -1 of sqlglot/__init__.py) may
    import sqlglot there (test_lazy_load): an import-time edit of a module that
    `import sqlglot` loads can change what they see."""
    core = info.maps.modules().get("core", [])
    if info.module not in core:
        return 0
    data = info.maps.file("sqlglot/__init__.py") or {}
    index = data.get("lines", {}).get("-1")
    return info.maps.bits(index) if index is not None else 0


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
