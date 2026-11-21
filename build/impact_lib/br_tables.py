"""Per-test reads of class-level data in the Dialect, Generator, Parser and
Tokenizer class families, recorded inside the coverage pass (br_cov_plugin).

Two layers, installed after `import sqlglot` on every sqlglot class of those
families (and on classes created later, through class-creation hooks):

* every plain data attribute (dict, set, list, tuple, scalar, enum member) in a
  class's own __dict__ is replaced by a non-data descriptor that returns the
  same value and records "this test obtained <class>.<attr>" (attribute level);
* every exact-dict value is additionally replaced by a recording dict subclass
  (_TD) holding the same items, which records, per test, the keys looked up
  (hit or miss: [], get, in, pop, setdefault, set, del), key-set reads (iter,
  len, keys, bool) and whole-value reads (values, items, copy, ==, repr, |,
  pickling). Overriding __iter__ takes CPython's dict-merge fast path off, so
  dict(td), {**td}, d.update(td) and d | td go through keys() + [] and are seen.
  A dict also held elsewhere (refcount above its class slots) keeps its plain
  object and only the attribute level.

Reads made while a sqlglot module executes (lazy imports inside a test) are not
recorded: what import derives from a table is found by the run-time import-time
state diff (br_state.py diff), which follows every propagation into subclasses,
merged tables, tries and metaclass products. Views or iterators of a table taken
during such an import and still alive at the end mark that table unsafe.

The generator's dispatch cache (sqlglot.generator._DISPATCH_CACHE, built once
per class from dict(cls.TRANSFORMS) and shared by later tests) is built with
recording off and returned as a _Dispatch dict whose every lookup records the
same key of the TRANSFORMS table it was built from.

At the end, an entry whose descriptor is gone, a tracked-type attribute added
at run time, or a table whose content a test left changed is listed as unsafe.
Key names are portable (br_state.portable_key) so the selector can match them
with the state diff's changed keys.
"""

from __future__ import annotations

import enum
import sys

_PLAIN = (dict, set, frozenset, list, tuple, bool, int, float, str, type(None))
_SINK: set = set()
_cur: set = _SINK
_gen = 0           # phase generation (attribute reads are recorded once per phase)
_imp = 0           # depth of sqlglot module executions in progress
_attrs: list = []  # eid -> [klass, attr, descriptor]
_tds: list = []    # tid -> _TD
_td_eids: dict = {}  # tid -> [eid, ...]
_by_class: dict = {}  # klass -> {attr: eid}
_index: dict = {}  # record key -> [codes]
_unsafe: set = set()
_imp_views: list = []  # (tid, view or iterator) taken during an import
_dirty: dict = {}  # tid -> content copy before the first test-time mutation of a phase
_installed_ok = False
_families: tuple = ()
_names = None


class _Attr:
    __slots__ = ("value", "eid", "gen")

    def __init__(self, value, eid):
        self.value = value
        self.eid = eid
        self.gen = -1

    def __get__(self, inst, owner=None):
        if self.gen != _gen and not _imp:
            self.gen = _gen
            _cur.add(self.eid)
        return self.value


def _rk(tid, key):
    if not _imp:
        try:
            _cur.add((tid, key))
        except TypeError:  # unhashable: the dict raises the same way below
            pass


def _rK(tid):
    if not _imp:
        _cur.add(("K", tid))


def _rA(tid):
    if not _imp:
        _cur.add(("A", tid))


def _mut(td):
    if not _imp and td._tid not in _dirty:
        _dirty[td._tid] = list(dict.items(td))


