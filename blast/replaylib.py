"""Record-replay of optimizer rule calls (strat/r2-replay). Shared by the build
(build/replay.py) and the run-time child (blast/replay_child.py).

Never imported by the selector process itself (it only needs the maps' JSON).

- `dumps`/`loads`: pickle that keeps every slot of sqlglot objects (parent pointers,
  cached hashes, types, meta) and object identity, bypassing Expression.__reduce__
  (which serialises a subtree only).
- `pdigest`: sha256 of that pickle. Exact under a fixed PYTHONHASHSEED (cached
  Expression hashes are part of the state).
- `gstate_*`: identity-based fingerprint of every sqlglot module and class namespace,
  valid within one process; `canon_digest`: process-independent digest of a value
  (classes and functions by qualified name) for the caches the pinned run fills.
"""

from __future__ import annotations

import copyreg
import enum
import hashlib
import io
import logging
import pickle
import sys
import types

sys.setrecursionlimit(max(sys.getrecursionlimit(), 100000))

_SLOTS: dict[type, tuple] = {}


def _slotnames(cls):
    s = _SLOTS.get(cls)
    if s is None:
        s = _SLOTS[cls] = tuple(copyreg._slotnames(cls))
    return s


def _rebuild(cls):
    return cls.__new__(cls)


class _Pickler(pickle.Pickler):
    def reducer_override(self, obj):
        cls = type(obj)
        if isinstance(obj, (type, types.FunctionType, types.BuiltinFunctionType, types.ModuleType)):
            return NotImplemented
        if (getattr(cls, "__module__", "") or "").startswith("sqlglot") and not isinstance(obj, enum.Enum):
            st = {}
            for s in _slotnames(cls):
                try:
                    st[s] = object.__getattribute__(obj, s)
                except AttributeError:
                    pass
            d = getattr(obj, "__dict__", None)
            return (_rebuild, (cls,), (d if d else None, st))
        return NotImplemented


def dumps(obj) -> bytes:
    f = io.BytesIO()
    _Pickler(f, protocol=5).dump(obj)
    return f.getvalue()


def loads(b: bytes):
    return pickle.loads(b)


def pdigest(obj) -> str:
    return hashlib.sha256(dumps(obj)).hexdigest()


# ---------------------------------------------------------------- canonical digest

_ATOMS = (type(None), bool, int, float, complex, str, bytes)


def _qual(o) -> str:
    return f"{getattr(o, '__module__', '?')}.{getattr(o, '__qualname__', getattr(o, '__name__', '?'))}"


_ATOM_TAG = {t: t.__name__ + ":" for t in _ATOMS}
_KIND: dict[type, tuple] = {}  # type -> ("O", tag, slots, has_dict)


def _kind(t):
    k = _KIND.get(t)
    if k is None:
        if t is list or t is tuple:
            k = ("L", t.__name__ + "[")
        elif t is dict:
            k = ("D",)
        elif t in (set, frozenset):
            k = ("S", t.__name__ + "[")
        elif issubclass(t, enum.Enum):
            k = ("E", f"E:{_qual(t)}.")
        elif issubclass(t, types.FunctionType):
            k = ("F",)
        elif issubclass(t, types.MethodType):
            k = ("M",)
        elif issubclass(t, (type, types.BuiltinFunctionType, types.ModuleType, types.BuiltinMethodType)):
            k = ("Q",)
        else:
            slots = _slotnames(t)
            opaque = not slots and "__dict__" not in dir(t) and not (t.__module__ or "").startswith("sqlglot")
            k = ("O", f"O:{_qual(t)}{{", tuple((s, s + "=") for s in slots), opaque)
        _KIND[t] = k
    return k


def canon_digest(o, stale_check: bool = False) -> str:
    """Process-independent digest: atoms by value (not identity), classes/functions by
    qualname, containers and other objects structurally with identity (aliasing) kept.
    A function made at run time also feeds its closure cells and defaults. A cached
    Expression `_hash` (string-hash-seed dependent) counts by presence, plus, with
    stale_check, whether it differs from a fresh recomputation."""
    parts: list[str] = []
    w = parts.append
    memo: dict[int, int] = {}
    keep = []
    hashed = []
    atom_tag = _ATOM_TAG
    kinds = _KIND
    getattr_ = object.__getattribute__

    def feed(x) -> None:
        t = type(x)
        tag = atom_tag.get(t)
        if tag is not None:
            w(tag)
            w(repr(x))
            w("|")
            return
        r = memo.get(id(x))
        if r is not None:
            w(f"@{r}|")
            return
        memo[id(x)] = len(keep)
        keep.append(x)
        k = kinds.get(t) or _kind(t)
        c = k[0]
        if c == "O":
            w(k[1])
            d = getattr(x, "__dict__", None)
            if d is not None:
                feed(dict(d))
            for s, se in k[2]:
                try:
                    v = getattr_(x, s)
                except AttributeError:
                    w(se)
                    w("<unset>;")
                    continue
                if s == "_hash":
                    if v is not None:
                        w("_hash=1;")
                        hashed.append(x)
                    else:
                        w("_hash=0;")
                    continue
                w(se)
                feed(v)
            if k[3] and d is None:
                w(f"R:{x!r}")
            w("}")
        elif c == "L":
            w(k[1])
            w(str(len(x)))
            for v in x:
                feed(v)
            w("]")
        elif c == "D":
            w(f"d[{len(x)}")
            for kk, v in x.items():
                feed(kk)
                feed(v)
            w("]")
        elif c == "E":
            w(k[1])
            w(x.name)
            w("|")
        elif c == "F":
            w(f"F:{_qual(x)}(")
            if x.__closure__:
                for cell in x.__closure__:
                    try:
                        feed(cell.cell_contents)
                    except ValueError:
                        w("<empty>")
            feed(x.__defaults__)
            feed(x.__kwdefaults__)
            w(")")
        elif c == "M":
            w(f"M:{_qual(x.__func__)}(")
            feed(x.__self__)
            w(")")
        elif c == "Q":
            w(f"Q:{_qual(x)}|")
        else:  # "S"
            w(k[1] + ",".join(sorted(canon_digest(v) for v in x)) + "]")

    feed(o)
    if stale_check and hashed:
        nodes = [x for x in keep if hasattr(x, "_hash") and hasattr(x, "args")]
        saved = [(n, n._hash) for n in nodes]
        stale = []
        try:
            for n in nodes:
                n._hash = None
            for i, (n, h) in enumerate(saved):
                if h is not None and hash(n) != h:
                    stale.append(i)
        finally:
            for n, h in saved:
                n._hash = h
        w(f"stale:{stale}")
    return hashlib.sha256("".join(parts).encode("utf-8", "surrogatepass")).hexdigest()


