"""Import-time state of sqlglot: a deterministic fingerprint of every class
attribute (and every entry of dict-valued ones) and every module global.

Used three ways (always with PYTHONHASHSEED=0, so str hashes are stable):

* build, `snapshot`: import every sqlglot module of the pinned checkout and write
  the fingerprints (pristine state) as JSON.
* run, `diff`: the same, but one module's source is replaced by an edited text
  read from stdin; print what differs from the stored pristine state. This is
  how the selector learns what an edit to import-time code (class bodies,
  module-level tables, metaclasses, __init_subclass__) actually changes, after
  every propagation that happens at import (merged tables in subclasses, tries,
  derived sets such as required_args, reverse maps, ...).
* the attribute-read profile (br_attr_plugin) uses `canonical_classes` and
  `fp` so its records name classes and dict keys exactly as the diff does.

Fingerprints ignore source positions (line numbers), so moving code is not a
change; everything else about a value (code, constants, closures, defaults,
nested containers, instance state) is.
"""

from __future__ import annotations

import gc
import importlib
import importlib.abc
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

SKIP_ATTRS = {"__dict__", "__weakref__", "__firstlineno__", "__static_attributes__"}


def module_names(root: Path) -> list[str]:
    names = []
    for path in sorted((root / "sqlglot").rglob("*.py")):
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        name = ".".join(parts)
        if name != "sqlglot.__main__":
            names.append(name)
    return names


def import_all(root: Path) -> list[str]:
    failed = []
    for name in module_names(root):
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001
            failed.append(name)
    return failed


def _is_sqlglot(obj) -> bool:
    mod = getattr(obj, "__module__", None)
    return isinstance(mod, str) and (mod == "sqlglot" or mod.startswith("sqlglot."))


def canonical_classes() -> dict[type, str]:
    """Every live class defined by sqlglot code -> a deterministic unique name.

    "module:qualname" when that is unique, else "module:qualname@path" with the
    first attribute path (modules and attributes in sorted order) reaching it."""
    mods = sorted((n, m) for n, m in sys.modules.items()
                  if m is not None and (n == "sqlglot" or n.startswith("sqlglot.")))
    path_of: dict[int, str] = {}
    order: list[type] = []
    stack: list[tuple[str, object, int]] = []
    for name, mod in mods:
        for attr in sorted(vars(mod), reverse=True):
            stack.append((f"{name}:{attr}", vars(mod)[attr], 0))
        while stack:
            path, obj, depth = stack.pop()
            if isinstance(obj, type):
                if id(obj) in path_of or not _is_sqlglot(obj):
                    continue
                path_of[id(obj)] = path
                order.append(obj)
                d = obj.__dict__
                for attr in sorted(d, reverse=True):
                    if attr not in SKIP_ATTRS and attr != "__mro__":
                        stack.append((f"{path}.{attr}", d[attr], 0))
            elif depth == 0 and type(obj) is dict:
                # registries ({name: class}); deeper containers (tries, tables of
                # tables) are not walked: anything missed is found by gc below
                items = list(obj.items())
                for i in range(len(items) - 1, -1, -1):
                    k, v = items[i]
                    if isinstance(v, type) or isinstance(k, type):
                        label = repr(k) if type(k) in (str, int) else str(i)
                        stack.append((f"{path}[{label}]", v, 1))
                        stack.append((f"{path}[{label}k]", k, 1))
            elif depth == 0 and type(obj) in (list, tuple):
                for i in range(len(obj) - 1, -1, -1):
                    if isinstance(obj[i], type):
                        stack.append((f"{path}[{i}]", obj[i], 1))
    # classes nothing reachable names (rare): by gc, ordered by key
    gc.collect()
    extra = [o for o in gc.get_objects() if isinstance(o, type) and _is_sqlglot(o)
             and id(o) not in path_of]
    count: dict[str, int] = {}
    for k in order + extra:
        key = f"{k.__module__}:{k.__qualname__}"
        count[key] = count.get(key, 0) + 1
    # a class whose module:qualname is not unique (the type("Tokenizer", ...) classes
    # _Dialect makes) is named after the uniquely named class whose own attribute
    # holds it, when there is one: that does not depend on which modules are loaded
    owner: dict[int, str] = {}
    for k in order + extra:
        key = f"{k.__module__}:{k.__qualname__}"
        if count[key] != 1:
            continue
        for attr, v in list(k.__dict__.items()):
            if isinstance(v, type) and _is_sqlglot(v):
                vkey = f"{v.__module__}:{v.__qualname__}"
                if count.get(vkey, 1) > 1:
                    cand = f"{vkey}@{key}.{attr}"
                    if id(v) not in owner or cand < owner[id(v)]:
                        owner[id(v)] = cand
    out: dict[type, str] = {}
    for k in order:
        key = f"{k.__module__}:{k.__qualname__}"
        out[k] = key if count[key] == 1 else owner.get(id(k)) or f"{key}@{path_of[id(k)]}"
    for k in extra:
        key = f"{k.__module__}:{k.__qualname__}"
        out[k] = key if count[key] == 1 else owner.get(id(k)) or f"{key}@?"
    return out