class _TD(dict):
    __slots__ = ("_tid",)

    # the three hot lookups record inline (same as _rk, one call less)
    def __getitem__(self, key):
        if not _imp:
            try:
                _cur.add((self._tid, key))
            except TypeError:
                pass
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        if not _imp:
            try:
                _cur.add((self._tid, key))
            except TypeError:
                pass
        return dict.get(self, key, default)

    def __contains__(self, key):
        if not _imp:
            try:
                _cur.add((self._tid, key))
            except TypeError:
                pass
        return dict.__contains__(self, key)

    def setdefault(self, key, default=None):
        _rk(self._tid, key)
        if not dict.__contains__(self, key):
            _mut(self)
        return dict.setdefault(self, key, default)

    def pop(self, key, *default):
        _rk(self._tid, key)
        if dict.__contains__(self, key):
            _mut(self)
        return dict.pop(self, key, *default)

    def __setitem__(self, key, value):
        _rk(self._tid, key)
        _mut(self)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        _rk(self._tid, key)
        _mut(self)
        dict.__delitem__(self, key)

    def popitem(self):
        _rA(self._tid)
        _mut(self)
        return dict.popitem(self)

    def clear(self):
        _rA(self._tid)
        _mut(self)
        dict.clear(self)

    def update(self, *args, **kwargs):
        _rA(self._tid)
        _mut(self)
        dict.update(self, *args, **kwargs)

    def __ior__(self, other):
        _rA(self._tid)
        _mut(self)
        dict.update(self, other)
        return self

    def __iter__(self):
        _rK(self._tid)
        it = dict.__iter__(self)
        if _imp:
            _imp_views.append((self._tid, it))
        return it

    def __reversed__(self):
        _rK(self._tid)
        it = dict.__reversed__(self)
        if _imp:
            _imp_views.append((self._tid, it))
        return it

    def __len__(self):
        _rK(self._tid)
        return dict.__len__(self)

    def keys(self):
        _rK(self._tid)
        v = dict.keys(self)
        if _imp:
            _imp_views.append((self._tid, v))
        return v

    def values(self):
        _rA(self._tid)
        v = dict.values(self)
        if _imp:
            _imp_views.append((self._tid, v))
        return v

    def items(self):
        _rA(self._tid)
        v = dict.items(self)
        if _imp:
            _imp_views.append((self._tid, v))
        return v

    def copy(self):
        _rA(self._tid)
        return dict(dict.items(self))

    def __copy__(self):
        return self.copy()

    def __deepcopy__(self, memo):
        import copy

        _rA(self._tid)
        return copy.deepcopy(dict(dict.items(self)), memo)

    def __reduce_ex__(self, protocol):
        _rA(self._tid)
        return (dict, (dict(dict.items(self)),))

    def __reduce__(self):
        return self.__reduce_ex__(2)

    def __eq__(self, other):
        _rA(self._tid)
        return dict.__eq__(self, other)

    def __ne__(self, other):
        _rA(self._tid)
        return dict.__ne__(self, other)

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self):
        _rA(self._tid)
        return dict.__repr__(self)

    def __str__(self):
        return self.__repr__()

    def __format__(self, spec):
        return format(self.__repr__(), spec)

    def __or__(self, other):
        _rA(self._tid)
        if not isinstance(other, dict):
            return NotImplemented
        out = dict(dict.items(self))
        out.update(other)
        return out

    def __ror__(self, other):
        _rA(self._tid)
        if not isinstance(other, dict):
            return NotImplemented
        out = dict(other)
        out.update(dict.items(self))
        return out

    def __sizeof__(self):
        return dict.__sizeof__(self)


