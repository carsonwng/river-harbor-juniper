"""pytest plugin: per-test line coverage of the sqlglot checkout via sys.monitoring.

Loaded with `-p br_cov_plugin` (this directory on PYTHONPATH). Every LINE event
returns DISABLE, so each (code, line) costs one callback per test; restart_events()
at every phase boundary re-arms them. Lines are keyed by path relative to the
sqlglot root and inverted on the fly (line -> test indices) to keep memory small.

Extra signal per test: the classes whose sqlglot-defined __init__ ran, with the
runtime type of `self` (so subclasses that inherit __init__ are seen).

Process pools are replaced by an in-process thread pool that round-trips every
task through pickle, so work the suite ships to spawned children (and the
pickling itself) is attributed to the test that submitted it.

Env: BR_IMPACT_ROOT (sqlglot checkout), BR_IMPACT_OUT (directory for results).
"""

from __future__ import annotations

import ast
import builtins
import concurrent.futures
import importlib.abc
import importlib.machinery
import json
import os
import pickle
import sys
import threading

import pytest

M = sys.monitoring
ROOT = os.path.normcase(os.path.realpath(os.environ["BR_IMPACT_ROOT"]))
OUT = os.environ["BR_IMPACT_OUT"]
PREFIXES = (os.path.join(ROOT, "sqlglot") + os.sep, os.path.join(ROOT, "tests") + os.sep)

_rel: dict[str, str | None] = {}
_sink: set = set()
_cur: set = _sink
_types: set = set()
_tool = None


def _relpath(filename: str) -> str | None:
    rel = _rel.get(filename, 0)
    if rel == 0:
        norm = os.path.normcase(os.path.realpath(filename)) if filename and not filename.startswith("<") else ""
        rel = None
        if norm.startswith(PREFIXES):
            rel = os.path.relpath(norm, ROOT).replace(os.sep, "/")
        _rel[filename] = rel
    return rel


def _on_line(code, line):
    if _relpath(code.co_filename) is not None:
        _cur.add((code.co_filename, line))
    return M.DISABLE


_track: dict = {}


def _on_start(code, offset):
    """Record the class behind `self` in __init__ and `cls` in classmethods."""
    mode = _track.get(code)
    if mode is None:
        mode = 0
        if code.co_argcount and _relpath(code.co_filename) is not None                 and (os.sep + "tests" + os.sep) not in code.co_filename:
            if code.co_name == "__init__":
                mode = 1
            elif code.co_varnames[0] == "cls":
                mode = 2
        _track[code] = mode
    if not mode:
        return M.DISABLE
    try:
        obj = sys._getframe(1).f_locals.get(code.co_varnames[0])
        _types.add(obj if mode == 2 and isinstance(obj, type) else type(obj))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- process pools


class _InlinePool(concurrent.futures.ThreadPoolExecutor):
    """Stand-in for ProcessPoolExecutor: one thread, pickle round trips."""

    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(),
                 **_ignored):
        if initializer is not None:
            init, args = pickle.loads(pickle.dumps((initializer, tuple(initargs))))
            initializer, initargs = init, args
        super().__init__(max_workers=1, initializer=initializer, initargs=initargs)

    def submit(self, fn, /, *args, **kwargs):
        fn, args, kwargs = pickle.loads(pickle.dumps((fn, args, kwargs)))

        def run():
            return pickle.loads(pickle.dumps(fn(*args, **kwargs)))

        return super().submit(run)

    def map(self, fn, *iterables, timeout=None, chunksize=1):
        return super().map(fn, *iterables, timeout=timeout)


concurrent.futures.ProcessPoolExecutor = _InlinePool
try:
    import concurrent.futures.process as _cfp
    _cfp.ProcessPoolExecutor = _InlinePool
except Exception:
    pass


# ---------------------------------------------------------------- arm probes
#
# Line coverage cannot tell which tests evaluated one operand of an expression that
# shares its line with the rest of the statement (`a and b`, `x if c else y`, a
# comprehension body or filter, a lambda body, an assert message). With BR_ARMS on,
# an import hook compiles every sqlglot module from its pinned source with each such
# conditionally evaluated operand ("arm") A rewritten to
#
#     (__br_arm__ or A)
#
# where `__br_arm__` is a builtin bound to False, so the value, the evaluation order
# and every exception are A's own. The probe name alone is placed on a fake line
# (ARM_LINE + the arm's index in its file), so the LINE event of that fake line says
# that the test started evaluating A. Every real instruction keeps its position, and
# the `or` keeps A's (so instructions placed at the operand's location, such as a
# lambda's return, stay where they were): the real line coverage can only gain the
# first line of an arm whenever the arm is entered, never lose a line. Annotations are left alone (with postponed
# evaluation their source text is the value). The arm table per file (index ->
# "line:col:end_line:end_col" of A in the pinned source) is written with the results.

ARM_LINE = 10_000_000
ARMS = os.environ.get("BR_ARMS", "1") != "0"
_arm_tables: dict[str, list[str]] = {}


