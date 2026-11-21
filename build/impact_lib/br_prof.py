"""Value profiles recorded inside the forked coverage pass (strat/impact-combo).

Imported by br_cov_plugin when BR_IMPACT_PROFILE=1. One import hook compiles every
module under <root>/sqlglot and <root>/tests from a rewritten AST that chains
  * br_attr_instr.Rewriter (attribute reads of tracked names, getattr, vars, dict,
    `o.NAME op= v`; sqlglot and tests), and
  * br_relprof_plugin._Rewrite (single-operator comparisons; sqlglot only),
keeping every node's line and column, so sys.monitoring LINE events and the
coverage records are those of the plain pass (checked by the build notes).

Per phase it collects what br_relprof_plugin and br_attr_plugin collect in their
own passes (relation signatures and literal-side values per comparison site;
(owner class, attribute) reads and keys looked up in every class-level dict).
In a forked child the records are returned to br_cov_plugin, which pickles them
to the parent together with the coverage records, so every test's profile is
recorded from the same pristine post-collection state as its coverage: a
consumer of a per-process cache recomputes it and is recorded as a reader.

The parent imports every sqlglot module at configure time (so the class-level
dicts can be wrapped once, and children do not recompile instrumented modules).
br_cov_plugin keeps lazily-loading code honest by comparison with the plain
in-process pass (state-dependent functions, see build/impact.py).

Env: BR_IMPACT_ROOT, BR_IMPACT_OUT, BR_ATTR_TRACKED (JSON list; attribute profile
off without it), BR_PROF_RELPROF (default 1).
"""

from __future__ import annotations

import ast
import doctest
import gc
import importlib.machinery
import importlib.util
import json
import os
import sys

LIB = os.path.dirname(os.path.abspath(__file__))
if LIB not in sys.path:
    sys.path.insert(0, LIB)
os.environ["BR_PROF_COMBINED"] = "1"

import br_relprof_plugin as rp  # noqa: E402
import br_attr_instr  # noqa: E402
import br_attr_rt as rt  # noqa: E402
import br_state  # noqa: E402

ROOT = os.path.normcase(os.path.realpath(os.environ["BR_IMPACT_ROOT"]))
OUT = os.environ["BR_IMPACT_OUT"]
RELPROF = os.environ.get("BR_PROF_RELPROF", "1") == "1"
_tracked_path = os.environ.get("BR_ATTR_TRACKED")
ATTRS = bool(_tracked_path) and os.path.exists(_tracked_path or "")
TRACKED: frozenset = frozenset()
if ATTRS:
    with open(_tracked_path, encoding="utf-8") as _fh:
        TRACKED = frozenset(json.load(_fh))
    rt.TRACKED = TRACKED
_TRACKED_SET = set(TRACKED)
# test modules only need rewriting for the attribute profile; otherwise pytest's
# own assertion rewriting keeps them, exactly as in the plain pass
PREFIXES = (os.path.join(ROOT, "sqlglot") + os.sep,) + (
    (os.path.join(ROOT, "tests") + os.sep,) if ATTRS else ())

# ---------------------------------------------------------------- import hook


def _rel(path: str) -> str:
    return os.path.relpath(os.path.normcase(os.path.realpath(path)), ROOT).replace(os.sep, "/")


def transform(tree, path: str, level: int = 0):
    """level 0: attribute + relation instrumentation; 1: attribute only; 2: none."""
    if ATTRS and level <= 1:
        br_attr_instr.Rewriter(_TRACKED_SET).visit(tree)
    rel = _rel(path)
    if RELPROF and level == 0 and rel.startswith("sqlglot/"):
        tree = rp._Rewrite(rel).visit(tree)
    ast.fix_missing_locations(tree)
    return tree


