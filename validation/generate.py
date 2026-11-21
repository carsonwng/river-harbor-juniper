"""Generate synthetic single-commit patches against the pinned sqlglot.

    python validation/generate.py --sqlglot DIR --out SETDIR -n 60 --seed 1

Writes SETDIR/pending/<id>.patch and SETDIR/pending/<id>.json (the kind and a
human-readable description of the edit). `label.py` runs the suite on each
pending patch and moves it into SETDIR/commits/ with its answer.

The mix follows the sample's kinds:
  code     one small source edit in sqlglot/ (operator flip, constant, string,
           dropped dict/collection entry, removed `not`, is <-> ==, ...)
  fixture  one edited line in tests/fixtures/**/*.sql
  comment  an inserted comment line, a trailing comment, or a docstring edit
  multi    two or three files, each edited by one of the above

Edits are picked from the pinned tree only. Nothing here knows which tests a
patch breaks; that comes from actually running pytest (label.py).
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import random
import string
import subprocess
import sys
import tokenize
from pathlib import Path

KIND_WEIGHTS = {"code": 0.56, "fixture": 0.27, "comment": 0.10, "multi": 0.07}

COMMENT_PHRASES = [
    "Cheap check first", "Keep in sync with the generator", "Kept for backwards compatibility",
    "Intentionally not cached", "Mirrors the behavior of the reference implementation",
    "TODO: revisit this", "NOTE: order matters here", "See the dialect docs",
    "Fast path", "Handled by the parser", "This is intentional", "Normalize early",
    "Edge case: empty input", "FIXME: quadratic in the worst case",
]
DOC_WORDS = ["the", "a", "an", "this", "is", "of", "to", "and", "or", "for", "if"]
FIXTURE_TOKENS = ["NULL", "x", "42", "1", "AS", ",", "(", "a"]


# --------------------------------------------------------------------------- helpers

def git(repo: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True, encoding="utf-8").stdout


def read_lines(path: Path) -> list[str]:
    return path.read_text("utf-8").splitlines(keepends=True)


def compiles(src: str) -> bool:
    try:
        compile(src, "<mutant>", "exec", dont_inherit=True)
        return True
    except (SyntaxError, ValueError):
        return False


def docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                lines.update(range(body[0].lineno, body[0].end_lineno + 1))
    return lines


def docstrings(tree: ast.AST) -> list[ast.Constant]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.append(body[0].value)
    return out


def replace_span(lines: list[str], row: int, col: int, end_col: int, new: str) -> list[str]:
    """Replace lines[row-1][col:end_col] (1-based row, tokenize columns) with new."""
    out = list(lines)
    line = out[row - 1]
    out[row - 1] = line[:col] + new + line[end_col:]
    return out


# --------------------------------------------------------------------------- code edits

SWAP_OPS = {"==": "!=", "!=": "==", "<": ">=", ">": "<=", "<=": ">", ">=": "<",
            "+": "-", "-": "+", "*": "+", "//": "*"}
SWAP_NAMES = {"and": "or", "or": "and", "True": "False", "False": "True"}


CODE_CATEGORY_WEIGHTS = {"boolop": 0.18, "compare": 0.24, "not": 0.12, "number": 0.12,
                         "string": 0.12, "drop": 0.12, "bool": 0.05, "arith": 0.05}


def _category(desc: str) -> str:
    if desc.startswith(("and ", "or ")):
        return "boolop"
    if desc.startswith(("True", "False")):
        return "bool"
    if desc.startswith(("drop not", "not in")):
        return "not"
    if desc.startswith("string"):
        return "string"
    if desc[0].isdigit():
        return "number"
    if desc.split()[0] in ("+", "-", "*", "//"):
        return "arith"
    return "compare"


def code_sites(path: Path) -> list[tuple[str, str, callable]]:
    """Every candidate single-site edit in a Python source file, as (category, description, apply)."""
    src = path.read_text("utf-8")
    lines = src.splitlines(keepends=True)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    doc = docstring_lines(tree)
    toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline)]
    sites: list[tuple[str, callable]] = []

    def add(desc, row, col, end_col, new):
        sites.append((_category(desc), desc, lambda r=row, c=col, e=end_col, n=new: replace_span(lines, r, c, e, n)))

    for i, tok in enumerate(toks):
        if tok.start[0] != tok.end[0] or tok.start[0] in doc:
            continue
        row, col = tok.start
        ecol = tok.end[1]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if tok.type == tokenize.OP and tok.string in SWAP_OPS:
            # skip unary minus / star-args: only binary positions (previous token is an operand)
            prev = toks[i - 1] if i else None
            if tok.string in ("-", "+", "*") and (prev is None or prev.type == tokenize.OP
                                                 and prev.string not in (")", "]", "}")):
                continue
            add(f"{tok.string} -> {SWAP_OPS[tok.string]}", row, col, ecol, SWAP_OPS[tok.string])
        elif tok.type == tokenize.NAME and tok.string in SWAP_NAMES:
            add(f"{tok.string} -> {SWAP_NAMES[tok.string]}", row, col, ecol, SWAP_NAMES[tok.string])
        elif tok.type == tokenize.NAME and tok.string == "is":
            if nxt is not None and nxt.string == "not" and nxt.start[0] == row:
                add("is not -> !=", row, col, nxt.end[1], "!=")
                add("is not -> is", row, col, nxt.end[1], "is")
            else:
                add("is -> ==", row, col, ecol, "==")
                add("is -> is not", row, col, ecol, "is not")
        elif tok.type == tokenize.NAME and tok.string == "not" and nxt is not None \
                and nxt.start[0] == row and (i == 0 or toks[i - 1].string != "is") \
                and nxt.string != "in":
            add("drop not", row, col, nxt.start[1], "")
        elif tok.type == tokenize.NAME and tok.string == "in" and i and toks[i - 1].string == "not" \
                and toks[i - 1].start[0] == row:
            prev = toks[i - 1]
            add("not in -> in", row, prev.start[1], ecol, "in")
        elif tok.type == tokenize.NUMBER and tok.string.isdigit():
            v = int(tok.string)
            add(f"{v} -> {v + 1}", row, col, ecol, str(v + 1))
            if v > 0:
                add(f"{v} -> {v - 1}", row, col, ecol, str(v - 1))
        elif tok.type == tokenize.STRING and tok.string[:1] in "\"'" and not tok.string.startswith(('"""', "'''")):
            q = tok.string[0]
            body = tok.string[1:-1]
            if not body or "\\" in body:
                continue
            new_body = _mutate_text(body)
            if new_body != body and q not in new_body:
                add(f"string {tok.string} -> {q}{new_body}{q}", row, col, ecol, q + new_body + q)

    # drop one single-line element of a dict / set / list / tuple literal
    for node in ast.walk(tree):
        elts: list[tuple[ast.AST, ast.AST | None]] = []
        if isinstance(node, ast.Dict):
            elts = [(k, v) for k, v in zip(node.keys, node.values) if k is not None]
        elif isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            elts = [(e, None) for e in node.elts]
        if len(elts) < 2 or node.lineno == node.end_lineno:
            continue
        for k, v in elts:
            last = v if v is not None else k
            if k.lineno != last.end_lineno:
                continue
            line = lines[k.lineno - 1]
            stripped = line.strip()
            # the element must be alone on its line
            if line[:k.col_offset].strip() or not stripped.endswith(","):
                continue
            if line[last.end_col_offset:].strip() not in (",", ""):
                continue
            ln = k.lineno
            sites.append(("drop", f"drop element line {ln}: {stripped[:60]}",
                          lambda ln=ln: lines[:ln - 1] + lines[ln:]))
    return sites