def odigest(obj) -> str:
    """Digest of a rule call's outcome (return value, arguments after the call)."""
    return canon_digest(obj, stale_check=True)


# ---------------------------------------------------------------- global state

_DEPTH = 4
_LEAF = (type(None), bool, int, float, complex, str, bytes, types.FunctionType,
         types.BuiltinFunctionType, types.ModuleType, type, types.MethodType, property,
         staticmethod, classmethod, types.CodeType, logging.Logger, logging.PlaceHolder)


# pickle's per-class slot-name cache (copyreg), filled by pickling itself
_IGNORED = frozenset({"__slotnames__"})


def _namespaces():
    for name, mod in list(sys.modules.items()):
        if mod is None or not (name == "sqlglot" or name.startswith("sqlglot.")):
            continue
        yield ("m", name), mod.__dict__
        for v in list(mod.__dict__.values()):
            if isinstance(v, type) and getattr(v, "__module__", None) == name:
                yield ("c", name, v.__qualname__), v.__dict__


def _is_leaf(v) -> bool:
    return isinstance(v, _LEAF)


def _fp(o, depth, seen):
    t = type(o)
    if _is_leaf(o) or depth == 0:
        return id(o)
    if t is dict or isinstance(o, types.MappingProxyType):
        items = tuple((k if _is_leaf(k) and type(k) in _ATOMS else id(k), _sub(v, depth, seen))
                      for k, v in list(o.items()))
        return (id(o) if t is dict else 0, len(items), hash(items))
    if t in (list, tuple):
        return (id(o), len(o), hash(tuple(_sub(v, depth, seen) for v in o)))
    if t in (set, frozenset):
        return (id(o), len(o), hash(frozenset(x if type(x) in _ATOMS else id(x) for x in o)))
    d = getattr(o, "__dict__", None)
    if d is not None:
        return (id(o), _sub(d, depth, seen))
    return id(o)


def _sub(v, depth, seen):
    if _is_leaf(v):
        return id(v)
    if id(v) in seen:
        return ("@", id(v))
    seen.add(id(v))
    try:
        return _fp(v, depth - 1, seen)
    finally:
        seen.discard(id(v))


def gstate_snapshot() -> dict:
    """{namespace key: {attr: fingerprint}} for every sqlglot module and class."""
    out = {}
    for key, ns in _namespaces():
        out[key] = {k: _sub(v, _DEPTH, {id(ns)}) for k, v in list(ns.items())
                    if k not in _IGNORED}
    return out


def gstate_changes(a: dict, b: dict) -> list[tuple]:
    """(namespace key, attr) pairs that differ; a namespace appearing is ("+", key)."""
    out = []
    for key in sorted(set(a) | set(b), key=repr):
        if key not in a or key not in b:
            out.append(("+", key))
            continue
        x, y = a[key], b[key]
        for attr in sorted(set(x) | set(y)):
            if x.get(attr, ()) != y.get(attr, ()):
                out.append((key, attr))
    return out


def namespace(key: tuple) -> dict:
    mod = sys.modules[key[1]]
    if key[0] == "m":
        return mod.__dict__
    obj = mod
    for part in key[2].split("."):
        obj = getattr(obj, part)
    return obj.__dict__


def cache_digests(changes: list[tuple]) -> dict | None:
    """For changed (namespace, attr) that are dicts: {repr(key, attr): {canon(k): canon(v)}}.
    None when some change is not a dict (no way to compare it across processes)."""
    out = {}
    for change in changes:
        if change[0] == "+":
            return None
        key, attr = change
        value = namespace(key).get(attr)
        if type(value) is not dict:
            return None
        out[repr((key, attr))] = {canon_digest(k): canon_digest(v) for k, v in value.items()}
    return out
