"""pytest plugin: per-test attribute-read profile of sqlglot (see br_attr_rt).

Loaded with `-p br_attr_plugin --assert=plain` (this directory on PYTHONPATH,
PYTHONHASHSEED=0). Env: BR_ATTR_ROOT (sqlglot checkout), BR_ATTR_OUT (output
directory), BR_ATTR_TRACKED (JSON list of tracked attribute names).

At configure time, after the import hook is installed, it imports every sqlglot
module, names every class canonically (br_state.canonical_classes), replaces
every class-level dict by a recording TD (aliases in other class or module
namespaces get the same TD) and fingerprints the whole import-time state. At
the end it fingerprints it again: anything sqlglot state that tests changed is
reported (runtime caches and registries), because a value cached by one test
and reused by another is a read the profile would not see.

Phases are attributed like br_cov_plugin: setup/teardown to the whole test
class, collection to the whole test module, call to the test. Work done
outside any phase (between tests) goes to a "global" bucket that the build
treats as read by every test. Before each phase the known pure memo caches are
emptied so every test rebuilds (and so reads) what it uses.
"""

from __future__ import annotations

import concurrent.futures
import doctest
import gc
import json
import os
import pickle
import sys

import pytest

LIB = os.path.dirname(os.path.abspath(__file__))
if LIB not in sys.path:
    sys.path.insert(0, LIB)

import br_attr_instr  # noqa: E402
import br_attr_rt as rt  # noqa: E402
import br_state  # noqa: E402

ROOT = os.path.realpath(os.environ["BR_ATTR_ROOT"])
OUT = os.environ["BR_ATTR_OUT"]
with open(os.environ["BR_ATTR_TRACKED"], encoding="utf-8") as _fh:
    TRACKED = frozenset(json.load(_fh))
rt.TRACKED = TRACKED
br_attr_instr.install(ROOT, set(TRACKED))

# pure memo caches of the pinned sqlglot, emptied before every phase:
# (module, global name). Anything else that changes at run time is reported.
MEMO_CACHES = [("sqlglot.generator", "_DISPATCH_CACHE")]


# ---------------------------------------------------------------- process pools


class _InlinePool(concurrent.futures.ThreadPoolExecutor):
    """Stand-in for ProcessPoolExecutor: one thread, pickle round trips."""

    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **_ignored):
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
except Exception:  # noqa: BLE001
    pass


# doctest examples are compiled from strings: rewrite them too
_builtin_compile = compile


def _doctest_compile(source, filename, mode, flags=0, dont_inherit=False, *a, **k):
    try:
        return br_attr_instr.rewrite_code(source, filename, set(TRACKED), mode, flags, dont_inherit)
    except SyntaxError:
        return _builtin_compile(source, filename, mode, flags, dont_inherit, *a, **k)


doctest.compile = _doctest_compile  # type: ignore[attr-defined]


# ---------------------------------------------------------------- bookkeeping

PHASES = {"setup": 0, "call": 1, "teardown": 2, "collect": 3}
_tests: list[str] = []
_pos: dict[str, int] = {}
_names: dict = {}            # class -> canonical key
_fper = None
_attr_index: dict[str, list] = {}           # "ckey|attr" -> codes
_key_index: dict[int, dict[str, list]] = {}  # tid -> keyfp -> codes
_star_index: dict[int, list] = {}
_iter_index: dict[int, list] = {}
_outcomes: dict[str, str] = {}
_table_names: dict[int, list] = {}
_leaky: dict[int, list] = {}
_start_state = None
_memo: list = []
_keyfp: dict[int, dict] = {}
_normal: dict[int, dict] = {}
_debug_tid = None


SCHEMA_CACHES = ("_type_mapping_cache", "_normalized_table_cache", "_normalized_name_cache", "_find_cache")


def _clear_memos():
    for d in _memo:
        dict.clear(d)
    # the global default schema (sqlglot.schema) lives across tests: its memo
    # caches of normalized names and types are emptied too
    schema = getattr(sys.modules.get("sqlglot"), "schema", None)
    for attr in SCHEMA_CACHES:
        cache = getattr(schema, attr, None)
        if isinstance(cache, dict):
            dict.clear(cache)


def _begin():
    _flush("global")
    _clear_memos()


def _code_for(key: str, phase: str) -> int:
    if key not in _pos:
        _pos[key] = len(_tests)
        _tests.append(key)
    return _pos[key] * 4 + PHASES[phase]


