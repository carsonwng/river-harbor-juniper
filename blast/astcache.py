"""Shared, run-time-only helpers that make AST work cheaper. No selection logic.

Triage and impact both parse the pinned file and the patched file and ask whether the
two ASTs are equal. Doing that with `ast.dump(a) == ast.dump(b)` formats every node of
large modules (expressions.py, parser.py) into strings, twice per layer per commit,
and was most of the selector's CPU time. Here:

  * parse(text) memoises ast.parse by source text (a few recent texts), so the pinned
    file is parsed once per run and the patched file once per commit, whichever layer
    asks first. Callers must not mutate the returned trees.
  * same(a, b) is exactly `ast.dump(a) == ast.dump(b)` (include_attributes=False),
    computed by walking both trees and stopping at the first difference, without
    building strings.
"""

from __future__ import annotations

import ast

_AST = ast.AST
_TREES: dict[str, ast.Module] = {}  # insertion order = least recently used first
_BUDGET = 4_000_000  # source chars kept parsed (about 45 bytes of AST per char)
_held = 0
_SAME: dict[tuple[str, str], bool] = {}


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


def same(a: ast.AST, b: ast.AST) -> bool:
    """ast.dump(a) == ast.dump(b), without formatting."""
    stack = [(a, b)]
    pop, push, extend = stack.pop, stack.append, stack.extend
    while stack:
        x, y = pop()
        tx = type(x)
        if tx is not type(y):
            return False
        if tx is list:
            if len(x) != len(y):
                return False
            extend(zip(x, y))
        elif isinstance(x, _AST):
            for name in tx._fields:
                # ast.dump skips a field the node lacks; a lacking field equals None here,
                # which is only ever compared against the same node type's field
                push((getattr(x, name, None), getattr(y, name, None)))
        elif x is not y and not _leaf_equal(x, y):
            return False
    return True


def same_text(old_text: str, new_text: str) -> bool:
    """same(parse(old_text), parse(new_text)), memoised. Raises what parse raises."""
    key = (old_text, new_text)
    got = _SAME.get(key)
    if got is None:
        got = _SAME[key] = same(parse(old_text), parse(new_text))
        if len(_SAME) > 16:
            _SAME.pop(next(iter(_SAME)))
    return got