def portable_key(k) -> str:
    """A name for a dict key that two processes agree on, and that equal keys share
    (True == 1 == 1.0, IntEnum members == their int, str enums == their str), so
    a lookup recorded in one process matches a changed key found in another.
    "?" when there is none (the key then matches nothing and is never trusted)."""
    import enum

    if k is None:
        return "N"
    if isinstance(k, bool) or isinstance(k, int):
        return f"i:{int(k)}"
    if isinstance(k, float):
        if k == k and k not in (float("inf"), float("-inf")) and k == int(k):
            return f"i:{int(k)}"
        return f"f:{k!r}"
    if isinstance(k, str):
        return "s:" + str.__str__(k)
    if isinstance(k, enum.Enum):
        t = type(k)
        if t.__eq__ is not object.__eq__ and t.__eq__ is not enum.Enum.__eq__:
            return "?"
        return f"e:{t.__module__}:{t.__qualname__}.{k.name}"
    if isinstance(k, type):
        if type(k) is not type:  # a metaclass may define __eq__ (_Dialect does)
            return "?"
        return f"c:{k.__module__}:{k.__qualname__}"
    if type(k) is tuple:
        parts = [portable_key(x) for x in k]
        return "?" if "?" in parts else "t:(" + "".join(parts) + ")"
    return "?"