class _Dispatch(dict):
    """The generator's dispatch dict: every lookup is a lookup of the same key in
    the TRANSFORMS table it was built from (or an attribute-level read of it)."""

    __slots__ = ("_src",)

    def _rec(self, key):
        if _imp:
            return
        src = self._src
        try:
            if isinstance(src, _TD):
                _cur.add((src._tid, key))
            else:
                _cur.add(src)  # eid: attribute-level read of TRANSFORMS
        except TypeError:
            pass

    def __getitem__(self, key):
        self._rec(key)
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        self._rec(key)
        return dict.get(self, key, default)

    def __contains__(self, key):
        self._rec(key)
        return dict.__contains__(self, key)

    def _all(self):
        if _imp:
            return
        src = self._src
        _cur.add(("A", src._tid) if isinstance(src, _TD) else src)

    def __iter__(self):
        self._all()
        return dict.__iter__(self)

    def __len__(self):
        self._all()
        return dict.__len__(self)

    def keys(self):
        self._all()
        return dict.keys(self)

    def values(self):
        self._all()
        return dict.values(self)

    def items(self):
        self._all()
        return dict.items(self)

    def __reduce_ex__(self, protocol):
        self._all()
        return (dict, (dict(dict.items(self)),))


# ---------------------------------------------------------------- install


def _family(klass) -> bool:
    mod = getattr(klass, "__module__", "") or ""
    return (mod == "sqlglot" or mod.startswith("sqlglot.")) and issubclass(klass, _families)


_BASELINE = None
_slot_count: dict = {}  # id(dict) -> number of family class slots holding it (first install)


def _scan(klass) -> list:
    """(attr, value, refcount) of the trackable own attributes; the refcount is taken
    on one fixed code path, so a dict held only by class slots gives the baseline
    plus its number of further slots."""
    out = []
    for attr, value in list(klass.__dict__.items()):
        if attr.startswith("__") or type(value) is _Attr:
            continue
        if not (isinstance(value, _PLAIN) or isinstance(value, enum.Enum)):
            continue
        out.append((attr, value, sys.getrefcount(value) if type(value) is dict else 0))
    return out


def _baseline() -> int:
    probe = type("_BrProbe", (), {"X": {}})
    return _scan(probe)[0][2]


def _install(klass) -> None:
    if klass in _by_class or not _family(klass):
        return
    own = _by_class.setdefault(klass, {})
    scanned = _scan(klass)
    local: dict = {}
    for _attr, value, _rc in scanned:
        if type(value) is dict:
            local[id(value)] = local.get(id(value), 0) + 1
    seen: dict = {}
    for attr, value, rc in scanned:
        stored = value
        if type(value) is _TD:
            stored = value  # a class body bound an existing table (alias): share it
        elif type(value) is dict:
            j = seen.get(id(value), 0)
            seen[id(value)] = j + 1
            stored = _aliases.get(id(value))
            if stored is None:
                # refs from class slots: 1 per slot in another class; in this class 2
                # per further slot (its dict entry and _scan's item tuple) plus 1 per
                # earlier occurrence (_scan's output tuple)
                n = local[id(value)]
                slots = _slot_count.get(id(value), n)
                expected = _BASELINE + 2 * (n - 1) + j + (slots - n)
                if rc > expected:
                    stored = value  # held elsewhere too: attribute level only
                else:
                    td = _TD(value)
                    td._tid = len(_tds)
                    _tds.append(td)
                    _aliases[id(value)] = td
                    _keep.append(value)  # keep the id unique while _aliases refers to it
                    stored = td
        eid = len(_attrs)
        desc = _Attr(stored, eid)
        _attrs.append([klass, attr, desc])
        own[attr] = eid
        if type(stored) is _TD:
            _td_eids.setdefault(stored._tid, []).append(eid)
        type.__setattr__(klass, attr, desc)


_aliases: dict = {}  # id(original dict) -> _TD (one table object per original)
_keep: list = []
_all_family: list = []


def _walk(roots) -> list:
    found, stack, seen = [], list(roots), set(roots)
    while stack:
        k = stack.pop()
        found.append(k)
        for s in type.__subclasses__(k):
            if s not in seen:
                seen.add(s)
                stack.append(s)
    return found


