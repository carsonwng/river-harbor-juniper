"""Layer 2: triage each changed file before impact analysis.

Owner: strat/triage.

For every FileChange, return one of:
  Decision.skip()          the change provably cannot fail a test
  Decision.tests({...})    exactly these tests can fail (e.g. an edited test or fixture)
  Decision.defer()         a source change; let the impact layer decide

Rules (maps from build/triage.py; without them everything defers):

* Fixture files (any non-.py file under tests/): the tests whose code names the file,
  from a static string analysis of the pinned test suite. New fixtures: tests whose
  path templates or listed directories cover them, else every test module that reads
  fixtures. Renames count as delete + add.
* Test modules (tests/**/*.py): rebuild the post-patch file and diff ASTs per function.
  Only test methods changed -> those tests (and subclasses that inherit them). Any other
  change (imports, module code, helpers, setUp, class bodies) -> the whole file plus
  every test module that imports it. Unparseable -> the same. Deleted -> the same.
* Python source outside tests/: skipped only when the post-patch AST equals the pinned
  AST (docstrings included), i.e. the patch only touches comments, blank lines or
  layout. Anything else defers.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .context import Context
from .patch import FileChange


@dataclass
class Decision:
    kind: str  # "skip" | "tests" | "defer"
    tests: set[int] = field(default_factory=set)
    reason: str = ""

    @staticmethod
    def skip(reason: str = "") -> "Decision":
        return Decision("skip", reason=reason)

    @staticmethod
    def select(tests: set[int], reason: str = "") -> "Decision":
        return Decision("tests", set(tests), reason)

    @staticmethod
    def defer(reason: str = "") -> "Decision":
        return Decision("defer", reason=reason)


class PatchMismatch(Exception):
    pass


# ----------------------------------------------------------------- maps

_MAPS: dict[str, object] = {}


def _maps(ctx: Context):
    key = str(ctx.maps_dir)
    if key not in _MAPS:
        base = ctx.layer_maps("triage")
        try:
            data = json.loads((base / "triage.json").read_text("utf-8"))
            data["src"] = base / "src"
        except (OSError, ValueError):
            data = None
        _MAPS[key] = data
    return _MAPS[key]


# ----------------------------------------------------------------- patch application

def apply_hunks(old_text: str, change: FileChange, touched: set[int] | None = None) -> str:
    """Rebuild the post-patch file from the pinned text; raise PatchMismatch if the
    patch does not apply exactly. `touched` collects the old line numbers each edit
    sits on (removed lines, or both neighbours of an insertion)."""
    old = old_text.splitlines()
    out: list[str] = []
    pos = 0  # index into old (0-based) of the next unconsumed line
    for hunk in change.hunks:
        start = hunk.old_start - 1 if hunk.old_count else hunk.old_start
        if start < pos or start > len(old):
            raise PatchMismatch("hunk order")
        out.extend(old[pos:start])
        pos = start
        removed = dict(hunk.removed)
        added = dict(hunk.added)
        for number, text in hunk.removed:
            if touched is not None:
                touched.add(number)
            if number - 1 >= len(old) or old[number - 1].rstrip("\r") != text.rstrip("\r"):
                raise PatchMismatch(f"removed line {number}")
        end = start + hunk.old_count
        for new_no in range(hunk.new_start, hunk.new_start + hunk.new_count):
            if new_no in added:
                out.append(added[new_no])
                if touched is not None:
                    touched.update((pos, pos + 1))  # old lines around the insertion
                continue
            while pos < end and (pos + 1) in removed:
                pos += 1
            if pos >= end:
                raise PatchMismatch("context overrun")
            out.append(old[pos])
            pos += 1
        while pos < end and (pos + 1) in removed:
            pos += 1
        if pos != end:
            raise PatchMismatch("context underrun")
    out.extend(old[pos:])
    return "\n".join(out) + "\n"


def _read_src(maps, path: str) -> str | None:
    try:
        return (maps["src"] / path).read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _dump(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def _parse(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


# ----------------------------------------------------------------- selection helpers

def _ids_to_numbers(ids, ctx: Context) -> set[int]:
    return {ctx.by_id[i] for i in ids if i in ctx.by_id}


def _whole(maps, path: str, ctx: Context) -> set[int]:
    """Every test that imports or inherits from module `path`: its own tests, tests
    of every module importing it, and tests defined there but collected elsewhere."""
    files = {path, *maps["importers"].get(path, [])}
    if path.endswith("__init__.py"):
        prefix = path[: -len("__init__.py")]
        files |= {f for f in ctx.by_file if f.startswith(prefix)}
    out = {n for f in files for n in ctx.by_file.get(f, [])}
    out |= _ids_to_numbers((i for i, d in maps["tests"].items() if d[0] in files), ctx)
    return out


def _fixture_fallback(maps, ctx: Context) -> set[int]:
    return {n for f in maps["fallback"] for n in ctx.by_file.get(f, [])}


def _fixture_readers(maps, path: str, ctx: Context) -> set[int] | None:
    ids = maps["fixtures"].get(path)
    if ids is None:
        return None
    out = _ids_to_numbers(ids, ctx)
    # tests.json ids the static enumeration does not know, in modules naming the file
    known = maps["tests"]
    modules = set(maps["fixture_modules"].get(path, []))
    out |= {ctx.by_id[t["id"]] for t in ctx.tests
            if t["file"] in modules and t["id"] not in known}
    return out


def _pattern_readers(maps, path: str, ctx: Context) -> set[int]:
    rel = path[len("tests/"):] if path.startswith("tests/") else path
    out: set[str] = set()
    for pattern, ids in maps["patterns"]:
        pattern = pattern.strip("/")
        for candidate in (pattern, "*/" + pattern):
            if fnmatch.fnmatchcase(rel, candidate) or fnmatch.fnmatchcase(rel, candidate + "/*") \
                    or fnmatch.fnmatchcase(rel, candidate + ".*"):
                out.update(ids)
                break
    return _ids_to_numbers(out, ctx)


# ----------------------------------------------------------------- AST diff of a test module

def _units(tree: ast.Module):
    """Split a module into (skeleton dump, {qualname: function dump}). The skeleton is
    the module with every module-level function and class method taken out."""
    functions: dict[str, str] = {}
    skeleton = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = _dump(node)
            skeleton.append(("def", node.name))
        elif isinstance(node, ast.ClassDef):
            body = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions[f"{node.name}.{item.name}"] = _dump(item)
                else:
                    body.append(_dump(item))
            skeleton.append(("class", node.name, [_dump(b) for b in node.bases],
                             [_dump(k) for k in node.keywords],
                             [_dump(d) for d in node.decorator_list], body))
        else:
            skeleton.append(_dump(node))
    return repr(skeleton), functions


def _spans(tree: ast.Module) -> list[tuple[str, int, int]]:
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node.lineno, node.end_lineno))
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = min([item.lineno] + [d.lineno for d in item.decorator_list])
                    out.append((f"{node.name}.{item.name}", start, item.end_lineno))
    return out


def _is_test_name(qualname: str) -> bool:
    return qualname.rsplit(".", 1)[-1].startswith("test")


def _tests_defined_by(maps, path: str, qualname: str, ctx: Context) -> set[int]:
    ids = [i for i, d in maps["tests"].items() if d[0] == path and d[1] == qualname]
    out = _ids_to_numbers(ids, ctx)
    # tests.json ids in this file with the same Class::method, in case the static
    # enumeration missed one
    if "." in qualname:
        cls, method = qualname.split(".", 1)
        out |= {n for n in ctx.by_file.get(path, [])
                if ctx.tests[n]["id"].endswith(f"::{cls}::{method}")}
    else:
        out |= {n for n in ctx.by_file.get(path, []) if ctx.tests[n]["id"].endswith(f"::{qualname}")}
    return out


def _test_module_change(maps, change: FileChange, ctx: Context) -> Decision:
    path = change.old_path
    old_text = _read_src(maps, path)
    if old_text is None:
        return Decision.select(_whole(maps, path, ctx), "test module: no pinned copy")
    touched: set[int] = set()
    try:
        new_text = apply_hunks(old_text, change, touched)
    except PatchMismatch as error:
        return Decision.select(_whole(maps, path, ctx), f"test module: patch mismatch ({error})")
    old_tree, new_tree = _parse(old_text), _parse(new_text)
    if old_tree is None or new_tree is None:
        return Decision.select(_whole(maps, path, ctx), "test module: does not parse")
    if path.endswith(("__init__.py", "conftest.py")):
        if _dump(old_tree) == _dump(new_tree) and min(touched, default=99) > 2:
            return Decision.skip("package/conftest: same AST")
        return Decision.select(_whole(maps, path, ctx), "package/conftest changed")

    old_skeleton, old_functions = _units(old_tree)
    new_skeleton, new_functions = _units(new_tree)
    if old_skeleton != new_skeleton:
        return Decision.select(_whole(maps, path, ctx), "test module: module/class level changed")

    selected: set[int] = set()
    reasons = []
    for qualname in sorted(set(old_functions) | set(new_functions)):
        before, after = old_functions.get(qualname), new_functions.get(qualname)
        if before == after:
            continue
        if not _is_test_name(qualname):
            return Decision.select(_whole(maps, path, ctx), f"test module: helper {qualname} changed")
        if before is not None:
            selected |= _tests_defined_by(maps, path, qualname, ctx)
        if after is not None and before is None:
            # A new test method: any collected test of that name in this module or a
            # module that imports it may now resolve to it.
            method = qualname.rsplit(".", 1)[-1]
            files = {path, *maps["importers"].get(path, [])}
            selected |= {n for f in files for n in ctx.by_file.get(f, [])
                         if ctx.tests[n]["id"].endswith("::" + method)}
        reasons.append(qualname)
    if reasons:
        return Decision.select(selected, "test functions changed: " + ", ".join(reasons[:5]))

    # Same AST: comments or layout only. Line numbers of the touched functions moved
    # at most; run those tests anyway, since that is cheap.
    if min(touched, default=99) <= 2:
        return Decision.select(_whole(maps, path, ctx), "test module: encoding line touched")
    for qualname, start, end in _spans(old_tree):
        if any(start <= line <= end for line in touched) and _is_test_name(qualname):
            selected |= _tests_defined_by(maps, path, qualname, ctx)
    return Decision.select(selected, "test module: comments/layout only")


# ----------------------------------------------------------------- classify

def _is_fixture(path: str) -> bool:
    return path.startswith("tests/") and not path.endswith((".py", ".pyc")) \
        and "__pycache__" not in path


def _fixture_change(maps, change: FileChange, ctx: Context) -> Decision:
    selected: set[int] = set()
    reasons = []
    old, new = change.old_path, change.new_path
    if old is not None and _is_fixture(old):
        readers = _fixture_readers(maps, old, ctx)
        if readers is None:
            selected |= _fixture_fallback(maps, ctx) | _pattern_readers(maps, old, ctx)
            reasons.append("unknown fixture -> fallback")
        else:
            selected |= readers | _pattern_readers(maps, old, ctx)
            reasons.append(f"fixture readers of {old}")
    if new is not None and _is_fixture(new) and new != old:
        readers = _fixture_readers(maps, new, ctx)
        if readers is not None:
            selected |= readers | _pattern_readers(maps, new, ctx)
        else:
            by_pattern = _pattern_readers(maps, new, ctx)
            selected |= by_pattern if by_pattern else _fixture_fallback(maps, ctx)
        reasons.append(f"new fixture {new}")
    return Decision.select(selected, "; ".join(reasons))


def _source_noop(maps, change: FileChange) -> bool:
    if change.old_path != change.new_path or not change.old_path.endswith(".py") or change.binary:
        return False
    old_text = _read_src(maps, change.old_path)
    if old_text is None:
        return False
    touched: set[int] = set()
    try:
        new_text = apply_hunks(old_text, change, touched)
    except PatchMismatch:
        return False
    if min(touched, default=99) <= 2:
        return False  # could change the encoding declaration
    old_tree, new_tree = _parse(old_text), _parse(new_text)
    return old_tree is not None and new_tree is not None and _dump(old_tree) == _dump(new_tree)


def classify(change: FileChange, ctx: Context) -> Decision:
    maps = _maps(ctx)
    if not maps:
        return Decision.defer("no triage maps")
    old, new = change.old_path, change.new_path
    paths = [p for p in (old, new) if p]
    if not paths:
        return Decision.defer("no path")
    in_tests = [p.startswith("tests/") for p in paths]
    if all(in_tests):
        if any(_is_fixture(p) for p in paths) and not any(p.endswith(".py") for p in paths):
            return _fixture_change(maps, change, ctx)
        if all(p.endswith(".py") for p in paths):
            return _test_py_change(maps, change, ctx)
        return Decision.defer("mixed test path kinds")
    if any(in_tests):
        return Decision.defer("moved across tests/")
    if _source_noop(maps, change):
        return Decision.defer("probe: comment skip off")
    return Decision.defer("source change")


def _test_py_change(maps, change: FileChange, ctx: Context) -> Decision:
    old, new = change.old_path, change.new_path
    if old is not None and new is not None and old == new:
        if _read_src(maps, old) is None:
            return Decision.defer("unknown test module")
        return _test_module_change(maps, change, ctx)
    selected: set[int] = set()
    reasons = []
    if old is not None:  # deleted or renamed away: its tests vanish, importers break
        selected |= _whole(maps, old, ctx)
        reasons.append(f"removed {old}")
    if new is not None and new != old:
        base = os.path.basename(new)
        if base in ("conftest.py", "__init__.py"):
            prefix = new[: -len(base)]
            selected |= {n for f, ns in ctx.by_file.items() if f.startswith(prefix) for n in ns}
            reasons.append(f"new {base}")
        else:
            selected |= set(ctx.by_file.get(new, []))
            reasons.append(f"new module {new}")
    return Decision.select(selected, "; ".join(reasons))
