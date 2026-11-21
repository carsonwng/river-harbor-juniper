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
        code = compile(source, rel, "exec", dont_inherit=True)
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
        compile(new_text, path, "exec", dont_inherit=True)
    except SyntaxError as exc:
        raise Unsafe(f"does not compile: {exc.msg}")
    if ast.dump(old_tree) == ast.dump(new_tree):
        _log(f"{path}: AST unchanged")
        return 0
    info = FileInfo(maps, path, old_text)
    old_to_new = {o: n for n, o in new_to_old.items()}
    bits = 0
    for hunk in change.hunks:
        for number, text in hunk.removed:
            bits |= _old_line_bits(info, old_tree, number, text, hunk, new_lines, old_to_new)
        for number, _text in hunk.added:
            bits |= _new_line_bits(info, new_tree, number, new_to_old)
        if bits == maps.ALL:
            break
    if bits:
        try:
            refined = _relprof_refine(info, old_tree, new_tree, bits)
        except Exception as exc:  # noqa: BLE001
            _log(f"{path}: relprof error {exc!r}")
            refined = None
        if refined is not None:
            maps.pruned = True
            return refined
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


def _compare_edits(old_tree, new_tree):
    """Edits when the new tree is the old one with only the operator and/or a
    literal operand of single-operator comparisons changed; None otherwise."""
    old_cmp = [n for n in ast.walk(old_tree) if isinstance(n, ast.Compare)]
    new_cmp = [n for n in ast.walk(new_tree) if isinstance(n, ast.Compare)]
    if len(old_cmp) != len(new_cmp):
        return None
    edits = []
    for a, b in zip(old_cmp, new_cmp):
        if ast.dump(a) == ast.dump(b):
            continue
        if len(a.ops) != 1 or len(b.ops) != 1:
            return None  # chained comparisons are not profiled
        o, n = _op_index(a.ops[0]), _op_index(b.ops[0])
        if o is None or n is None:
            return None
        sides = [(a.left, b.left), (a.comparators[0], b.comparators[0])]
        changed = [i for i, (x, y) in enumerate(sides) if ast.dump(x) != ast.dump(y)]
        if not changed:
            if o >= _IN or n >= _IN:
                return None  # in <-> not in always disagree when evaluated
            edits.append(_Edit(a, o, n))
            continue
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
        edits.append(_Edit(a, o, n, side, old_lit, new_lit))
    if not edits:
        return None
    # the new tree must be exactly the old one with those comparisons replaced
    pairs = {id(a): b for a, b in zip(old_cmp, new_cmp)}
    saved = [(e.node, e.node.left, e.node.ops, e.node.comparators) for e in edits]
    for e in edits:
        b = pairs[id(e.node)]
        e.node.left, e.node.ops, e.node.comparators = b.left, b.ops, b.comparators
    try:
        same = ast.dump(old_tree) == ast.dump(new_tree)
    finally:
        for node, left, ops, comparators in saved:
            node.left, node.ops, node.comparators = left, ops, comparators
    return edits if same else None


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


def _relprof_refine(info: FileInfo, old_tree, new_tree, bits: int) -> int | None:
    """Bits to keep when the profile proves the edit inert, else None."""
    edits = _compare_edits(old_tree, new_tree)
    if not edits:
        return None
    rp = info.maps.relprof()
    if rp is None:
        return None
    sites = rp["sites"]
    parents = _parents(old_tree)
    for edit in edits:
        node, old_op, new_op = edit.node, edit.old_op, edit.new_op
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
