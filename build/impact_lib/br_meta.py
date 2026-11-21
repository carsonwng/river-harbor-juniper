"""Typing metadata tables: per-key import-time state (strat/r2-ledger).

`sqlglot.typing.*` modules build `EXPRESSION_METADATA`, a dict from expression
class to its type annotation spec ({"annotator": fn, "returns": type, ...}).
Every dialect class copies one into its own `EXPRESSION_METADATA`, and the
optimizer's TypeAnnotator reads it at run time with `.get(type(expression))`.
An edit to these tables changes the program only through the entries whose
value differs after import; a test can see that only by looking up one of those
keys (br_cov_plugin records every lookup per test) or by reading a table as a
whole (iteration, copy into a plain dict, comparison: also recorded).

Modes (always PYTHONHASHSEED=0, like br_state.py):

* `snapshot --out F`: import every sqlglot module (pinned) and write, for every
  metadata table, {key name: value fingerprint}.
* `diff --state STATE --meta F --rel REL` (edited text on stdin): import with REL
  replaced and print one JSON line:
  {"import_failed": bool, "foreign": [...state-diff changes outside the tables...],
   "keys": [changed key names], "bad_keys": bool}

A table is every dict object bound to the name EXPRESSION_METADATA in a sqlglot
module or class, and every other module global or class attribute bound to the
same object (HIVE_EXPRESSION_METADATA). Keys are expression classes, named
"module:qualname" exactly as br_cov_plugin names them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import br_state  # noqa: E402

NAME = "EXPRESSION_METADATA"


def key_name(k) -> str | None:
    if isinstance(k, type):
        return f"{getattr(k, '__module__', '?')}:{getattr(k, '__qualname__', '?')}"
    return None


def tables(names: dict[type, str]) -> dict[str, dict]:
    """{"m|module|attr" or "c|ckey|attr": the table} for every binding of a table."""
    mods = sorted((n, m) for n, m in sys.modules.items()
                  if m is not None and (n == "sqlglot" or n.startswith("sqlglot.")))
    objs: dict[int, dict] = {}
    for _n, mod in mods:
        v = vars(mod).get(NAME)
        if isinstance(v, dict):
            objs[id(v)] = v
    for klass in names:
        v = klass.__dict__.get(NAME)
        if isinstance(v, dict):
            objs[id(v)] = v
    out: dict[str, dict] = {}
    for n, mod in mods:
        for attr, v in list(vars(mod).items()):
            if id(v) in objs:
                out[f"m|{n}|{attr}"] = v
    for klass, ckey in names.items():
        for attr, v in list(klass.__dict__.items()):
            if id(v) in objs:
                out[f"c|{ckey}|{attr}"] = v
    return out


def key_state(names: dict[type, str]) -> dict[str, dict[str, int] | None]:
    fo = br_state.Fingerprinter(names)
    out: dict[str, dict[str, int] | None] = {}
    for binding, table in sorted(tables(names).items()):
        entries: dict[str, int] | None = {}
        for k, v in dict.items(table):
            name = key_name(k)
            if name is None or name in entries:
                entries = None  # a key we cannot name: the table is never narrowed
                break
            entries[name] = fo.fp(v)
        out[binding] = entries
    return out


def main() -> int:
    import argparse
    import warnings

    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["snapshot", "diff"])
    parser.add_argument("--sqlglot", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--meta", type=Path)
    parser.add_argument("--rel")
    opts = parser.parse_args()
    if os.environ.get("PYTHONHASHSEED") != "0":
        print("PYTHONHASHSEED must be 0", file=sys.stderr)
        return 2
    root = opts.sqlglot.resolve()
    sys.path.insert(0, str(root))
    if opts.rel:
        text = sys.stdin.read()
        parts = list(Path(opts.rel).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        sys.meta_path.insert(0, br_state._Override(".".join(parts), str(root / opts.rel), text))
    warnings.simplefilter("ignore")
    failed = br_state.import_all(root)
    names = br_state.canonical_classes()
    if opts.mode == "snapshot":
        opts.out.write_text(json.dumps({"failed": failed, "tables": key_state(names)}, sort_keys=True,
                                       separators=(",", ":")), "utf-8")
        return 0
    old = json.loads(opts.state.read_text("utf-8"))
    pristine = json.loads(opts.meta.read_text("utf-8"))
    if old.get("failed") != failed or pristine.get("failed") != failed:
        print(json.dumps({"import_failed": True, "error": f"import failures differ: {failed}"}))
        return 0
    state = br_state.snapshot(names, unordered=set(old.get("unordered", ())), old=old)
    now = key_state(names)
    bindings = set(pristine["tables"]) | set(now)
    foreign = []
    for ch in br_state.diff(old, state):
        if ch[0] == "class" and f"c|{ch[1]}|{ch[2]}" in bindings:
            continue
        if ch[0] == "module" and f"m|{ch[1]}|{ch[2]}" in bindings:
            continue
        foreign.append(ch)
    keys: set[str] = set()
    bad = False
    for binding in sorted(bindings):
        a, b = pristine["tables"].get(binding), now.get(binding)
        if a is None or b is None:
            bad = True  # a table appeared, vanished, or has a key we cannot name
            continue
        for k in set(a) | set(b):
            if a.get(k) != b.get(k):
                keys.add(k)
    print(json.dumps({"import_failed": False, "foreign": foreign, "keys": sorted(keys),
                      "bad_keys": bad}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