# ---- profile levels. A test whose profiled run exceeds its time budget is run
# again in a fresh child at a lower level (br_cov_plugin): every live function's
# code object is swapped for the same function compiled with less (or no)
# instrumentation. Line numbers are the same at every level, so coverage is too;
# the test is then "unprofiled" for what it no longer records, and the selector
# treats it so (relprof: no inert verdict for a site it covers; attrs: added to
# every verdict about a class it uses).
_variants: dict = {}   # level-0 code -> (level-1 code, level-2 code)
_level = 0


def _pair(full, others) -> None:
    _variants[full] = tuple(others)
    for subs in zip(*([c for c in co.co_consts if hasattr(c, "co_code")] for co in (full,) + tuple(others))):
        if len({(c.co_qualname, c.co_firstlineno) for c in subs}) != 1:
            raise ValueError(f"code trees differ at {subs[0].co_qualname}")
        _pair(subs[0], subs[1:])


def set_level(level: int) -> int:
    """In a child: swap every live function to `level`; returns how many."""
    global _level
    import types

    _level = level
    if level >= 2:
        rt.on = False
    swapped = 0
    for obj in gc.get_objects():
        if type(obj) is types.FunctionType:
            alt = _variants.get(obj.__code__)
            if alt is not None:
                obj.__code__ = alt[level - 1]
                swapped += 1
    return swapped


class _Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        path = self.get_filename(fullname)
        return self.source_to_code(self.get_data(path), path)

    def source_to_code(self, data, path, *, _optimize=-1):
        if _level:
            return compile(transform(ast.parse(data, path), path, _level), path, "exec",
                           dont_inherit=True)
        full = compile(transform(ast.parse(data, path), path, 0), path, "exec", dont_inherit=True)
        try:
            others = [compile(transform(ast.parse(data, path), path, lv), path, "exec",
                              dont_inherit=True) for lv in (1, 2)]
            _pair(full, others)
        except Exception as exc:  # noqa: BLE001  (no lower levels for this module)
            print(f"[br_prof] no profile levels for {path}: {exc!r}", file=sys.stderr)
        return full


