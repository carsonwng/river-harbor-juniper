"""pytest plugin: *instruction* coverage of sqlglot for one chosen test per process.

Loaded with `-p br_instr_plugin` in a pytest process that collects the whole suite
(so every module the labelling run imports at collection is imported here too) and
runs exactly one test, BR_INSTR_ONLY (a fresh process, so no other test warms
caches or changes global state for it).
From the moment this plugin is imported (before sqlglot or any test module is
imported) until the process ends, every bytecode instruction of a sqlglot/ code
object that executes is recorded once, with its source position (line, col,
end_line, end_col). Everything the process ran is attributed to that one test:
imports, collection, setUpClass, the call, teardown.

Line coverage says a test ran a *statement*; this says which *expressions* of it
were evaluated. The selector drops a profiled test when an edit only replaces
expressions none of whose instructions that test ever executed.

Process pools are replaced in-process exactly like br_cov_plugin (the executor
tests ship their work to spawned children), so child work is recorded too.

Env: BR_IMPACT_ROOT (sqlglot checkout), BR_IMPACT_OUT (directory for results),
BR_INSTR_TAG (file name tag).
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import pickle
import sys

M = sys.monitoring
ROOT = os.path.normcase(os.path.realpath(os.environ["BR_IMPACT_ROOT"]))
OUT = os.environ["BR_IMPACT_OUT"]
PREFIX = os.path.join(ROOT, "sqlglot") + os.sep

_rel: dict[str, str | None] = {}
_seen: set = set()


def _relpath(filename: str) -> str | None:
    rel = _rel.get(filename, 0)
    if rel == 0:
        norm = os.path.normcase(os.path.realpath(filename)) if filename and not filename.startswith("<") else ""
        rel = None
        if norm.startswith(PREFIX):
            rel = os.path.relpath(norm, ROOT).replace(os.sep, "/")
        _rel[filename] = rel
    return rel


def _on_instr(code, offset):
    _seen.add((code, offset))
    return M.DISABLE


_tool = None
for _t in (3, 4, 5, M.COVERAGE_ID):
    try:
        M.use_tool_id(_t, "br-instr")
        _tool = _t
        break
    except ValueError:
        continue


def _on_start(code, offset):
    # INSTRUCTION events only for sqlglot *function* code objects (local events
    # persist on the code object; PY_START is then disabled for it). Module and
    # class bodies are import-time code, which the selector never prunes by this map.
    if code.co_flags & 1 and _relpath(code.co_filename) is not None:  # CO_OPTIMIZED: functions
        M.set_local_events(_tool, code, M.events.INSTRUCTION)
    return M.DISABLE


M.register_callback(_tool, M.events.INSTRUCTION, _on_instr)
M.register_callback(_tool, M.events.PY_START, _on_start)
M.set_events(_tool, M.events.PY_START)


# A test that reads sqlglot source text (ast.parse of a module, inspect.getsource,
# formatted tracebacks) can see edits it never executes: such a profile is unusable.
_source_reads: list[str] = []


def _audit(event, args):
    if event == "open" and args and isinstance(args[0], str) and args[0].endswith(".py"):
        norm = os.path.normcase(os.path.realpath(args[0]))
        if norm.startswith(PREFIX):
            frame = sys._getframe(1)
            while frame is not None:
                if frame.f_code.co_filename.startswith("<frozen importlib"):
                    return  # the import system loading the module
                frame = frame.f_back
            _source_reads.append(norm)


sys.addaudithook(_audit)


class _InlinePool(concurrent.futures.ThreadPoolExecutor):
    """Stand-in for ProcessPoolExecutor: one thread, pickle round trips."""

    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(),
                 **_ignored):
        if initializer is not None:
            initializer, initargs = pickle.loads(pickle.dumps((initializer, tuple(initargs))))
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
except Exception:  # noqa: BLE001
    pass

_outcomes: dict[str, str] = {}
_items: list[str] = []


def pytest_collection_modifyitems(config, items):
    # the whole suite is collected (every test module imported, as in the labelling
    # run), then everything but BR_INSTR_ONLY is deselected
    only = os.environ.get("BR_INSTR_ONLY")
    if only:
        keep = [i for i in items if i.nodeid == only]
        drop = [i for i in items if i.nodeid != only]
        if drop:
            config.hook.pytest_deselected(items=drop)
        items[:] = keep
    _items.extend(i.nodeid for i in items)


def pytest_runtest_logreport(report):
    if report.failed:
        _outcomes[report.nodeid] = f"failed:{report.when}"
    elif report.when == "call" and report.nodeid not in _outcomes:
        _outcomes[report.nodeid] = report.outcome


def pytest_unconfigure(config):
    M.set_events(_tool, 0)
    M.free_tool_id(_tool)
    files: dict[str, set] = {}
    positions: dict = {}
    for code, offset in _seen:
        rel = _relpath(code.co_filename)
        if rel is None:
            continue
        pos = positions.get(code)
        if pos is None:
            pos = positions[code] = list(code.co_positions())
        i = offset // 2
        if i >= len(pos) or pos[i][0] is None:
            continue
        line, end_line, col, end_col = pos[i]
        files.setdefault(rel, set()).add((line, -1 if col is None else col,
                                          line if end_line is None else end_line,
                                          1 << 20 if end_col is None else end_col))
    tag = os.environ.get("BR_INSTR_TAG", str(os.getpid()))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"instr-{tag}.json"), "w", encoding="utf-8") as handle:
        json.dump({"items": _items, "outcomes": _outcomes, "source_reads": sorted(set(_source_reads)),
                   "files": {rel: sorted(s) for rel, s in files.items()}},
                  handle, separators=(",", ":"))
