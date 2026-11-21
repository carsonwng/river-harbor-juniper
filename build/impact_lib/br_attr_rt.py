"""Run-time side of the attribute-read profile (helpers called by code rewritten
by br_attr_instr, plus the table wrapper).

Per phase (set by br_attr_plugin) it collects:
  attrs: (owner class, name) for every read `obj.NAME` of a tracked name, where
         owner is the class in the MRO whose __dict__ supplied the value
         (an instance attribute shadowing it records nothing; a data
         descriptor on the class wins over the instance, as in Python);
         (class, "*") for a read of a class's whole namespace (__dict__, vars).
  keys:  (table id, key) for every lookup in a wrapped class-level dict
         (getitem, get, in, setdefault, pop), hit or miss.
  star:  table ids whose values were read in bulk (items, values, ==, repr,
         pickling, ...).
  iters: table ids whose key set was observed (iteration, keys, len).

Class-level dicts are replaced (after import) by TD, a dict subclass whose
overrides record the above. Overriding __iter__ also takes CPython's merge
fast path off ({**d}, dict(d), d.update): they then go through keys() and
__getitem__ and are recorded. dict(x) and x.copy() of a table give a TD that
records lookups against its source tables (so a cached derived table, e.g. the
generator's dispatch dict, still attributes lookups to the test that makes
them).
"""

from __future__ import annotations

import builtins
import copy as _copy

on = False
attrs: set = set()
keys: set = set()
star: set = set()
iters: set = set()
TRACKED: frozenset = frozenset()
tables: list = []          # tid -> the TD created for that table
_getattr = getattr
_dict_getitem = dict.__getitem__
_dict_get = dict.get
_dict_contains = dict.__contains__


_ocache: dict = {}   # (class or type, name, is_class) -> (owner, data descriptor)


def clear_cache() -> None:
    """Emptied at every phase start (br_prof.begin): a test that rebinds class
    attributes at run time can only leave a stale owner within its own phase,
    and the owner is re-validated on every hit."""
    _ocache.clear()


def _owner(obj, name):
    """Cached _owner_slow: the MRO walk is the hot part of the profile (tokenizer
    loops). A hit is re-validated (the owner still defines the name)."""
    tp = type(obj)
    if tp is super:
        return _owner_slow(obj, name)
    is_class = isinstance(obj, type)
    key = (obj if is_class else tp, name, is_class)
    try:
        hit = _ocache.get(key)
    except TypeError:
        return _owner_slow(obj, name)
    if hit is None:
        return _owner_fill(obj, name, key, is_class)
    k, data = hit
    if k is None or name not in k.__dict__:
        return _owner_fill(obj, name, key, is_class)
    if is_class or data:
        return k
    try:
        inst = object.__getattribute__(obj, "__dict__")
    except Exception:  # noqa: BLE001
        inst = None
    if isinstance(inst, dict) and name in inst:
        return None
    return k


def _owner_fill(obj, name, key, is_class):
    if is_class:
        k = _owner_slow(obj, name)
        if k is not None:
            _ocache[key] = (k, True)
        return k
    kk = _owner_slow_cls(type(obj), name)
    if kk is not None:
        vt = type(kk.__dict__[name])
        _ocache[key] = (kk, hasattr(vt, "__set__") or hasattr(vt, "__delete__"))
    return _owner_slow(obj, name)


def _owner_slow_cls(tp, name):
    for k in tp.__mro__:
        if name in k.__dict__:
            return k
    return None


def _owner_slow(obj, name):
    if isinstance(obj, type):
        for k in obj.__mro__:
            if name in k.__dict__:
                return k
        for k in type(obj).__mro__:
            if name in k.__dict__:
                return k
        return None
    tp = type(obj)
    if tp is super:
        so = obj.__self_class__
        if so is None:
            return None
        mro = so.__mro__
        try:
            i = mro.index(obj.__thisclass__)
        except ValueError:
            return None
        for k in mro[i + 1:]:
            if name in k.__dict__:
                return k
        return None
    for k in tp.__mro__:
        d = k.__dict__
        if name in d:
            vt = type(d[name])
            if hasattr(vt, "__set__") or hasattr(vt, "__delete__"):
                return k
            try:
                inst = object.__getattribute__(obj, "__dict__")
            except Exception:  # noqa: BLE001
                inst = None
            if isinstance(inst, dict) and name in inst:
                return None
            return k
    return None