def setup() -> bool:
    """Install on every loaded family class and hook class creation. False when
    the families cannot be found (no table profile then)."""
    global _families, _BASELINE, _installed_ok, _all_family
    import gc

    try:
        from sqlglot import generator, parser, tokens
        from sqlglot.dialects import dialect
    except Exception:  # noqa: BLE001
        return False
    _families = (dialect.Dialect, generator.Generator, parser.Parser, tokens._TokenizerBase)
    gc.collect()
    _BASELINE = _baseline()
    _all_family = [k for k in _walk(_families) if _family(k)]
    for k in _all_family:
        for v in k.__dict__.values():
            if type(v) is dict:
                _slot_count[id(v)] = _slot_count.get(id(v), 0) + 1
    for k in _all_family:
        _install(k)
    _slot_count.clear()
    _all_family = []
    _hook_imports()
    _hook_classes(dialect, generator, parser, tokens)
    _installed_ok = True
    return True


def _hook_imports() -> None:
    import importlib._bootstrap_external as be

    orig = be._LoaderBasics.exec_module

    def exec_module(self, module, _orig=orig):
        global _imp
        name = getattr(module, "__name__", "") or ""
        if name == "sqlglot" or name.startswith("sqlglot."):
            _imp += 1
            try:
                return _orig(self, module)
            finally:
                _imp -= 1
                _flush()
        return _orig(self, module)

    be._LoaderBasics.exec_module = exec_module


def _hook_classes(dialect, generator, parser, tokens) -> None:
    meta = type(dialect.Dialect)
    orig_new = meta.__new__

    def __new__(mcs, clsname, bases, attrs, _orig=orig_new):
        klass = _orig(mcs, clsname, bases, attrs)
        for k in (klass, klass.__dict__.get("tokenizer_class"), klass.__dict__.get("jsonpath_tokenizer_class"),
                  klass.__dict__.get("parser_class"), klass.__dict__.get("generator_class")):
            if isinstance(k, type):
                _later.append(k)
        return klass

    meta.__new__ = __new__

    tb = tokens._TokenizerBase
    orig_tok = tb.__dict__["__init_subclass__"].__func__

    def tok_init_subclass(cls, **kwargs):
        orig_tok(cls, **kwargs)
        if not issubclass(cls, dialect.Dialect):
            _install_later(cls)

    tb.__init_subclass__ = classmethod(tok_init_subclass)

    for base in (generator.Generator, parser.Parser):
        def plain_init_subclass(cls, _base=base, **kwargs):
            super(_base, cls).__init_subclass__(**kwargs)
            _install_later(cls)

        type.__setattr__(base, "__init_subclass__", classmethod(plain_init_subclass))

    orig_build = generator._build_dispatch

    def _build_dispatch(cls, _orig=orig_build):
        global _imp
        _imp += 1
        try:
            plain = _orig(cls)
            src = None
            for k in cls.__mro__:
                eid = _by_class.get(k, {}).get("TRANSFORMS")
                if eid is not None:
                    value = _attrs[eid][2].value
                    src = value if isinstance(value, _TD) else eid
                    break
                if "TRANSFORMS" in k.__dict__:
                    break
        finally:
            _imp -= 1
        if src is None:
            return plain
        out = _Dispatch(plain)
        out._src = src
        return out

    generator._build_dispatch = _build_dispatch


_later: list = []


def _install_later(cls) -> None:
    """Class created after setup (lazy import or a test's own subclass): installed
    at the next flush (end of a sqlglot module's execution, phase boundaries), once
    the class-creation namespace that still holds its values is gone (the refcount
    check would see it) and any metaclass has finished it."""
    _later.append(cls)


def _flush() -> None:
    while _later:
        batch = list(_later)
        _later.clear()
        for k in batch:
            _install(k)


# ---------------------------------------------------------------- phases


def begin() -> None:
    global _cur, _gen
    _flush()
    _gen += 1
    _cur = set()


