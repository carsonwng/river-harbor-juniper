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

Isolation (BR_IMPACT_ISOLATE=fork, see strategies/impact-isolate.md): every test
runs in its own os.fork() of the pristine post-collection process (setup, call
and a full teardown, so setUpClass and module fixtures run in every child). A
test then computes every lazily built cache, lru_cache and class-level table it
consumes itself, instead of consuming state an earlier test of the same worker
filled, so coverage names every consumer. The child ships its coverage records
and its reports to the parent through a pipe. BR_IMPACT_PREIMPORT=1 imports every
sqlglot module in the parent after collection (cheaper children), but then no
test runs lazy-loading code (Dialect._try_load, dialects.__getattr__), so it is
off by default. Any other value of BR_IMPACT_ISOLATE: in-process.

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


_importing = 0  # > 0 while a module body executes (see _skip_imports)


def _on_line(code, line):
    if _importing:
        return None  # import-time execution: the probe maps it; stay armed for the test
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
    if _importing:
        return None
    try:
        obj = sys._getframe(1).f_locals.get(code.co_varnames[0])
        _types.add(obj if mode == 2 and isinstance(obj, type) else type(obj))
    except Exception:
        pass
    return None


TEXT_READ_LINE = 0  # pseudo line: "this test read the file's source text"


SUBPROCESS_LINE = -1  # pseudo line of sqlglot/__init__.py: "this test started a process"
_INIT = os.path.join(ROOT, "sqlglot", "__init__.py")


def _on_audit(event, args):
    """A test that opens a sqlglot source file outside the import system (ast.parse
    of its text, inspect.getsource, linecache) depends on the text itself, so any
    edit of that file can change it. Recorded as line 0 of the file.
    A test that starts a process (subprocess, os.exec*, os.posix_spawn) may run
    sqlglot there unseen (test_lazy_load runs `import sqlglot`): recorded as line -1
    of sqlglot/__init__.py."""
    if _cur is _sink:
        return
    if event in ("subprocess.Popen", "os.posix_spawn", "os.exec", "os.spawn"):
        _cur.add((_INIT, SUBPROCESS_LINE))
        return
    if event != "open":
        return
    try:
        path = args[0]
        if not isinstance(path, str) or not path.endswith(".py"):
            return
        if sys._getframe(1).f_code.co_filename.startswith("<frozen importlib"):
            return
        if _relpath(path) is not None:
            _cur.add((path, TEXT_READ_LINE))
    except Exception:  # noqa: BLE001
        pass


sys.addaudithook(_on_audit)


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


_child_records: list | None = None  # set in a forked child: records go to the parent


def _end(key: str, phase: str):
    global _cur, _types
    lines, types = _cur, _types
    _cur, _types = _sink, set()
    _sink.clear()
    if _child_records is not None:
        _child_records.append((key, phase, list(lines), sorted(_type_keys(types))))
        return
    _record(key, phase, lines, _type_keys(types))


def _type_keys(types) -> set:
    keys = set()
    for tp in types:
        for klass in getattr(tp, "__mro__", ()):
            module = getattr(klass, "__module__", "") or ""
            if module.startswith("sqlglot"):
                keys.add(f"{module}:{getattr(klass, '__qualname__', '?')}")
    return keys


def _record(key: str, phase: str, lines, types) -> None:
    if key not in _tests_pos:
        _tests_pos[key] = len(_tests)
        _tests.append(key)
    idx = _tests_pos[key]
    code = idx * 4 + PHASES[phase]
    for item in lines:
        _index.setdefault(item, []).append(code)
    if types:
        _typemap.setdefault(idx, []).extend(types)


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


# ---------------------------------------------------------------- fork isolation

SKIP_IMPORTS = os.environ.get(
    "BR_IMPACT_SKIP_IMPORTS", "1" if os.environ.get("BR_IMPACT_ISOLATE") == "fork" else "0") == "1"


