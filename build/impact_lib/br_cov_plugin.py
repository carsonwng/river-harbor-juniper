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


# ---------------------------------------------------------------- bookkeeping

_tests: list[str] = []          # index -> nodeid (or "collect::<module nodeid>")
_index: dict[tuple, list] = {}  # (filename, line) -> [(test index, phase)]
_typemap: dict[int, list] = {}
_outcomes: dict[str, str] = {}
PHASES = {"setup": 0, "call": 1, "teardown": 2, "collect": 3}


def _begin():
    global _cur, _types, _rcur
    _cur = set()
    _types = set()
    _rcur = set()
    if TRACK_TABLES:
        br_tables.begin()
    M.restart_events()


def _end(key: str, phase: str):
    global _cur, _types, _rcur
    lines, types, reads = _cur, _types, _rcur
    _cur, _types, _rcur = _sink, set(), _rsink
    trecs = br_tables.end() if TRACK_TABLES else None
    _sink.clear()
    _rsink.clear()
    if key not in _tests_pos:
        _tests_pos[key] = len(_tests)
        _tests.append(key)
    idx = _tests_pos[key]
    code = idx * 4 + PHASES[phase]
    if trecs:
        br_tables.store(trecs, code)
    for item in lines:
        _index.setdefault(item, []).append(code)
    if reads:
        done = set()
        for owner, home, attr in reads:
            for klass in (owner, home):
                if klass is None:
                    continue
                key = _class_key(klass)
                entry = (key or "*", attr)
                if entry not in done:
                    done.add(entry)
                    _rindex.setdefault(entry, []).append(code)
    if types:
        bucket = _typemap.setdefault(idx, [])
        for tp in types:
            for klass in getattr(tp, "__mro__", ()):
                module = getattr(klass, "__module__", "") or ""
                if module.startswith("sqlglot"):
                    bucket.append(f"{module}:{getattr(klass, '__qualname__', '?')}")


_tests_pos: dict[str, int] = {}


# ---------------------------------------------------------------- expression class attributes
#
# Every plain data attribute (dict, set, bool, ...) in the own __dict__ of a sqlglot
# Expr class (arg_types, required_args, is_var_len_args, _sql_names, ...) is
# replaced by a non-data descriptor returning the same object, which records
# (runtime class, attribute) and (defining class, attribute) for the current
# phase. The selector pairs these reads with the import-time state diff of an
# edited class body. Classes created later (lazy imports) are wrapped through
# Expr.__init_subclass__. At the end, a descriptor that is no longer in place,
# or a tracked-type attribute added at run time, marks that entry unsafe.

TRACK_EXPR = os.environ.get("BR_TRACK_EXPR", "1") == "1"
# class-level tables of the Dialect/Generator/Parser/Tokenizer families (br_tables.py)
TRACK_TABLES = os.environ.get("BR_TRACK_TABLES", "1") == "1"
import br_tables  # noqa: E402
_PLAIN = (dict, set, frozenset, list, tuple, bool, int, float, str, type(None))
_SKIP_ATTRS = {"key"}  # hot, and changes only with a renamed class (a class-set change)
_rsink: set = set()
_rcur: set = _rsink
_rindex: dict[tuple, list] = {}
_installed: list = []  # (class, attr, descriptor)
_ckey: dict = {}       # class -> "module:qualname" (None: not unique, never trusted)
_seen_names: dict[str, int] = {}
_unsafe: set = set()


class _Tracked:
    __slots__ = ("value", "home", "name")

    def __init__(self, value, home, name):
        self.value = value
        self.home = home
        self.name = name

    def __get__(self, inst, owner=None):
        _rcur.add((owner, self.home, self.name))
        return self.value


def _class_key(klass):
    got = _ckey.get(klass, 0)
    if got == 0:
        key = f"{getattr(klass, '__module__', '?')}:{getattr(klass, '__qualname__', '?')}"
        got = key if _seen_names.get(key, 0) <= 1 else None
        _ckey[klass] = got
    return got


def _install(klass) -> None:
    mod = getattr(klass, "__module__", "") or ""
    if not (mod == "sqlglot" or mod.startswith("sqlglot.")):
        return
    key = f"{mod}:{klass.__qualname__}"
    _seen_names[key] = _seen_names.get(key, 0) + 1
    if _seen_names[key] > 1:
        _unsafe.add(key + "|*")  # duplicate name: nothing about it is trusted
    for attr, value in list(klass.__dict__.items()):
        if attr.startswith("__") or attr in _SKIP_ATTRS or not isinstance(value, _PLAIN):
            continue
        desc = _Tracked(value, klass, attr)
        type.__setattr__(klass, attr, desc)
        _installed.append((klass, attr, desc))


def _track_setup() -> None:
    try:
        from sqlglot.expressions.core import Expr
    except Exception:  # noqa: BLE001
        return
    found, stack = [Expr], [Expr]
    seen = {Expr}
    while stack:
        k = stack.pop()
        for s in type.__subclasses__(k):
            if s not in seen:
                seen.add(s)
                found.append(s)
                stack.append(s)
    for k in found:
        _install(k)
    orig = Expr.__dict__["__init_subclass__"].__func__

    def __init_subclass__(cls, **kwargs):
        orig(cls, **kwargs)
        _install(cls)

    Expr.__init_subclass__ = classmethod(__init_subclass__)


def _track_check() -> list[str]:
    for klass, attr, desc in _installed:
        if klass.__dict__.get(attr) is not desc:
            key = _class_key(klass)
            _unsafe.add(f"{key}|{attr}" if key else "*")
    wrapped = {(k, a) for k, a, _ in _installed}
    for klass in {k for k, _, _ in _installed}:
        for attr, value in list(klass.__dict__.items()):
            if (klass, attr) not in wrapped and not attr.startswith("__")                     and attr not in _SKIP_ATTRS and isinstance(value, _PLAIN):
                key = _class_key(klass)
                _unsafe.add(f"{key}|{attr}" if key else "*")
    return sorted(x for x in _unsafe if x)


def pytest_configure(config):
    global _tool, TRACK_TABLES
    import sqlglot  # noqa: F401  core import-time lines are mapped by the import probe

    if TRACK_EXPR:
        _track_setup()
    if TRACK_TABLES and not br_tables.setup():
        TRACK_TABLES = False

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
    reads = {}
    if TRACK_EXPR:
        for (key, attr), codes in _rindex.items():
            reads[f"{key}|{attr}"] = codes
    tables = {"ok": False}
    if TRACK_TABLES:
        try:
            import br_state

            canon = br_state.canonical_classes()

            def ckey(klass):
                got = canon.get(klass)
                return None if got is None or got.endswith("@?") else got

            tables = br_tables.results(ckey)
        except Exception as exc:  # noqa: BLE001
            tables = {"ok": False, "error": repr(exc)}
    with open(os.path.join(OUT, f"tables-{worker}.json"), "w", encoding="utf-8") as handle:
        json.dump(tables, handle)
    with open(os.path.join(OUT, f"cov-{worker}.json"), "w", encoding="utf-8") as handle:
        json.dump({"tests": _tests, "files": files,
                   "types": {str(k): sorted(set(v)) for k, v in _typemap.items()},
                   "outcomes": _outcomes,
                   "exprattr": {"reads": reads,
                                "tracked": sorted({f"{_class_key(k)}|{a}" for k, a, _ in _installed
                                                   if _class_key(k)}) if TRACK_EXPR else [],
                                "unsafe": _track_check() if TRACK_EXPR else ["*"]}}, handle)