def _mutate_text(body: str) -> str:
    r = random.random()
    if body.isupper() or body.islower():
        if r < 0.4 and body.isalpha():
            return body.swapcase()
    if r < 0.7 and len(body) > 1:
        i = random.randrange(len(body))
        return body[:i] + body[i + 1:]
    return body + random.choice(["_", "X", "s", " "])


def code_edit(path: Path, rng: random.Random) -> tuple[list[str], str] | None:
    sites = code_sites(path)
    cats = sorted({c for c, _, _ in sites})
    if not cats:
        return None
    cat = rng.choices(cats, weights=[CODE_CATEGORY_WEIGHTS[c] for c in cats])[0]
    sites = [s for s in sites if s[0] == cat]
    rng.shuffle(sites)
    for _, desc, apply in sites[:20]:
        new = apply()
        if compiles("".join(new)):
            return new, desc
    return None


# --------------------------------------------------------------------------- comment edits

def comment_edit(path: Path, rng: random.Random) -> tuple[list[str], str] | None:
    src = path.read_text("utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    r = rng.random()
    if r < 0.2:
        docs = [d for d in docstrings(tree) if d.lineno != d.end_lineno or len(d.value) > 20]
        if docs:
            d = rng.choice(docs)
            for _ in range(10):
                ln = rng.randint(d.lineno, d.end_lineno)
                words = [w for w in lines[ln - 1].split() if w.isalpha() and len(w) > 2]
                if not words:
                    continue
                w = rng.choice(words)
                new_w = rng.choice([w + "s", w[:-1], rng.choice(DOC_WORDS) + " " + w])
                new = list(lines)
                new[ln - 1] = lines[ln - 1].replace(w, new_w, 1)
                if compiles("".join(new)):
                    return new, f"docstring line {ln}: {w} -> {new_w}"
    # line starts of real statements / bracketed continuations (not inside strings)
    starts = {}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                        tokenize.COMMENT, tokenize.ENDMARKER):
            continue
        row = tok.start[0]
        if row not in starts:
            starts[row] = tok
    cands = [row for row, tok in starts.items()
             if lines[row - 1][:tok.start[1]].strip() == "" and tok.start[1] == len(lines[row - 1]) - len(lines[row - 1].lstrip())]
    phrase = rng.choice(COMMENT_PHRASES)
    rng.shuffle(cands)
    for row in cands[:20]:
        line = lines[row - 1]
        indent = line[: len(line) - len(line.lstrip())]
        new = list(lines)
        if r < 0.35 and "#" not in line and len(line.rstrip()) < 80:
            new[row - 1] = line.rstrip("\r\n") + f"  # {phrase}\n"
            desc = f"trailing comment at line {row}"
        else:
            new.insert(row - 1, f"{indent}# {phrase}\n")
            desc = f"comment before line {row}"
        if compiles("".join(new)):
            return new, desc
    return None