class Fingerprinter:
    def __init__(self, names: dict[type, str], ordered: bool = True) -> None:
        self.names = names
        self.ordered = ordered
        self.memo: dict[int, int] = {}
        self.keep: list = []  # keep fingerprinted objects alive (ids stay unique)

    def fp(self, obj) -> int:
        t = type(obj)
        if t is str:
            return hash(("str", obj))
        if obj is None:
            return hash(("NoneType", None))
        if t is int or t is bool or t is float:
            return hash((t.__name__, repr(obj)))
        oid = id(obj)
        got = self.memo.get(oid)
        if got is not None:
            return got
        if oid in self.memo:  # in progress: a cycle
            return hash(("cycle", type(obj).__name__))
        self.memo[oid] = None  # type: ignore[assignment]
        self.keep.append(obj)
        try:
            value = self._fp(obj)
        except Exception as exc:  # noqa: BLE001
            value = hash(("error", type(obj).__qualname__, type(exc).__name__))
        self.memo[oid] = value
        return value

    def _fp(self, obj) -> int:
        t = type(obj)
        if obj is None or t in (str, bytes):
            return hash((t.__name__, obj))
        if t in (bool, int, float, complex):  # hash(-1) == hash(-2): use the text
            return hash((t.__name__, repr(obj)))
        if isinstance(obj, type):
            name = self.names.get(obj)
            if name is None:
                name = f"{getattr(obj, '__module__', '?')}:{getattr(obj, '__qualname__', '?')}"
            return hash(("class", name))
        if t is types.ModuleType:
            return hash(("module", obj.__name__))
        fp = self.fp
        import enum

        if isinstance(obj, enum.Enum):
            return hash(("enum", fp(t), obj.name, fp(obj._value_)))
        if t is tuple or t is list:
            items = tuple(fp(x) for x in obj)
            return hash((t.__name__, items if self.ordered else tuple(sorted(items))))
        if t is set or t is frozenset:
            return hash((t.__name__, tuple(sorted(fp(x) for x in obj))))
        if isinstance(obj, dict) and t.__module__ in ("builtins", "collections"):
            items = tuple((fp(k), fp(v)) for k, v in dict.items(obj))
            return hash(("dict", t.__name__, items if self.ordered else tuple(sorted(items))))
        if t is types.FunctionType:
            cells = tuple(fp(c.cell_contents) if _cell_full(c) else 0 for c in (obj.__closure__ or ()))
            return hash(("fn", obj.__qualname__, obj.__module__, fp(obj.__code__),
                         fp(obj.__defaults__), fp(obj.__kwdefaults__), cells,
                         fp(dict(obj.__dict__)), fp(getattr(obj, "__annotations__", None))))
        if t is types.CodeType:
            return hash(("code", obj.co_name, obj.co_qualname, obj.co_code,
                         tuple(fp(c) for c in obj.co_consts), obj.co_names, obj.co_varnames,
                         obj.co_freevars, obj.co_cellvars, obj.co_flags, obj.co_argcount,
                         obj.co_kwonlyargcount, obj.co_posonlyargcount))
        if t is types.MethodType:
            return hash(("method", fp(obj.__func__), fp(obj.__self__)))
        if t in (types.BuiltinFunctionType, types.BuiltinMethodType, types.WrapperDescriptorType,
                 types.MethodDescriptorType, types.MethodWrapperType, types.ClassMethodDescriptorType,
                 types.GetSetDescriptorType, types.MemberDescriptorType):
            owner = getattr(obj, "__self__", None)
            own = fp(owner) if isinstance(owner, type) or t is types.MethodWrapperType else 0
            return hash(("builtin", t.__name__, getattr(obj, "__qualname__", getattr(obj, "__name__", "?")),
                         str(getattr(obj, "__module__", "")), own))
        if t is classmethod or t is staticmethod:
            return hash((t.__name__, fp(obj.__func__)))
        if t is property:
            return hash(("property", fp(obj.fget), fp(obj.fset), fp(obj.fdel), fp(obj.__doc__)))
        import functools
        import re

        if t is functools.partial:
            return hash(("partial", fp(obj.func), fp(obj.args), fp(obj.keywords)))
        if t is re.Pattern:
            return hash(("re", obj.pattern, obj.flags))
        if t is functools.cached_property:
            return hash(("cached_property", fp(obj.func), obj.attrname))
        if isinstance(obj, functools._lru_cache_wrapper):  # type: ignore[attr-defined]
            return hash(("lru", fp(obj.__wrapped__), obj.cache_info().currsize))
        if t is types.SimpleNamespace:
            return hash(("ns", fp(dict(vars(obj)))))
        if isinstance(obj, (types.GeneratorType, types.FrameType)):
            return hash(("opaque", t.__name__))
        if _is_sqlglot(t) and isinstance(getattr(obj, "args", None), dict) and hasattr(obj, "parent"):
            # an expression node: its type, arguments and comments. Its position in
            # some tree (parent, arg_key, index) and caches (_hash, _type, _meta)
            # change whenever a shared node is used, which is not a value change.
            return hash(("expr", fp(t), fp(dict(obj.args)), fp(getattr(obj, "comments", None))))
        # any other object: its type plus its state
        parts = [fp(t)]
        d = getattr(obj, "__dict__", None)
        if isinstance(d, dict):
            parts.append(fp(dict(d)))
        for klass in t.__mro__:
            for slot in klass.__dict__.get("__slots__", ()) or ():
                if isinstance(slot, str) and slot not in ("__dict__", "__weakref__"):
                    try:
                        parts.append(hash((slot, fp(getattr(obj, slot)))))
                    except AttributeError:
                        parts.append(hash((slot, "unset")))
        if isinstance(obj, dict):
            parts.append(hash(tuple((fp(k), fp(v)) for k, v in dict.items(obj))))
        elif isinstance(obj, (list, tuple)):
            parts.append(hash(tuple(fp(x) for x in obj)))
        elif isinstance(obj, (set, frozenset)):
            parts.append(hash(tuple(sorted(fp(x) for x in obj))))
        elif isinstance(obj, (str, bytes, int, float)):
            parts.append(hash((t.__mro__[1].__name__, repr(obj))))
        return hash(("obj", tuple(parts)))


def _cell_full(cell) -> bool:
    try:
        cell.cell_contents
        return True
    except ValueError:
        return False