SKIP_IDS: set = set()  # opaque classes (enums ...): their changes never use the profile


def rd(obj, name):
    value = _getattr(obj, name)
    if on and id(obj) not in SKIP_IDS:
        if name == "__dict__":
            if isinstance(obj, type):
                attrs.add((obj, "*"))
        else:
            k = _owner(obj, name)
            if k is not None:
                attrs.add((k, name))
    return value


def ga(obj, name, *default):
    if default:
        try:
            value = _getattr(obj, name)
        except AttributeError:
            return default[0]
    else:
        value = _getattr(obj, name)
    if on and type(name) is str and (name in TRACKED or name == "__dict__") and id(obj) not in SKIP_IDS:
        if name == "__dict__":
            if isinstance(obj, type):
                attrs.add((obj, "*"))
        else:
            k = _owner(obj, name)
            if k is not None:
                attrs.add((k, name))
    return value


def vs(*args):
    value = vars(*args)
    if on and args and isinstance(args[0], type):
        attrs.add((args[0], "*"))
    return value


def dc(x):
    if isinstance(x, TD):
        return x._derive()
    return dict(x)


class TD(dict):
    __slots__ = ("_src",)

    def _derive(self, extra=()):
        new = TD()
        new._src = self._src + tuple(extra)
        dict.update(new, dict.items(self))
        return new

    def _look(self, key):
        if on:
            for t in self._src:
                keys.add((t, key))

    def _star(self):
        if on:
            star.update(self._src)

    def _iter(self):
        if on:
            iters.update(self._src)

    def __getitem__(self, key):
        try:
            return _dict_getitem(self, key)
        finally:
            self._look(key)

    def get(self, key, default=None):
        try:
            return _dict_get(self, key, default)
        finally:
            self._look(key)

    def __contains__(self, key):
        try:
            return _dict_contains(self, key)
        finally:
            self._look(key)

    def setdefault(self, key, default=None):
        try:
            return dict.setdefault(self, key, default)
        finally:
            self._look(key)

    def pop(self, key, *default):
        try:
            return dict.pop(self, key, *default)
        finally:
            self._look(key)

    def popitem(self):
        self._star()
        return dict.popitem(self)

    def __iter__(self):
        self._iter()
        return dict.__iter__(self)

    def __reversed__(self):
        self._iter()
        return dict.__reversed__(self)

    def __len__(self):
        self._iter()
        return dict.__len__(self)

    def keys(self):
        self._iter()
        return dict.keys(self)

    def values(self):
        self._star()
        return dict.values(self)

    def items(self):
        self._star()
        return dict.items(self)

    def copy(self):
        return self._derive()

    def __copy__(self):
        return self._derive()

    def __deepcopy__(self, memo):
        self._star()
        return _copy.deepcopy(dict(dict.items(self)), memo)

    def __reduce_ex__(self, protocol):
        self._star()
        return (dict, (list(dict.items(self)),))

    def __or__(self, other):
        if not isinstance(other, dict):
            return NotImplemented
        new = self._derive(other._src if isinstance(other, TD) else ())
        dict.update(new, dict.items(other) if isinstance(other, TD) else other)
        return new

    def __ror__(self, other):
        if not isinstance(other, dict):
            return NotImplemented
        new = TD()
        new._src = self._src
        dict.update(new, other)
        dict.update(new, dict.items(self))
        return new

    def __eq__(self, other):
        self._star()
        if isinstance(other, TD):
            other._star()
            other = dict(dict.items(other))
        return dict.__eq__(dict(dict.items(self)), other)

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self):
        self._star()
        return dict.__repr__(dict(dict.items(self)))

    def __str__(self):
        return self.__repr__()

    def __format__(self, spec):
        return format(self.__repr__(), spec)


def wrap(raw: dict) -> TD:
    td = TD()
    td._src = (len(tables),)
    dict.update(td, raw)
    tables.append(td)
    return td


builtins._br_rd = rd  # type: ignore[attr-defined]
builtins._br_ga = ga  # type: ignore[attr-defined]
builtins._br_vars = vs  # type: ignore[attr-defined]
builtins._br_dict = dc  # type: ignore[attr-defined]