# --------------------------------------------------------------------------- fixture edits

def fixture_edit(path: Path, rng: random.Random) -> tuple[list[str], str] | None:
    lines = read_lines(path)
    cands = [i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith(("--", "#"))]
    meta = [i for i, l in enumerate(lines) if l.lstrip().startswith(("#", "--")) and l.strip() not in ("#", "--")]
    if not cands:
        return None
    use_meta = meta and rng.random() < 0.08
    i = rng.choice(meta if use_meta else cands)
    line = lines[i].rstrip("\r\n")
    eol = lines[i][len(line):] or "\n"
    r = rng.random()
    spaces = [j for j, ch in enumerate(line) if ch == " "]
    if r < 0.4 and spaces:  # insert a token at a word boundary
        j = rng.choice(spaces)
        tok = rng.choice(FIXTURE_TOKENS)
        new_line = line[:j] + " " + tok + line[j:]
        desc = f"insert {tok!r}"
    elif r < 0.65:  # delete one character
        j = rng.randrange(len(line))
        new_line = line[:j] + line[j + 1:]
        desc = f"delete {line[j]!r}"
    elif r < 0.8:  # split an identifier with a space
        letters = [j for j in range(1, len(line)) if line[j - 1].isalnum() and line[j].isalnum()]
        if not letters:
            return None
        j = rng.choice(letters)
        new_line = line[:j] + " " + line[j:]
        desc = "split word"
    elif r < 0.9:  # change a number or keyword case
        words = [w for w in line.split() if w.isalpha()]
        if not words:
            return None
        w = rng.choice(words)
        new_line = line.replace(w, w.swapcase(), 1)
        desc = f"case {w}"
    else:  # swap two adjacent characters
        if len(line) < 2:
            return None
        j = rng.randrange(len(line) - 1)
        new_line = line[:j] + line[j + 1] + line[j] + line[j + 2:]
        desc = "swap chars"
    if new_line == line:
        return None
    new = list(lines)
    new[i] = new_line + eol
    return new, f"line {i + 1}: {desc}"


# --------------------------------------------------------------------------- driver

# Source areas for code/comment edits. An area is drawn first, then a file in it, so that
# small areas (executor, expressions) are covered and the 105 per-dialect files don't swamp
# the core modules. Chosen from the tree's layout only, never from test outcomes.
AREA_WEIGHTS = {"parsers": 0.20, "generators": 0.20, "dialects": 0.18, "optimizer": 0.16,
                "expressions": 0.09, "executor": 0.08, "core": 0.09}