def _flush(key: str, phase: str | None = None):
    """Move the records collected so far to `key` (a test, collect::module or global)."""
    a, k, s, i = rt.attrs, rt.keys, rt.star, rt.iters
    rt.attrs, rt.keys, rt.star, rt.iters = set(), set(), set(), set()
    code = -1 if phase is None else _code_for(key, phase)
    if not (a or k or s or i):
        return
    for klass, name in a:
        ckey = _names.get(klass)
        if ckey is not None:
            _attr_index.setdefault(f"{ckey}|{name}", []).append(code)
    for tid, kk in k:
        fp = _key_fp(tid, kk)
        if _debug_tid is not None and tid == _debug_tid:
            print("BRDEBUG", key, repr(kk), fp, file=sys.stderr)
        _key_index.setdefault(tid, {}).setdefault(fp, []).append(code)
    for tid in s:
        _star_index.setdefault(tid, []).append(code)
    for tid in i:
        _iter_index.setdefault(tid, []).append(code)


def _key_fp(tid: int, key) -> str:
    cache = _keyfp.setdefault(tid, {})
    try:
        got = cache.get(key)
    except TypeError:
        return "unhashable"
    if got is None:
        normal = _normal.get(tid)
        if normal is None:
            table = rt.tables[tid]
            normal = _normal[tid] = {k: k for k in dict.keys(table)}
        stored = normal.get(key, key)
        got = cache[key] = str(_fper.fp(stored))
    return got


def pytest_configure(config):
    global _fper, _start_state
    sys.path.insert(0, ROOT)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        br_state.import_all(__import__("pathlib").Path(ROOT))
    names = br_state.canonical_classes()
    _names.update(names)
    _fper = br_state.Fingerprinter(names)
    for module, attr in MEMO_CACHES:
        mod = sys.modules.get(module)
        cache = getattr(mod, attr, None) if mod else None
        if isinstance(cache, dict):
            _memo.append(cache)
    # wrap class-level dicts
    wrapped: dict[int, rt.TD] = {}
    raws: dict[int, dict] = {}
    for klass, ckey in sorted(names.items(), key=lambda kv: kv[1]):
        for attr, value in list(klass.__dict__.items()):
            if type(value) is not dict or attr.startswith("__"):
                continue
            td = wrapped.get(id(value))
            if td is None:
                td = wrapped[id(value)] = rt.wrap(value)
                raws[id(value)] = value
            _table_names.setdefault(td._src[0], []).append([ckey, attr])
            type.__setattr__(klass, attr, td)
    module_dicts = []
    for name, mod in list(sys.modules.items()):
        if mod is not None and (name == "sqlglot" or name.startswith("sqlglot.")):
            d = vars(mod)
            module_dicts.append(d)
            for attr, value in list(d.items()):
                if type(value) is dict and id(value) in wrapped:
                    d[attr] = wrapped[id(value)]
    # who else still holds an original dict? (a read through it is invisible)
    gc.collect()
    allowed = {id(gc.get_referents(k.__dict__)[0]) for k in names if gc.get_referents(k.__dict__)}
    allowed |= {id(d) for d in module_dicts} | {id(raws)}
    raw_ids = set(raws)
    for obj in gc.get_objects():
        oid = id(obj)
        if oid in allowed or oid in raw_ids:
            continue
        for ref in gc.get_referents(obj):
            if id(ref) in raw_ids:
                tid = wrapped[id(ref)]._src[0]
                if os.environ.get("BR_ATTR_DEBUG") and tid < 3:
                    print("LEAK", tid, type(obj), repr(obj)[:200], file=sys.stderr)
                _leaky.setdefault(tid, []).append(type(obj).__name__)
    raws.clear()
    global _debug_tid
    for tid, names_ in _table_names.items():
        if os.environ.get("BR_ATTR_DEBUG_TABLE") in ("|".join(n) for n in names_):
            _debug_tid = tid
    _start_state = br_state.snapshot(names)
    rt.on = True


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    if isinstance(collector, pytest.Module):
        _begin()
        yield
        _flush("collect::" + collector.nodeid, "collect")
    else:
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    _begin()
    yield
    _flush(item.nodeid, "setup")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    _begin()
    yield
    _flush(item.nodeid, "call")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    _begin()
    yield
    _flush(item.nodeid, "teardown")


def pytest_runtest_logreport(report):
    if report.failed:
        _outcomes[report.nodeid] = f"failed:{report.when}"
    elif report.when == "call" and report.nodeid not in _outcomes:
        _outcomes[report.nodeid] = report.outcome


def pytest_unconfigure(config):
    if _fper is None:
        return
    _flush("global")
    rt.on = False
    _clear_memos()
    end_state = br_state.snapshot(_names)
    changed = br_state.diff(_start_state, end_state)
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"attr-{worker}.json"), "w", encoding="utf-8") as handle:
        json.dump({"tests": _tests, "attrs": _attr_index,
                   "keys": {str(t): v for t, v in _key_index.items()},
                   "star": {str(t): v for t, v in _star_index.items()},
                   "iters": {str(t): v for t, v in _iter_index.items()},
                   "tables": {str(t): v for t, v in _table_names.items()},
                   "leaky": {str(t): v for t, v in _leaky.items()},
                   "runtime_changes": changed, "outcomes": _outcomes}, handle)