def snapshot(names: dict[type, str] | None = None, unordered: set[str] = frozenset(),
             old: dict | None = None) -> dict:
    """{"classes": {ckey: {attr: [fp, {keyfp: valfp} | None]}}, "modules": {mod: {name: fp}}}

    Entries named in `unordered` ("c|ckey|attr", "m|module|attr") are fingerprinted
    without container order: their order differs between processes on the pinned
    code itself (built by iterating sets of classes), so order is not a signal."""
    names = names if names is not None else canonical_classes()
    fo = Fingerprinter(names)
    fu = Fingerprinter(names, ordered=False)
    classes: dict[str, dict] = {}
    for klass, ckey in sorted(names.items(), key=lambda kv: kv[1]):
        attrs = {}
        for attr, value in list(klass.__dict__.items()):
            if attr in SKIP_ATTRS:
                continue
            f = fu if f"c|{ckey}|{attr}" in unordered else fo
            keys = None
            whole = f.fp(value)
            prev = old["classes"].get(ckey, {}).get(attr) if old is not None else None
            if type(value) is dict and (prev is None or prev[0] != whole):
                keys = {}
                knames = {}
                for k, v in value.items():
                    kf = str(fo.fp(k))
                    keys[kf] = f.fp(v)
                    knames[kf] = portable_key(k)
                attrs[attr] = [whole, keys, knames]
            else:
                attrs[attr] = [whole, keys]
        classes[ckey] = attrs
    modules: dict[str, dict] = {}
    for name, mod in sorted(sys.modules.items()):
        if mod is None or not (name == "sqlglot" or name.startswith("sqlglot.")):
            continue
        modules[name] = {attr: (fu if f"m|{name}|{attr}" in unordered else fo).fp(v)
                         for attr, v in list(vars(mod).items())
                         if attr not in ("__builtins__", "__cached__", "__spec__", "__loader__", "__file__")}
    import enum

    # classes whose attributes Python's own machinery reads (enum, namedtuple,
    # abc, other metaclasses): reads the profile cannot see
    opaque = sorted(ckey for k, ckey in names.items()
                    if issubclass(k, enum.Enum) or type(k).__name__ not in ("type", "_Dialect")
                    or "_fields" in k.__dict__)
    return {"classes": classes, "modules": modules, "unordered": sorted(unordered), "opaque": opaque}


def diff(old: dict, new: dict) -> list:
    """Changes between two snapshots: ["class", ckey, attr, keyfp|None, kind] entries
    (kind: "value" for one dict entry's value, "attr" otherwise) and
    ["module", name, attr] / ["classset", ckey] / ["moduleset", name] entries."""
    out: list = []
    oc, nc = old["classes"], new["classes"]
    for ckey in sorted(set(oc) | set(nc)):
        a, b = oc.get(ckey), nc.get(ckey)
        if a is None or b is None:
            out.append(["classset", ckey])
            continue
        for attr in sorted(set(a) | set(b)):
            x, y = a.get(attr), b.get(attr)
            if x is None or y is None:
                out.append(["class", ckey, attr, None, "added" if x is None else "removed"])
                continue
            if x[0] == y[0]:
                continue
            xk, yk = x[1], y[1]
            if xk is not None and yk is not None and list(xk) == list(yk):
                changed = [k for k in xk if xk[k] != yk[k]]
                if changed:
                    for k in changed:
                        out.append(["class", ckey, attr, k, "value"])
                    continue
            out.append(["class", ckey, attr, None, "attr"])
    om, nm = old["modules"], new["modules"]
    for name in sorted(set(om) | set(nm)):
        a, b = om.get(name), nm.get(name)
        if a is None or b is None:
            out.append(["moduleset", name])
            continue
        for attr in sorted(set(a) | set(b)):
            if a.get(attr) != b.get(attr):
                out.append(["module", name, attr])
    return out


def key_details(old: dict, new: dict) -> list:
    """Key-level detail of changed dict-valued class attributes:
    [ckey, attr, kind, key name] with kind "value" (a common key's value changed),
    "add", "del", or "order" (same keys, other order; key name None)."""
    out: list = []
    oc, nc = old["classes"], new["classes"]
    for ckey in sorted(set(oc) & set(nc)):
        a, b = oc[ckey], nc[ckey]
        for attr in sorted(set(a) & set(b)):
            x, y = a[attr], b[attr]
            if x[0] == y[0] or x[1] is None or y[1] is None or len(x) < 3 or len(y) < 3:
                continue
            xk, yk, xn, yn = x[1], y[1], x[2], y[2]
            for k in xk:
                if k not in yk:
                    out.append([ckey, attr, "del", xn.get(k, "?")])
                elif xk[k] != yk[k]:
                    out.append([ckey, attr, "value", xn.get(k, "?")])
            for k in yk:
                if k not in xk:
                    out.append([ckey, attr, "add", yn.get(k, "?")])
            common = [k for k in xk if k in yk]
            if common != [k for k in yk if k in xk]:
                out.append([ckey, attr, "order", None])
    return out