class _Finder:
    def find_spec(self, fullname, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or not spec.origin or not spec.origin.endswith(".py"):
            return spec
        if not os.path.normcase(os.path.realpath(spec.origin)).startswith(PREFIXES):
            return spec
        return importlib.util.spec_from_file_location(
            fullname, spec.origin, loader=_Loader(fullname, spec.origin),
            submodule_search_locations=spec.submodule_search_locations)

    def invalidate_caches(self):
        pass


sys.meta_path.insert(0, _Finder())

_builtin_compile = compile


def _doctest_compile(source, filename, mode, flags=0, dont_inherit=False, *a, **k):
    if not ATTRS or _level >= 2:
        return _builtin_compile(source, filename, mode, flags, dont_inherit, *a, **k)
    try:
        return br_attr_instr.rewrite_code(source, filename, _TRACKED_SET, mode, flags, dont_inherit)
    except SyntaxError:
        return _builtin_compile(source, filename, mode, flags, dont_inherit, *a, **k)


doctest.compile = _doctest_compile  # type: ignore[attr-defined]

# ---------------------------------------------------------------- attrs state

from br_prof_memo import clear_memos  # noqa: E402

_names: dict = {}
_fper = None
_keyfp: dict = {}
_normal: dict = {}
_table_names: dict = {}
_leaky: dict = {}
_start_state = None
_configured = False


def configure(root: str) -> None:
    """Parent, after `import sqlglot`: import everything, wrap class-level dicts."""
    global _fper, _start_state, _configured
    import pathlib
    import warnings

    if root not in sys.path:
        sys.path.insert(0, root)
    if ATTRS or os.environ.get("BR_PROF_PREIMPORT") == "1":
        # every class-level dict is wrapped once, here (forked pass: the children
        # inherit them, and nothing is recompiled per test)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            br_state.import_all(pathlib.Path(root))
    if RELPROF:
        rp.setup_expr()
        rp._kinds.clear()
    if ATTRS:
        names = br_state.canonical_classes()
        _names.update(names)
        _fper = br_state.Fingerprinter(names)
        wrapped: dict = {}
        raws: dict = {}
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
                    _leaky.setdefault(wrapped[id(ref)]._src[0], []).append(type(obj).__name__)
        raws.clear()
        _start_state = br_state.snapshot(names)
        # reads of an opaque class (enum, namedtuple, abc, other metaclass) never
        # serve a verdict (blast/impact.py falls back on any change there): do not
        # record them when every sqlglot class in the MRO is opaque (TokenType.X is
        # most of the reads in tokenizer loops)
        opaque = set(_start_state.get("opaque", ()))
        for klass, ckey in names.items():
            mro = [k for k in klass.__mro__ if k in names]
            if mro and all(names[k] in opaque for k in mro):
                rt.SKIP_IDS.add(id(klass))
        rt.on = True
    _configured = True


# ---------------------------------------------------------------- per phase

_global: dict = {"attrs": set(), "keys": set(), "star": set(), "iters": set()}


def _take_attrs() -> dict:
    a, k, s, i = rt.attrs, rt.keys, rt.star, rt.iters
    rt.attrs, rt.keys, rt.star, rt.iters = set(), set(), set(), set()
    if not (a or k or s or i):
        return {}
    was, rt.on = rt.on, False  # fingerprinting keys must not record reads
    try:
        attrs = sorted({f"{_names[klass]}|{name}" for klass, name in a if klass in _names})
        keys = sorted({(tid, _key_fp(tid, kk)) for tid, kk in k})
    finally:
        rt.on = was
    return {"attrs": attrs, "keys": keys, "star": sorted(s), "iters": sorted(i)}


def _key_fp(tid: int, key) -> str:
    cache = _keyfp.setdefault(tid, {})
    try:
        got = cache.get(key)
    except TypeError:
        return "unhashable"
    if got is None:
        normal = _normal.get(tid)
        if normal is None:
            normal = _normal[tid] = {k: k for k in dict.keys(rt.tables[tid])}
        got = cache[key] = str(_fper.fp(normal.get(key, key)))
    return got


def begin(key: str) -> None:
    if ATTRS and _configured:
        got = _take_attrs()  # reads between phases: every test
        for name, items in got.items():
            _global[name].update(map(_hashable, items))
    rt.clear_cache()
    if ATTRS and not _in_child:
        clear_memos()  # shared process: every phase rebuilds what it uses
    rp._begin(key)


def _hashable(x):
    return tuple(x) if isinstance(x, list) else x


def end() -> dict:
    """Records of the phase that just ended (picklable)."""
    out = {}
    got = rp._cur
    rp._cur = rp._outside
    rp._phase_key[0] = None
    if got:
        out["rp"] = sorted(got)
    if ATTRS and _configured:
        att = _take_attrs()
        if att:
            out["at"] = att
    return out


# ---------------------------------------------------------------- parent bookkeeping

_rp_index: dict = {}    # (sid << 16 | sig) -> codes
_at_attrs: dict = {}
_at_keys: dict = {}
_at_star: dict = {}
_at_iters: dict = {}


def record(code: int, data: dict) -> None:
    for item in data.get("rp", ()):
        _rp_index.setdefault(item, []).append(code)
    att = data.get("at")
    if att:
        for k in att["attrs"]:
            _at_attrs.setdefault(k, []).append(code)
        for tid, fp in att["keys"]:
            _at_keys.setdefault(tid, {}).setdefault(fp, []).append(code)
        for tid in att["star"]:
            _at_star.setdefault(tid, []).append(code)
        for tid in att["iters"]:
            _at_iters.setdefault(tid, []).append(code)


# ---------------------------------------------------------------- fork


_base_sites = 0
_in_child = False


def child_start() -> None:
    """In the child right after fork: fresh containers for whole-pass records,
    so the child ships only what it adds."""
    global _base_sites, _in_child
    _in_child = True
    # a forked test starts from the parent's state: empty the memo caches once, so
    # the test (setup phase: its class) rebuilds and is recorded reading what it uses
    if ATTRS:
        clear_memos()
    _base_sites = len(rp._sites)
    rp._outside = set()
    rp._cur = rp._outside
    rp._values = {}
    rp._opaque = set()
    rp._spawned = set()
    for v in _global.values():
        v.clear()


def child_payload() -> dict:
    begin("<child-end>")  # flush reads after the last phase into the global bucket
    rp._cur = rp._outside
    return {"new_sites": rp._sites[_base_sites:], "base_sites": _base_sites,
            "outside": sorted(rp._outside),
            "values": {k: sorted(v, key=repr) for k, v in rp._values.items()},
            "opaque": sorted(rp._opaque), "spawned": sorted(rp._spawned),
            "global": {k: sorted(v) for k, v in _global.items()}}


def merge_child(extra: dict, records: list) -> None:
    """Parent: remap sites a child compiled itself, merge whole-pass records.
    `records` are the child's (key, phase, ..., prof) tuples, remapped in place."""
    base = extra["base_sites"]
    remap = {}
    for i, site in enumerate(extra["new_sites"]):
        sid = rp._site_ids.get(site)
        if sid is None:
            sid = rp._site_ids[site] = len(rp._sites)
            rp._sites.append(site)
        remap[base + i] = sid

    def fix(item: int) -> int:
        sid = item >> 16
        return (remap[sid] << 16) | (item & 0xFFFF) if sid in remap else item

    if remap:
        for rec in records:
            data = rec[-1]
            if "rp" in data:
                data["rp"] = [fix(i) for i in data["rp"]]
    rp._outside.update(fix(i) for i in extra["outside"])
    for sid, vals in extra["values"].items():
        sid = remap.get(sid, sid)
        if sid in rp._opaque:
            continue
        bucket = rp._values.setdefault(sid, set())
        bucket.update(vals)
        if len(bucket) > rp.VALUE_CAP:
            rp._opaque.add(sid)
    rp._opaque.update(remap.get(s, s) for s in extra["opaque"])
    rp._spawned.update(extra["spawned"])
    for name, items in extra["global"].items():
        _global[name].update(map(_hashable, items))


# ---------------------------------------------------------------- output


def dump(tests: list, outcomes: dict) -> None:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    os.makedirs(OUT, exist_ok=True)
    if RELPROF:
        with open(os.path.join(OUT, f"relprof-{worker}.json"), "w", encoding="utf-8") as handle:
            json.dump({"tests": tests, "sites": rp._sites,
                       "records": {str(k): v for k, v in _rp_index.items()},
                       "outside": sorted(rp._outside), "outcomes": outcomes,
                       "spawned": sorted(rp._spawned),
                       "values": {str(k): sorted(v, key=repr) for k, v in rp._values.items()
                                  if k not in rp._opaque},
                       "opaque": sorted(rp._opaque)}, handle)
    if ATTRS and _configured:
        begin("<end>")
        for k in _global["attrs"]:
            _at_attrs.setdefault(k, []).append(-1)
        for tid, fp in _global["keys"]:
            _at_keys.setdefault(tid, {}).setdefault(fp, []).append(-1)
        for tid in _global["star"]:
            _at_star.setdefault(tid, []).append(-1)
        for tid in _global["iters"]:
            _at_iters.setdefault(tid, []).append(-1)
        rt.on = False
        clear_memos()
        changed = br_state.diff(_start_state, br_state.snapshot(_names))
        with open(os.path.join(OUT, f"attr-{worker}.json"), "w", encoding="utf-8") as handle:
            json.dump({"tests": tests, "attrs": _at_attrs,
                       "keys": {str(t): v for t, v in _at_keys.items()},
                       "star": {str(t): v for t, v in _at_star.items()},
                       "iters": {str(t): v for t, v in _at_iters.items()},
                       "tables": {str(t): v for t, v in _table_names.items()},
                       "leaky": {str(t): v for t, v in _leaky.items()},
                       "runtime_changes": changed, "outcomes": outcomes}, handle)