def _skip_imports() -> None:
    """Do not attribute the execution of module bodies (and whatever they call) to
    the test that triggered the import. In a forked child every test re-imports the
    lazily loaded dialect modules, and their import-time work (metaclasses,
    class-body calls, classmethods) would make each such test look like a user of
    every class touched at import. Import-time lines are mapped per module by the
    import probe instead, as the in-process mode effectively does for all but the
    first importer. Wraps importlib._bootstrap._find_and_load, which both the
    import statement and importlib.import_module go through for a module that is
    not loaded yet."""
    import importlib._bootstrap as bootstrap

    original = bootstrap._find_and_load

    def _find_and_load(name, import_):
        global _importing
        _importing += 1
        try:
            return original(name, import_)
        finally:
            _importing -= 1

    bootstrap._find_and_load = _find_and_load

ISOLATE = os.environ.get("BR_IMPACT_ISOLATE", "") == "fork" and hasattr(os, "fork")
PREIMPORT = os.environ.get("BR_IMPACT_PREIMPORT", "0") == "1"
CHILD_TIMEOUT = float(os.environ.get("BR_IMPACT_CHILD_TIMEOUT", "600"))


def pytest_collection_finish(session):
    if SKIP_IMPORTS:
        _skip_imports()
    if not PREIMPORT:
        return
    import importlib
    import pkgutil

    import sqlglot

    for info in pkgutil.walk_packages(sqlglot.__path__, prefix="sqlglot."):
        if info.name == "sqlglot.__main__":
            continue
        try:
            importlib.import_module(info.name)
        except Exception:  # noqa: BLE001
            pass


def _read_all(fd: int, deadline: float) -> bytes:
    import select
    import time

    chunks = []
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError
        ready, _, _ = select.select([fd], [], [], left)
        if not ready:
            continue
        chunk = os.read(fd, 1 << 20)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    if not ISOLATE:
        return None
    import time
    from _pytest.runner import runtestprotocol

    hook = item.ihook
    hook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    sys.stdout.flush()
    sys.stderr.flush()
    rfd, wfd = os.pipe()
    import warnings

    with warnings.catch_warnings():  # xdist's execnet thread; the child never uses it
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:  # child
        global _child_records
        status = 0
        try:
            os.close(rfd)
            _child_records = []
            _outcomes.clear()
            reports = runtestprotocol(item, log=False, nextitem=None)
            for rep in reports:
                pytest_runtest_logreport(rep)
            payload = {"records": _child_records, "outcomes": dict(_outcomes),
                       "reports": [item.config.hook.pytest_report_to_serializable(
                           config=item.config, report=r) for r in reports]}
            data = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
            view = memoryview(data)
            while view:
                view = view[os.write(wfd, view):]
        except BaseException:  # noqa: BLE001
            status = 1
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                os._exit(status)
    os.close(wfd)
    payload = None
    try:
        data = _read_all(rfd, time.monotonic() + CHILD_TIMEOUT)
        payload = pickle.loads(data) if data else None
    except TimeoutError:
        os.kill(pid, 9)
    except Exception:  # noqa: BLE001
        payload = None
    finally:
        os.close(rfd)
        os.waitpid(pid, 0)
    if payload is None:
        from _pytest.reports import TestReport

        reports = [TestReport(item.nodeid, item.location, {}, "failed",
                              "br-impact: forked child died", "call")]
    else:
        for key, phase, lines, types in payload["records"]:
            _record(key, phase, lines, types)
        for nodeid, outcome in payload.get("outcomes", {}).items():
            if outcome.startswith("failed"):  # failed subtests included
                _outcomes[nodeid] = outcome
        reports = [item.config.hook.pytest_report_from_serializable(config=item.config, data=d)
                   for d in payload["reports"]]
    for rep in reports:
        hook.pytest_runtest_logreport(report=rep)
    hook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True


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
