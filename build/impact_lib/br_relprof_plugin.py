"""pytest plugin: relation profile of every single-operator comparison in sqlglot.

Loaded with `-p br_relprof_plugin` (this directory on PYTHONPATH) in a second suite
pass, separate from the coverage pass. An import hook compiles every `sqlglot.*`
module from its pinned source with each eligible `ast.Compare` (one operator out of
is, is not, ==, !=, <, <=, >, >=) rewritten into a call

    _br_rp_<op>(site, left, right)

The helper evaluates the ORIGINAL operator exactly once on the already evaluated
operands (Python evaluates call arguments left to right, like a comparison), returns
its result unchanged and re-raises its exceptions unchanged. Line and column of the
call are the original comparison's, and the source file is untouched.

Per evaluation the helper records a 16-bit relation signature: for each of the 8
operators a 2-bit code (0 false, 1 true, 2 raises, 3 unknown). Operators other than
the original are only evaluated when that is provably side-effect free (see
_kind); otherwise they are "unknown". The signature never depends on anything but
the two operand values and their identity.

Records are attributed like br_cov_plugin.py (test index * 4 + phase; collection and
evaluations outside any phase are kept too, so nothing evaluated is ever dropped).

Env: BR_IMPACT_ROOT (sqlglot checkout to instrument), BR_IMPACT_OUT (results dir).
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

import pytest

ROOT = os.path.normcase(os.path.realpath(os.environ["BR_IMPACT_ROOT"]))
OUT = os.environ["BR_IMPACT_OUT"]
PKG = os.path.join(ROOT, "sqlglot") + os.sep

# operator order of the signature; the helper names use the same index
OPS = ("is", "isnot", "eq", "ne", "lt", "le", "gt", "ge")
AST_OPS = {ast.Is: 0, ast.IsNot: 1, ast.Eq: 2, ast.NotEq: 3, ast.Lt: 4, ast.LtE: 5,
           ast.Gt: 6, ast.GtE: 7}
F, T, E, U = 0, 1, 2, 3
ALL_U = 0xFFFF

# ---------------------------------------------------------------- sites

_sites: list[str] = []          # site id -> "rel:line:col"
_site_ids: dict[str, int] = {}


class _Rewrite(ast.NodeTransformer):
    def __init__(self, rel: str) -> None:
        self.rel = rel

    def visit_Compare(self, node):
        self.generic_visit(node)
        if len(node.ops) != 1 or type(node.ops[0]) not in AST_OPS:
            return node
        key = f"{self.rel}:{node.lineno}:{node.col_offset}"
        sid = _site_ids.get(key)
        if sid is None:
            sid = _site_ids[key] = len(_sites)
            _sites.append(key)
        op = AST_OPS[type(node.ops[0])]
        func = ast.Name(id=f"_br_rp_{OPS[op]}", ctx=ast.Load())
        call = ast.Call(func=func, args=[ast.Constant(sid), node.left, node.comparators[0]],
                        keywords=[])
        for n in (call, func, call.args[0]):
            ast.copy_location(n, node)
        return call


class _Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        path = self.get_filename(fullname)
        source = self.get_data(path)
        rel = os.path.relpath(os.path.normcase(os.path.realpath(path)), ROOT).replace(os.sep, "/")
        tree = ast.parse(source, path)
        tree = _Rewrite(rel).visit(tree)
        ast.fix_missing_locations(tree)
        return compile(tree, path, "exec", dont_inherit=True)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname != "sqlglot" and not fullname.startswith("sqlglot."):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or not spec.origin or not spec.origin.endswith(".py"):
            return spec
        if not os.path.normcase(os.path.realpath(spec.origin)).startswith(PKG):
            return spec
        spec.loader = _Loader(fullname, spec.origin)
        return spec


# ---------------------------------------------------------------- operand kinds

V, O, X, UK = 1, 2, 3, 4   # value builtin, object-default (identity), Expr-like, unknown
_RICH = ("__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__")
_VALUE_BASES = (int, float, str, bytes, bool, type(None), complex)
_VALUE_SLOTS = {getattr(b, n) for b in _VALUE_BASES for n in _RICH}
_OBJECT_SLOTS = {getattr(object, n) for n in _RICH}
_kinds: dict[type, int] = {}
_EXPR_EQ = None   # sqlglot.expressions.core.Expr.__eq__, found lazily


def _type_kind(t: type) -> int:
    k = _kinds.get(t)
    if k is not None:
        return k
    slots = [getattr(t, n, None) for n in _RICH]
    if t in (tuple, frozenset):
        k = 0  # decided per value
    elif all(s in _OBJECT_SLOTS for s in slots) and getattr(t, "__hash__", None) is not None:
        k = O
    elif all(s in _VALUE_SLOTS or s in _OBJECT_SLOTS for s in slots) and any(
            issubclass(t, b) for b in _VALUE_BASES):
        # builtin value type or a subclass that overrides no comparison (str/int enums)
        k = V
    elif _EXPR_EQ is not None and getattr(t, "__eq__", None) is _EXPR_EQ \
            and getattr(t, "__ne__", None) is _EXPR_NE:
        k = X
    else:
        k = UK
    _kinds[t] = k
    return k


def _value_kind(x, depth=0) -> int:
    t = type(x)
    k = _kinds.get(t)
    if k is None:
        k = _type_kind(t)
    if k:
        return k
    # tuple / frozenset: value if every element is a value (bounded)
    if depth > 3 or len(x) > 64:
        return UK
    for item in x:
        if _value_kind(item, depth + 1) != V:
            return UK
    return V


def _all_ops(a, b) -> int:
    sig = 0
    for i, fn in enumerate(_FNS):
        try:
            r = fn(a, b)
            c = T if r is True else F if r is False else U
        except Exception:  # noqa: BLE001
            c = E
        sig |= c << (2 * i)
    return sig


_FNS = (lambda a, b: a is b, lambda a, b: a is not b, lambda a, b: a == b,
        lambda a, b: a != b, lambda a, b: a < b, lambda a, b: a <= b,
        lambda a, b: a > b, lambda a, b: a >= b)
_vcache: dict = {}   # (ta, tb, a, b) -> signature of value ops (is/isnot masked out)
_ocache: dict = {}   # (ta, tb, identical) -> full signature
_IS_MASK = 0xF


def _id_bits(same: bool) -> int:
    return (T | (F << 2)) if same else (F | (T << 2))


def _signature(op: int, r, a, b) -> int:
    """Relation signature of one evaluation whose original operator `op` gave `r`."""
    same = a is b
    ka = _value_kind(a)
    kb = _value_kind(b)
    ta = type(a)
    tb = type(b)
    if ka == V and kb == V:
        key = (ta, tb, a, b)
        try:
            sig = _vcache.get(key)
        except Exception:  # noqa: BLE001  unhashable after all
            sig = None
            key = None
        if sig is None:
            sig = _all_ops(a, b) & ~_IS_MASK
            if key is not None:
                if len(_vcache) > 500_000:
                    _vcache.clear()
                _vcache[key] = sig
        return sig | _id_bits(same)
    if (ka == O and kb in (O, V)) or (kb == O and ka == V):
        key = (ta, tb, same)
        sig = _ocache.get(key)
        if sig is None:
            sig = _ocache[key] = _all_ops(a, b)
        return sig
    sig = _id_bits(same) | (ALL_U & ~_IS_MASK)
    if (ka == X and kb in (O, V, X)) or (kb == X and ka in (O, V)):
        # Expr.__eq__: `self is other or (type(self) is type(other) and hash...)`,
        # Expr.__ne__: `not self.__eq__(other)`. Different types, or identical
        # operands: pure and decided without hashing.
        if same:
            eq = T
        elif ta is not tb:
            eq = F
        else:
            eq = U
        if eq != U:
            sig = (sig & ~(0xF << 4)) | (eq << 4) | ((1 - eq) << 6)
    # the original operator's own result is known whatever the operands
    c = T if r is True else F if r is False else U
    return (sig & ~(3 << (2 * op))) | (c << (2 * op))


# ---------------------------------------------------------------- recording

_outside: set = set()
_cur: set = _outside


def _record_error(sid: int) -> None:
    _cur.add((sid << 16) | ALL_U)


def _make(op: int):
    expr = ("a is b", "a is not b", "a == b", "a != b", "a < b", "a <= b", "a > b",
            "a >= b")[op]
    src = (
        "def helper(sid, a, b):\n"
        "    try:\n"
        f"        r = {expr}\n"
        "    except BaseException:\n"
        "        _record_error(sid)\n"
        "        raise\n"
        "    try:\n"
        f"        _cur.add((sid << 16) | _signature({op}, r, a, b))\n"
        "    except Exception:\n"
        "        _cur.add((sid << 16) | 0xFFFF)\n"
        "    return r\n"
    )
    ns: dict = {}
    exec(src, globals(), ns)  # noqa: S102
    return ns["helper"]


for _i, _name in enumerate(OPS):
    setattr(builtins, f"_br_rp_{_name}", _make(_i))


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
except Exception:  # noqa: BLE001
    pass

sys.meta_path.insert(0, _Finder())

# Child processes run sqlglot uninstrumented: the tests that start one are listed
# so the selector never prunes them on the strength of this profile.
_spawned: set = set()
_phase_key: list = [None]


def _note_spawn():
    if _phase_key[0] is not None:
        _spawned.add(_phase_key[0])
    else:
        import traceback

        stack = traceback.extract_stack()
        if not any(os.path.isabs(f.filename) and os.path.normcase(
                os.path.realpath(f.filename)).startswith(ROOT + os.sep) for f in stack):
            return  # pytest/xdist/platform machinery: no sqlglot or test code involved
        where = " < ".join(f"{f.filename}:{f.lineno}"
                           for f in reversed(traceback.extract_stack()[:-2]))
        _spawned.add("<outside>" + where)


import subprocess as _sp  # noqa: E402
import multiprocessing.process as _mpp  # noqa: E402

_orig_popen_init = _sp.Popen.__init__
_orig_mp_start = _mpp.BaseProcess.start


def _popen_init(self, *args, **kwargs):
    _note_spawn()
    return _orig_popen_init(self, *args, **kwargs)


def _mp_start(self):
    _note_spawn()
    return _orig_mp_start(self)


_sp.Popen.__init__ = _popen_init
_mpp.BaseProcess.start = _mp_start


# ---------------------------------------------------------------- bookkeeping

_tests: list[str] = []
_tests_pos: dict[str, int] = {}
_index: dict[int, list] = {}   # (sid << 16 | sig) -> [test index * 4 + phase]
_outcomes: dict[str, str] = {}
PHASES = {"setup": 0, "call": 1, "teardown": 2, "collect": 3}


def _begin(key: str):
    global _cur
    _cur = set()
    _phase_key[0] = key


def _end(key: str, phase: str):
    global _cur
    got = _cur
    _cur = _outside
    _phase_key[0] = None
    if key not in _tests_pos:
        _tests_pos[key] = len(_tests)
        _tests.append(key)
    code = _tests_pos[key] * 4 + PHASES[phase]
    for item in got:
        _index.setdefault(item, []).append(code)


def pytest_configure(config):
    global _EXPR_EQ, _EXPR_NE
    import sqlglot.expressions.core as core  # instrumented through the hook

    # the class defining the structural __eq__/__ne__ quoted in _signature; the
    # inference is only used if the pinned source matches that text exactly
    import inspect

    expr = getattr(core, "Expression", None)
    eq = expr.__dict__.get("__eq__") if expr is not None else None
    ne = expr.__dict__.get("__ne__") if expr is not None else None
    try:
        ok = ("return self is other or (type(self) is type(other) and hash(self) == hash(other))"
              in inspect.getsource(eq) and "return not self.__eq__(other)" in inspect.getsource(ne))
    except Exception:  # noqa: BLE001
        ok = False
    if ok:
        _EXPR_EQ, _EXPR_NE = eq, ne
    _kinds.clear()


_EXPR_NE = None


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    if isinstance(collector, pytest.Module):
        _begin("collect::" + collector.nodeid)
        yield
        _end("collect::" + collector.nodeid, "collect")
    else:
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    _begin(item.nodeid)
    yield
    _end(item.nodeid, "setup")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    _begin(item.nodeid)
    yield
    _end(item.nodeid, "call")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    _begin(item.nodeid)
    yield
    _end(item.nodeid, "teardown")


def pytest_runtest_logreport(report):
    if report.failed:
        _outcomes[report.nodeid] = f"failed:{report.when}"
    elif report.when == "call" and report.nodeid not in _outcomes:
        _outcomes[report.nodeid] = report.outcome


def pytest_unconfigure(config):
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    records: dict[str, list] = {}
    for item, codes in _index.items():
        records[str(item)] = codes
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"relprof-{worker}.json"), "w", encoding="utf-8") as handle:
        json.dump({"tests": _tests, "sites": _sites, "records": records,
                   "outside": sorted(_outside), "outcomes": _outcomes,
                   "spawned": sorted(_spawned)}, handle)
