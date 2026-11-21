"""AST instrumentation for the attribute-read profile.

An import hook compiles every module under <root>/sqlglot and <root>/tests from
a rewritten AST (positions kept, so line-based logic and tracebacks are as
before; nothing is written to disk):

    obj.NAME          ->  _br_rd(obj, "NAME")      for NAME in the tracked set
    obj.__dict__      ->  _br_rd(obj, "__dict__")  (a class's whole namespace)
    getattr(o, n...)  ->  _br_ga(o, n...)          (any name, also computed ones)
    vars(o)           ->  _br_vars(o)
    dict(x)           ->  _br_dict(x)              (a copy keeps its table identity)
    o.NAME op= v      ->  records the read of o.NAME first (o a plain name)

The helpers are put in builtins by br_attr_rt, so rewritten code needs no
import. pytest must run with --assert=plain so test modules come through here.
`rewrite_code` is also used for doctest examples.
"""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import os
import sys


class Rewriter(ast.NodeTransformer):
    def __init__(self, tracked: set[str]) -> None:
        self.tracked = tracked
        self.count = 0

    def _call(self, name: str, args: list, node) -> ast.Call:
        self.count += 1
        call = ast.Call(func=ast.Name(id=name, ctx=ast.Load()), args=args, keywords=[])
        ast.copy_location(call, node)
        ast.copy_location(call.func, node)
        return call

    def visit_Attribute(self, node: ast.Attribute):
        self.generic_visit(node)
        if isinstance(node.ctx, ast.Load) and (node.attr in self.tracked or node.attr == "__dict__"):
            const = ast.copy_location(ast.Constant(node.attr), node)
            return self._call("_br_rd", [node.value, const], node)
        return node

    def visit_Call(self, node: ast.Call):
        self.generic_visit(node)
        func = node.func
        if isinstance(func, ast.Name) and not any(isinstance(a, ast.Starred) for a in node.args):
            if func.id == "getattr" and 2 <= len(node.args) <= 3 and not node.keywords:
                return self._call("_br_ga", node.args, node)
            if func.id == "vars" and len(node.args) == 1 and not node.keywords:
                return self._call("_br_vars", node.args, node)
            if func.id == "dict" and len(node.args) == 1 and not node.keywords:
                return self._call("_br_dict", node.args, node)
        return node

    def visit_AugAssign(self, node: ast.AugAssign):
        self.generic_visit(node)
        t = node.target
        if isinstance(t, ast.Attribute) and t.attr in self.tracked and isinstance(t.value, ast.Name):
            name = ast.copy_location(ast.Name(id=t.value.id, ctx=ast.Load()), node)
            const = ast.copy_location(ast.Constant(t.attr), node)
            read = ast.copy_location(ast.Expr(self._call("_br_rd", [name, const], node)), node)
            return [read, node]
        return node


def rewrite_code(source, filename: str, tracked: set[str], mode: str = "exec", flags: int = 0,
                 dont_inherit: bool = True):
    tree = ast.parse(source, filename, mode)
    Rewriter(tracked).visit(tree)
    ast.fix_missing_locations(tree)
    return compile(tree, filename, mode, flags, dont_inherit)


class _Loader(importlib.machinery.SourceFileLoader):
    tracked: set[str] = set()

    def get_code(self, fullname):
        path = self.get_filename(fullname)
        data = self.get_data(path)
        return self.source_to_code(data, path)

    def source_to_code(self, data, path, *, _optimize=-1):
        return rewrite_code(data, path, self.tracked)


class _Finder:
    def __init__(self, prefixes: tuple[str, ...]) -> None:
        self.prefixes = prefixes

    def find_spec(self, fullname, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or not spec.origin or not spec.origin.endswith(".py"):
            return spec
        origin = os.path.realpath(spec.origin)
        if not origin.startswith(self.prefixes):
            return spec
        loader = _Loader(fullname, spec.origin)
        new = importlib.util.spec_from_file_location(
            fullname, spec.origin, loader=loader,
            submodule_search_locations=spec.submodule_search_locations)
        return new

    def invalidate_caches(self):
        pass


def install(root: str, tracked: set[str]) -> None:
    import importlib.util  # noqa: F401

    _Loader.tracked = tracked
    root = os.path.realpath(root)
    prefixes = (os.path.join(root, "sqlglot") + os.sep, os.path.join(root, "tests") + os.sep)
    sys.meta_path.insert(0, _Finder(prefixes))
