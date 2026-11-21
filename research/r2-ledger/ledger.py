"""Cost ledger: run the selector's pipeline with per-rule tags (in the image).

usage: python ledger.py DATA_DIR OUT_JSON
Mirrors selector.choose + impact.select exactly, but records for every selected test
which (file, rule) put it there. Answers are read only by the analysis, never here.
"""
import ast
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "/app")
from blast import context, impact, pack, patch, triage  # noqa: E402

I = impact
STATE = {"cur": None, "nested": None, "namekind": None}


def loc_of(tree, line):
    chain = I._chain(tree, line)
    if not chain:
        return "module-blank"
    fi = I._func_index(chain)
    if fi is not None:
        first = I._first_body_line(chain[fi])
        if first is not None and line >= first:
            if len(chain) - 1 > fi and I._is_docstring(chain[fi + 1], chain[fi]) and len(chain) == fi + 2:
                return "fn-docstring"
            return "fn-body"
        return "fn-header"
    stmt = chain[-1]
    if isinstance(stmt, ast.ClassDef):
        first = I._first_body_line(stmt)
        if first is None or line < first:
            return "class-header"
        return "class-blank"
    parent = chain[-2] if len(chain) > 1 else tree
    scope = "class" if any(isinstance(n, ast.ClassDef) for n in chain[:-1]) else "module"
    if I._is_docstring(stmt, parent):
        return scope + "-docstring"
    return f"{scope}-stmt:{type(stmt).__name__}"


_orig_import_bits = I.FileInfo.import_bits


def import_bits(self, lo, hi):
    r = _orig_import_bits(self, lo, hi)
    if STATE["nested"] is not None and r:
        STATE["nested"].append(r)
    return r


I.FileInfo.import_bits = import_bits

_orig_name_bits = I._name_bits


def name_bits(info, names, base):
    r = _orig_name_bits(info, names, base)
    if names is None:
        STATE["namekind"] = "users(no-names)"
    elif r == base:
        STATE["namekind"] = "users(name-read-at-import)"
    else:
        STATE["namekind"] = "names"
    return r


I._name_bits = name_bits


def wrap_line(orig, side):
    def f(info, tree, line, *a):
        STATE["nested"] = []
        STATE["namekind"] = None
        r = orig(info, tree, line, *a)
        nested = 0
        for b in STATE["nested"]:
            nested |= b
        STATE["nested"] = None
        loc = loc_of(tree, line)
        if STATE["namekind"]:
            loc += ":" + STATE["namekind"]
        cur = STATE["cur"]
        if nested:
            tag = "import-fn:core" if nested == info.maps.ALL else "import-fn:module"
            cur[tag] = cur.get(tag, 0) | (r & nested)
        rest = r & ~nested
        if rest:
            cur[loc] = cur.get(loc, 0) | rest
        return r
    return f


I._old_line_bits = wrap_line(I._old_line_bits, "old")
I._new_line_bits = wrap_line(I._new_line_bits, "new")

_orig_lambda = I._lambda_only_bits


def lambda_only(*a):
    r = _orig_lambda(*a)
    if r is not None:
        STATE["cur"]["lambda-only"] = r
    return r


I._lambda_only_bits = lambda_only
_orig_expr = I._expr_state_bits


def expr_state(*a):
    r = _orig_expr(*a)
    if r is not None:
        STATE["cur"].clear()
        STATE["cur"]["exprattr" + (":import-failed" if r == a[0].ALL else "")] = r
    return r


I._expr_state_bits = expr_state
_orig_relprof = I._relprof_refine


def relprof(*a, **k):
    r = _orig_relprof(*a, **k)
    if r is not None:
        STATE["cur"]["relprof-refined"] = r
    return r


I._relprof_refine = relprof


def tagged_select(changes, ctx, tags):
    """Copy of impact.select with tags. tags: list of [file, rule, bits-or-ALL]."""
    if not changes:
        return set()
    maps = I._maps(ctx)
    if maps is None:
        tags.append(["*", "no-maps", "ALL"])
        return ctx.all_tests
    bits = 0
    maps.pruned = False
    maps.import_sources = {}
    local = []
    for change in changes:
        STATE["cur"] = {}
        try:
            b = I._change_bits(change, maps)
        except I.Unsafe as exc:
            tags.append([change.path, "unsafe:" + re.sub(r"\d+", "N", str(exc)), "ALL"])
            return ctx.all_tests
        except Exception as exc:  # noqa: BLE001
            tags.append([change.path, "error:" + type(exc).__name__, "ALL"])
            return ctx.all_tests
        cur = STATE["cur"]
        covered = 0
        for rule, rb in cur.items():
            rb &= b
            if rb:
                local.append([change.path, rule, rb])
                covered |= rb
        if b & ~covered:
            local.append([change.path, "unattributed", b & ~covered])
        if not b:
            local.append([change.path, "no-bits", 0])
        bits |= b
        if bits == maps.ALL:
            for f, r, rb in local:
                tags.append([f, r, "ALL" if rb == maps.ALL else sorted(maps.to_tests(rb) - maps.always)])
            tags.append(["*", "bits==ALL", "ALL"])
            return ctx.all_tests
    imp = None
    if maps.import_sources:
        t0 = time.time()
        ok = I._imports_cleanly(maps)
        imp = [sorted(maps.import_sources), ok, round(time.time() - t0, 3)]
        tags.append(["*", "import-check:" + ("ok" if ok else "FAIL"), []])
        if not ok:
            tags.append(["*", "import-check-fail", "ALL"])
            return ctx.all_tests
    for f, r, rb in local:
        tags.append([f, r, sorted(maps.to_tests(rb) - maps.always) if rb else []])
    if not bits and maps.pruned and maps.always:
        tags.append(["*", "floor(pruned-empty)", sorted(maps.always)])
        return set(maps.always)
    if bits:
        tags.append(["*", "floor", sorted(maps.always)])
    return maps.to_tests(bits) if bits else set()


def reason_cat(reason):
    r = re.sub(r"tests/[\w/.\-]+", "<path>", reason)
    r = re.sub(r"Test\w+(\.\w+)?", "<fn>", r)
    return r[:80]


def main():
    data = Path(sys.argv[1])
    ctx = context.load(data)
    out = {}
    for p in sorted((data / "commits").glob("*.patch")):
        commit = p.stem
        t0 = time.time()
        tags = []
        files = []
        try:
            parsed = patch.parse(commit, p.read_text("utf-8", "replace"))
            selected = set()
            deferred = []
            for change in parsed.files:
                d = triage.classify(change, ctx)
                files.append([change.path, d.kind, d.reason[:200]])
                if d.kind == "tests":
                    selected |= d.tests
                    tags.append([change.path, "triage:" + reason_cat(d.reason), sorted(d.tests)])
                elif d.kind == "defer":
                    deferred.append(change)
            selected |= tagged_select(deferred, ctx, tags)
        except Exception as exc:  # noqa: BLE001
            selected = ctx.all_tests
            tags.append(["*", "selector-exception:" + type(exc).__name__, "ALL"])
        workers = pack.split(selected, ctx)
        out[commit] = {"files": files, "selected": sorted(selected), "tags": tags,
                       "workers": workers, "secs": round(time.time() - t0, 3)}
        print(commit, len(selected), round(time.time() - t0, 2), file=sys.stderr, flush=True)
    Path(sys.argv[2]).write_text(json.dumps(out))


if __name__ == "__main__":
    main()
