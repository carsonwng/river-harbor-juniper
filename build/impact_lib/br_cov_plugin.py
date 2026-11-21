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

import concurrent.futures
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


def _on_start(code, offset):
    if code.co_name == "__init__" and code.co_argcount and _relpath(code.co_filename) is not None \
            and code.co_filename.find(os.sep + "tests" + os.sep) < 0:
        try:
            frame = sys._getframe(1)
            obj = frame.f_locals.get(code.co_varnames[0])
            _types.add(type(obj))
        except Exception:
            pass
        return None
    return M.DISABLE


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
                   "outcomes": _outcomes}, handle)
