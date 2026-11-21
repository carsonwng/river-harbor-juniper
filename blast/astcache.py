"""Run-time-only helpers that make AST work cheaper (strat/runcpu2). No selection logic.

Ported from strat/runcpu (79a3b3e) and extended:

  * parse(text) memoises ast.parse by source text (bounded LRU), so a pinned file is
    parsed once per run and a patched file once per commit, whichever layer asks
    first. Callers must not mutate the returned trees (impact's _compare_edits swaps
    fields and restores them in a finally, which is fine).
  * same(a, b) is exactly `ast.dump(a) == ast.dump(b)` (default arguments: no
    attributes, show_empty=False), computed by walking both trees and stopping at the
    first difference, without building strings.
  * same(a, b, funcs=True, lambdas=keep) is exactly
    `ast.dump(blank(a)) == ast.dump(blank(b))` where blank() is impact's old
    deepcopy-and-replace: every reachable function body becomes [Pass()] (when
    funcs) and the body of every reachable lambda whose lineno is not in `keep`
    becomes Constant(None) (when keep is not None). A replaced body is never
    walked, as in the copied tree, where the old body is detached.
  * code_lines(rel, text): line numbers of compile(text)'s code objects, memoised.
"""

from __future__ import annotations

import ast

_AST = ast.AST
_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
_LAMBDA = ast.Lambda
_BLANK_LAMBDA_BODY = ast.Constant(None)
_TREES: dict[str, ast.Module] = {}  # insertion order = least recently used first
_BUDGET = 3_000_000  # source chars kept parsed (about 45 bytes of AST per char)
_held = 0
_CODE_LINES: dict[tuple[str, str], frozenset] = {}


def parse(text: str) -> ast.Module:
    """ast.parse(text), memoised (LRU). Raises what ast.parse raises (never cached)."""
    global _held
    tree = _TREES.pop(text, None)
    if tree is None:
        tree = ast.parse(text)
        _held += len(text)
        while _held > _BUDGET and _TREES:
            oldest = next(iter(_TREES))
            _TREES.pop(oldest)
            _held -= len(oldest)
    _TREES[text] = tree
    return tree


def _leaf_equal(x, y) -> bool:
    # ast.dump renders non-node, non-list values with repr(); match that exactly
    # (1 vs True vs 1.0, 0.0 vs -0.0 differ in repr but compare equal).
    if type(x) is not type(y):
        return False
    if type(x) is str:
        return x == y
    return repr(x) == repr(y)


def same(a, b, funcs: bool = False, lambdas: set | frozenset | None = None) -> bool:
    """ast.dump(a) == ast.dump(b) (optionally of the blanked copies), without
    copying or formatting."""
    stack = [(a, b)]
    pop, push, extend = stack.pop, stack.append, stack.extend
    blanking = funcs or lambdas is not None
    while stack:
        x, y = pop()
        if x is y:
            continue  # same object: same dump (identical subtrees are never blanked differently)
        tx = type(x)
        if tx is not type(y):
            return False
        if tx is list:
            if len(x) != len(y):
                return False
            extend(zip(x, y))
        elif isinstance(x, _AST):
            if blanking:
                if funcs and tx in _FUNC:
                    for name in tx._fields:
                        if name != "body":
                            push((getattr(x, name, None), getattr(y, name, None)))
                    continue  # both bodies are [Pass()]
                if lambdas is not None and tx is _LAMBDA:
                    push((getattr(x, "args", None), getattr(y, "args", None)))
                    bx = x.body if x.lineno in lambdas else _BLANK_LAMBDA_BODY
                    by = y.body if y.lineno in lambdas else _BLANK_LAMBDA_BODY
                    if bx is not by:
                        push((bx, by))
                    continue
            for name in tx._fields:
                # ast.dump skips a field the node lacks; a lacking field equals None here,
                # which is only ever compared against the same node type's field
                push((getattr(x, name, None), getattr(y, name, None)))
        elif not _leaf_equal(x, y):
            return False
    return True


def code_lines(rel: str, text: str) -> frozenset:
    """Every line number in the code objects of compile(text, rel), memoised.
    Raises what compile raises (never cached)."""
    key = (rel, text)
    got = _CODE_LINES.get(key)
    if got is None:
        code = compile(text, rel, "exec", dont_inherit=True)
        lines: set[int] = set()
        stack = [code]
        while stack:
            co = stack.pop()
            lines.update(l for _, _, l in co.co_lines() if l)
            stack.extend(c for c in co.co_consts if hasattr(c, "co_code"))
        got = _CODE_LINES[key] = frozenset(lines)
        if len(_CODE_LINES) > 64:
            _CODE_LINES.pop(next(iter(_CODE_LINES)))
    return got