def area_of(rel: str) -> str:
    parts = rel.split("/")
    if len(parts) > 2 and parts[1] in ("parsers", "generators", "dialects", "optimizer",
                                       "expressions", "executor"):
        return parts[1]
    if rel in ("sqlglot/parser.py", "sqlglot/tokens.py", "sqlglot/tokenizer_core.py"):
        return "parsers"
    if rel == "sqlglot/generator.py":
        return "generators"
    if rel == "sqlglot/planner.py":
        return "executor"
    return "core"  # other top-level modules and sqlglot/typing


def pick_file(rng: random.Random, files: list[Path], by_area: dict | None = None) -> Path:
    if by_area:
        areas = sorted(by_area)
        return rng.choice(by_area[rng.choices(areas, weights=[AREA_WEIGHTS[a] for a in areas])[0]])
    return rng.choice(files)


def make_edit(kind: str, rng: random.Random, py: list[Path], fx: list[Path], used: set[Path],
              by_area: dict | None = None):
    for _ in range(50):
        if kind == "fixture":
            f = pick_file(rng, fx)
            fn = fixture_edit
        else:
            f = pick_file(rng, py, by_area)
            fn = code_edit if kind == "code" else comment_edit
        if f in used:
            continue
        res = fn(f, rng)
        if res:
            return f, res[0], res[1]
    raise RuntimeError(f"no {kind} edit found")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlglot", required=True, help="clean git checkout of pinned sqlglot (scratch)")
    ap.add_argument("--out", required=True)
    ap.add_argument("-n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--kinds", help="override weights, e.g. code=1,comment=1")
    ap.add_argument("--only-area", help="restrict code/comment edits to these areas (comma list)")
    ap.add_argument("--areas", action="store_true",
                    help="draw a source area first (AREA_WEIGHTS), then a file in it")
    opts = ap.parse_args()

    repo = Path(opts.sqlglot).resolve()
    if git(repo, "status", "--porcelain").strip():
        sys.exit(f"{repo} is dirty")
    rng = random.Random(opts.seed)
    random.seed(opts.seed)  # _mutate_text
    weights = dict(KIND_WEIGHTS)
    if opts.kinds:
        weights = {k: float(v) for k, v in (p.split("=") for p in opts.kinds.split(","))}
    py = sorted(p for p in (repo / "sqlglot").rglob("*.py"))
    fx = sorted(p for p in (repo / "tests" / "fixtures").rglob("*.sql"))
    by_area: dict[str, list[Path]] | None = None
    if opts.only_area:
        py = [p for p in py if area_of(p.relative_to(repo).as_posix()) in opts.only_area.split(",")]
    if opts.areas:
        by_area = {}
        for p in py:
            by_area.setdefault(area_of(p.relative_to(repo).as_posix()), []).append(p)
    out = Path(opts.out).resolve()
    pending = out / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    taken = {p.stem for d in (out / "pending", out / "commits") if d.exists() for p in d.glob("*.patch")}

    made = 0
    while made < opts.n:
        kind = rng.choices(list(weights), weights=list(weights.values()))[0]
        parts = [kind] if kind != "multi" else [rng.choice(["code", "code", "comment", "fixture"])
                                                  for _ in range(rng.choice([2, 2, 3]))]
        if kind == "multi" and all(p == "comment" for p in parts):
            parts[0] = "code"
        used: set[Path] = set()
        descs = []
        try:
            for part in parts:
                f, new_lines, desc = make_edit(part, rng, py, fx, used, by_area)
                used.add(f)
                f.write_text("".join(new_lines), "utf-8", newline="")
                descs.append(f"{part} {f.relative_to(repo).as_posix()}: {desc}")
            patch = git(repo, "-c", "core.abbrev=7", "diff", "--no-color", "--no-ext-diff")
        finally:
            git(repo, "checkout", "--", ".")
        if not patch.strip():
            continue
        cid = "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(4))
        if cid in taken:
            continue
        taken.add(cid)
        (pending / f"{cid}.patch").write_text(patch, "utf-8", newline="\n")
        (pending / f"{cid}.json").write_text(json.dumps({"kind": kind, "edits": descs}, indent=1), "utf-8")
        made += 1
        print(cid, kind, "; ".join(descs), file=sys.stderr)


if __name__ == "__main__":
    main()