def end() -> set:
    global _cur, _gen
    _flush()
    got = _cur
    _cur = _SINK
    _gen += 1
    _SINK.clear()
    if _dirty:
        for tid, before in list(_dirty.items()):
            if list(dict.items(_tds[tid])) != before:
                _unsafe.add(("T", tid))
        _dirty.clear()
    return got


def store(records: set, code: int) -> None:
    """Fold one phase's records into the index (record -> codes)."""
    for rec in records:
        if type(rec) is int:
            key = ("a", rec)
        elif rec[0] == "K" or rec[0] == "A":
            key = rec
        else:
            key = ("k", rec[0], portable(rec[1]))
        _index.setdefault(key, []).append(code)


def portable(key) -> str:
    try:
        import br_state

        return br_state.portable_key(key)
    except Exception:  # noqa: BLE001
        return "?"


# ---------------------------------------------------------------- results


def results(class_key) -> dict:
    """{"reads": {entry: codes}, "keys": {entry: {key: codes}}, "keyset": {...},
    "all": {...}, "tracked": [...], "tables": [...], "unsafe": [...]}; entry is
    "<class key>|<attr>"; class_key(klass) gives the state diff's class name."""
    if not _installed_ok:
        return {"ok": False}
    unsafe: set = set()
    names: dict = {}

    def entry(eid):
        got = names.get(eid)
        if got is None:
            klass, attr, _ = _attrs[eid]
            ck = class_key(klass)
            got = names[eid] = f"{ck}|{attr}" if ck else None
        return got

    # descriptors no longer in place, plain attributes added at run time
    for eid, (klass, attr, desc) in enumerate(_attrs):
        if klass.__dict__.get(attr) is not desc:
            unsafe.add(entry(eid) or "*")
    for klass, own in _by_class.items():
        for attr, value in list(klass.__dict__.items()):
            if attr in own or attr.startswith("__"):
                continue
            if isinstance(value, _PLAIN) or isinstance(value, enum.Enum):
                ck = class_key(klass)
                unsafe.add(f"{ck}|{attr}" if ck else "*")
    for _tid, it in list(_imp_views):
        pass
    held = set()
    for i, (tid, obj) in enumerate(_imp_views):
        # references: the tuple in _imp_views, the loop variable, getrefcount's argument
        if sys.getrefcount(obj) > 3:
            held.add(tid)
    for tid in held:
        unsafe.add(("T", tid))
    for item in list(_unsafe):
        if isinstance(item, tuple) and item[0] == "T":
            for eid in _td_eids.get(item[1], []):
                unsafe.add(entry(eid) or "*")
    for tid in held:
        for eid in _td_eids.get(tid, []):
            unsafe.add(entry(eid) or "*")
    unsafe = {u for u in unsafe if isinstance(u, str)}

    def entries_of_tid(tid):
        return [e for e in (entry(eid) for eid in _td_eids.get(tid, [])) if e]

    reads: dict = {}
    keys: dict = {}
    keyset: dict = {}
    whole: dict = {}
    for rec, codes in _index.items():
        if rec[0] == "a":
            e = entry(rec[1])
            if e:
                reads.setdefault(e, []).extend(codes)
        elif rec[0] == "K":
            for e in entries_of_tid(rec[1]):
                keyset.setdefault(e, []).extend(codes)
        elif rec[0] == "A":
            for e in entries_of_tid(rec[1]):
                whole.setdefault(e, []).extend(codes)
        else:
            for e in entries_of_tid(rec[1]):
                keys.setdefault(e, {}).setdefault(rec[2], []).extend(codes)
    tracked = sorted({e for e in (entry(eid) for eid in range(len(_attrs))) if e})
    tables = sorted({e for tid in range(len(_tds)) for e in entries_of_tid(tid)})
    dup = sorted(e for e in tracked if tracked.count(e) > 1)
    return {"ok": True, "reads": reads, "keys": keys, "keyset": keyset, "all": whole,
            "tracked": tracked, "tables": tables, "unsafe": sorted(unsafe | set(dup))}