def tracked_names(names: dict[type, str]) -> list[str]:
    """Class attribute names bound (somewhere) to something other than a method
    defined under that name by a def: the names whose reads the profile records."""
    out = set()
    for klass in names:
        for attr, value in list(klass.__dict__.items()):
            if attr.startswith("__") and attr.endswith("__"):
                continue
            if isinstance(value, (types.MemberDescriptorType, types.GetSetDescriptorType)):
                continue  # __slots__ members: changed only through __slots__
            func = value
            if isinstance(value, (classmethod, staticmethod)):
                func = value.__func__
            elif isinstance(value, property):
                func = value.fget
            if (isinstance(func, types.FunctionType) and func.__name__ == attr
                    and func.__qualname__ == f"{klass.__qualname__}.{attr}"):
                continue
            out.add(attr)
    return sorted(out)


def change_id(change: list) -> str:
    if change[0] == "class":
        return f"c|{change[1]}|{change[2]}"
    if change[0] == "module":
        return f"m|{change[1]}|{change[2]}"
    return f"{change[0]}|{change[1]}"


RELS = ["sqlglot/dialects/tsql.py", "sqlglot/expressions/core.py", "sqlglot/parser.py",
        "sqlglot/generator.py", "sqlglot/tokens.py"]


def build_state(root: Path, out: Path, runs: int = 6) -> dict:
    """Pristine snapshot, with entries whose order is not reproducible across
    processes fingerprinted unordered, and entries that still are not
    reproducible listed as volatile (a change there is never trusted)."""
    import subprocess
    import tempfile

    env = dict(os.environ, PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")

    def one(unordered: set[str], rel: str | None = None) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            uf = Path(tmp) / "unordered.json"
            uf.write_text(json.dumps(sorted(unordered)), "utf-8")
            target = Path(tmp) / "state.json"
            extra = ["--rel", rel] if rel else []
            text = (root / rel).read_text("utf-8") if rel else ""
            subprocess.run([sys.executable, "-W", "ignore", __file__, "snapshot", "--sqlglot", str(root),
                            "--out", str(target), "--unordered", str(uf)] + extra, env=env, check=True,
                           input=text, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return json.loads(target.read_text("utf-8"))

    # the run-time diff loads one module from text: vary that too
    rels = [None, None] + [r for r in RELS if (root / r).exists()][: max(0, runs - 2)]
    first = [one(set(), r) for r in rels]
    unstable = {change_id(c) for s in first[1:] for c in diff(first[0], s)}
    second = [one(unstable, r) for r in rels]
    volatile = {change_id(c) for s in second[1:] for c in diff(second[0], s)}
    state = second[0]
    state["volatile"] = sorted(volatile)
    out.write_text(json.dumps(state, separators=(",", ":")), "utf-8")
    return state


class _Override(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Load one module from an edited source text instead of its file."""

    def __init__(self, name: str, path: str, text: str) -> None:
        self.name, self.path, self.text = name, path, text

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.name:
            return None
        is_pkg = os.path.basename(self.path) == "__init__.py"
        return importlib.util.spec_from_file_location(
            fullname, self.path, loader=self,
            submodule_search_locations=[os.path.dirname(self.path)] if is_pkg else None)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        code = compile(self.text, self.path, "exec", dont_inherit=True)
        exec(code, module.__dict__)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["snapshot", "diff", "tracked", "serve"])
    parser.add_argument("--sqlglot", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--state", type=Path, help="pristine snapshot (diff mode)")
    parser.add_argument("--rel", help="edited file, relative to --sqlglot (diff mode)")
    parser.add_argument("--unordered", type=Path, help="JSON list of entries to fingerprint unordered")
    opts = parser.parse_args()
    if os.environ.get("PYTHONHASHSEED") != "0":
        print("PYTHONHASHSEED must be 0", file=sys.stderr)
        return 2
    root = opts.sqlglot.resolve()
    if opts.mode == "serve":
        serve(root, opts.state)
        return 0
    sys.path.insert(0, str(root))
    if opts.rel:
        text = sys.stdin.read()
        # "--rel -": stdin is a JSON object {rel: edited text} (every edited file of a commit)
        overrides = json.loads(text) if opts.rel == "-" else {opts.rel: text}
        for rel, body in sorted(overrides.items()):
            parts = list(Path(rel).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            sys.meta_path.insert(0, _Override(".".join(parts), str(root / rel), body))
    import warnings

    warnings.simplefilter("ignore")
    if opts.mode == "tracked":
        import_all(root)
        opts.out.write_text(json.dumps(tracked_names(canonical_classes())), "utf-8")
        return 0
    if opts.mode == "snapshot":
        unordered = set(json.loads(opts.unordered.read_text("utf-8"))) if opts.unordered else set()
        failed = import_all(root)
        state = snapshot(unordered=unordered)
        state["failed"] = failed
        opts.out.write_text(json.dumps(state, separators=(",", ":")), "utf-8")
        return 0
    old = json.loads(opts.state.read_text("utf-8"))
    print(json.dumps(run_diff(root, old)))
    return 0


def run_diff(root: Path, old: dict) -> dict:
    failed = import_all(root)
    state = snapshot(unordered=set(old.get("unordered", ())), old=old)
    if old.get("failed") != failed:
        return {"error": f"import failures differ: {failed}", "import_failed": True}
    volatile = set(old.get("volatile", ()))
    changes = diff(old, state)
    return {"changes": changes,
            "volatile": [c for c in changes if change_id(c) in volatile],
            "keys": key_details(old, state)}


def _module_of(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def serve(root: Path, state_path: Path) -> None:
    """Diff server (POSIX): the setup a fresh `diff` child does before its first
    import (sys.path, warnings off, pristine state loaded), then `import sqlglot`
    once, recording every module the import looks up. Each request line
    {"overrides": {rel: text}} is answered from a fork that installs the overrides
    and runs the rest of the diff exactly as a fresh child does after its own
    `import sqlglot` (import_all imports "sqlglot" first, then the rest in sorted
    order). If the pristine import looked up or loaded an edited module, the fork
    answers {"conflict": true} and the caller runs a fresh child."""
    import importlib
    import importlib.abc
    import warnings

    sys.path.insert(0, str(root))
    warnings.simplefilter("ignore")
    old = json.loads(state_path.read_text("utf-8"))
    asked: set = set()

    class Recorder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            asked.add(fullname)
            return None

    rec = Recorder()
    sys.meta_path.insert(0, rec)
    out = sys.stdout.fileno()
    sys.stdout = sys.stderr

    def reply(value: dict) -> None:
        data = (json.dumps(value) + "\n").encode("utf-8")
        while data:
            data = data[os.write(out, data):]

    try:
        importlib.import_module("sqlglot")
    except BaseException:  # noqa: BLE001
        reply({"ready": False})
        return
    sys.meta_path.remove(rec)
    seen = frozenset(asked) | frozenset(sys.modules)
    reply({"ready": True})
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        if not line.strip():
            continue
        req = json.loads(line)
        r, w = os.pipe()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(r)
                overrides = req["overrides"]
                if any(_module_of(rel) in seen for rel in overrides):
                    value = {"conflict": True}
                else:
                    for rel, body in sorted(overrides.items()):
                        sys.meta_path.insert(0, _Override(_module_of(rel), str(root / rel), body))
                    try:
                        value = run_diff(root, old)
                    except BaseException as exc:  # noqa: BLE001
                        value = {"error": f"server child: {exc!r}"}
                data = json.dumps(value).encode("utf-8")
                while data:
                    data = data[os.write(w, data):]
                sys.stderr.flush()
            finally:
                os._exit(0)
        os.close(w)
        chunks = []
        while True:
            chunk = os.read(r, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
        os.close(r)
        _, status = os.waitpid(pid, 0)
        data = b"".join(chunks)
        if status != 0 or not data:
            reply({"error": f"forked child status {status}"})
        else:
            reply(json.loads(data.decode("utf-8")))


if __name__ == "__main__":
    sys.exit(main())