class _ArmRewrite(ast.NodeTransformer):
    def __init__(self) -> None:
        self.keys: list[str] = []

    def arm(self, node):
        if node is None or not hasattr(node, "end_col_offset"):
            return node
        line = ARM_LINE + len(self.keys)
        self.keys.append(f"{node.lineno}:{node.col_offset}:{node.end_lineno}:{node.end_col_offset}")
        probe = ast.Name(id="__br_arm__", ctx=ast.Load(), lineno=line, col_offset=0,
                         end_lineno=line, end_col_offset=1)
        wrapped = ast.BoolOp(op=ast.Or(), values=[probe, node])
        return ast.copy_location(wrapped, node)

    def _no_annotations(self, node, fields):
        saved = [(f, getattr(node, f, None)) for f in fields]
        for f, _ in saved:
            setattr(node, f, None)
        self.generic_visit(node)
        for f, v in saved:
            setattr(node, f, v)
        return node

    def visit_arg(self, node):
        return node  # only an annotation below

    def visit_FunctionDef(self, node):
        return self._no_annotations(node, ("returns",))

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_AnnAssign(self, node):
        return self._no_annotations(node, ("annotation",))

    def visit_TypeAlias(self, node):
        return node

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        node.values = node.values[:1] + [self.arm(v) for v in node.values[1:]]
        return node

    def visit_IfExp(self, node):
        self.generic_visit(node)
        node.body = self.arm(node.body)
        node.orelse = self.arm(node.orelse)
        return node

    def _comp(self, node, fields):
        self.generic_visit(node)
        for f in fields:
            setattr(node, f, self.arm(getattr(node, f)))
        for i, gen in enumerate(node.generators):
            if i:
                gen.iter = self.arm(gen.iter)
            gen.ifs = [self.arm(c) for c in gen.ifs]
        return node

    def visit_ListComp(self, node):
        return self._comp(node, ("elt",))

    visit_SetComp = visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node):
        return self._comp(node, ("key", "value"))

    def visit_Lambda(self, node):
        self.generic_visit(node)
        node.body = self.arm(node.body)
        return node

    def visit_Assert(self, node):
        self.generic_visit(node)
        node.msg = self.arm(node.msg)
        return node


class _ArmLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        path = self.get_filename(fullname)
        source = self.get_data(path)
        rel = _relpath(path)
        try:
            rewrite = _ArmRewrite()
            tree = rewrite.visit(ast.parse(source, path))
            code = compile(tree, path, "exec", dont_inherit=True)
        except Exception:  # noqa: BLE001  plain compile: no arms for this file
            return compile(source, path, "exec", dont_inherit=True)
        if rel is not None:
            _arm_tables[rel] = rewrite.keys
        return code


class _ArmFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname != "sqlglot" and not fullname.startswith("sqlglot."):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or not spec.origin or not spec.origin.endswith(".py"):
            return spec
        if not os.path.normcase(os.path.realpath(spec.origin)).startswith(PREFIXES[0]):
            return spec
        spec.loader = _ArmLoader(fullname, spec.origin)
        return spec


if ARMS:
    builtins.__br_arm__ = False
    sys.meta_path.insert(0, _ArmFinder())


# ---------------------------------------------------------------- bookkeeping

_tests: list[str] = []          # index -> nodeid (or "collect::<module nodeid>")
_index: dict[tuple, list] = {}  # (filename, line) -> [(test index, phase)]
_typemap: dict[int, list] = {}
_outcomes: dict[str, str] = {}
PHASES = {"setup": 0, "call": 1, "teardown": 2, "collect": 3}


def _begin():
    global _cur, _types
    _cur = set()
    _types = set()
    M.restart_events()


def _end(key: str, phase: str):
    global _cur, _types
    lines, types = _cur, _types
    _cur, _types = _sink, set()
    _sink.clear()
    if key not in _tests_pos:
        _tests_pos[key] = len(_tests)
        _tests.append(key)
    idx = _tests_pos[key]
    code = idx * 4 + PHASES[phase]
    for item in lines:
        _index.setdefault(item, []).append(code)
    if types:
        bucket = _typemap.setdefault(idx, [])
        for tp in types:
            for klass in getattr(tp, "__mro__", ()):
                module = getattr(klass, "__module__", "") or ""
                if module.startswith("sqlglot"):
                    bucket.append(f"{module}:{getattr(klass, '__qualname__', '?')}")


_tests_pos: dict[str, int] = {}


def pytest_configure(config):
    global _tool
    import sqlglot  # noqa: F401  core import-time lines are mapped by the import probe

    for tool in (M.COVERAGE_ID, 3, 4, 5):
        try:
            M.use_tool_id(tool, "br-impact")
            _tool = tool
            break
        except ValueError:
            continue
    M.register_callback(_tool, M.events.LINE, _on_line)
    M.register_callback(_tool, M.events.PY_START, _on_start)
    M.set_events(_tool, M.events.LINE | M.events.PY_START)


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    if isinstance(collector, pytest.Module):
        _begin()
        yield
        _end("collect::" + collector.nodeid, "collect")
    else:
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    _begin()
    yield
    _end(item.nodeid, "setup")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    _begin()
    yield
    _end(item.nodeid, "call")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    _begin()
    yield
    _end(item.nodeid, "teardown")


def pytest_runtest_logreport(report):
    if report.failed:
        _outcomes[report.nodeid] = f"failed:{report.when}"
    elif report.when == "call" and report.nodeid not in _outcomes:
        _outcomes[report.nodeid] = report.outcome


def pytest_unconfigure(config):
    if _tool is None:
        return
    M.set_events(_tool, 0)
    M.free_tool_id(_tool)
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    files: dict[str, dict] = {}
    for (filename, line), codes in _index.items():
        rel = _relpath(filename)
        files.setdefault(rel, {}).setdefault(str(line), []).extend(codes)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"cov-{worker}.json"), "w", encoding="utf-8") as handle:
        json.dump({"tests": _tests, "files": files,
                   "types": {str(k): sorted(set(v)) for k, v in _typemap.items()},
                   "outcomes": _outcomes, "arms": _arm_tables}, handle)
