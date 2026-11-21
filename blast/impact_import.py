"""Import check: does the edited sqlglot still import every module?

    python -I impact_import.py            (stdin: {"sqlglot": DIR, "files": {rel: text}})

Run in a fresh interpreter by blast/impact.py (and by build/impact.py with no
files, to list the modules that import on the pinned commit). Each edited file
is served from its new text (compiled directly, never from a cached .pyc of the
pinned source); everything else loads from DIR. Imports `sqlglot`, then every
edited module, then every module under sqlglot/ in sorted order, and prints
{"failed": {module: error}, "modules": n} as the last stdout line.

A module that imported on the pinned commit and no longer does fails every test
that imports it, at collection or lazily (Dialect.get, the dialects package's
__getattr__). Which tests those are is not in the coverage map (import-time
lines are not attributed to tests), so the selector then runs every test.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import sys


def module_name(rel: str) -> str | None:
    if not rel.startswith("sqlglot/") or not rel.endswith(".py"):
        return None
    name = rel[:-3].replace("/", ".")
    if name.endswith(".__init__"):
        name = name[: -len(".__init__")]
    if name.split(".")[-1] == "__main__":
        return None
    return name


def all_modules(root: str) -> list[str]:
    out = []
    base = os.path.join(root, "sqlglot")
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not d.startswith("."))
        for f in sorted(filenames):
            if f.endswith(".py"):
                rel = os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/")
                name = module_name(rel)
                if name:
                    out.append(name)
    return sorted(set(out))


class _EditedLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, fullname, path, text):
        super().__init__(fullname, path)
        self._text = text

    def get_data(self, path):
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(self.path)):
            return self._text.encode("utf-8")
        return super().get_data(path)

    def get_code(self, fullname):
        return compile(self._text, self.path, "exec", dont_inherit=True)


class _EditedFinder(importlib.abc.MetaPathFinder):
    def __init__(self, root: str, files: dict[str, str]):
        self.by_name = {}
        for rel, text in files.items():
            name = module_name(rel)
            if name:
                self.by_name[name] = (os.path.join(root, *rel.split("/")), text)

    def find_spec(self, fullname, path=None, target=None):
        hit = self.by_name.get(fullname)
        if hit is None:
            return None
        filename, text = hit
        loader = _EditedLoader(fullname, filename, text)
        is_pkg = os.path.basename(filename) == "__init__.py"
        return importlib.util.spec_from_file_location(
            fullname, filename, loader=loader,
            submodule_search_locations=[os.path.dirname(filename)] if is_pkg else None)


def main() -> int:
    request = json.loads(sys.stdin.read() or "{}")
    root = request["sqlglot"]
    files = request.get("files") or {}
    sys.path.insert(0, root)
    finder = _EditedFinder(root, files)
    sys.meta_path.insert(0, finder)
    failed: dict[str, str] = {}
    order = ["sqlglot"] + sorted(finder.by_name) + all_modules(root)
    # keep stdout for the result line only
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        for name in order:
            if name in failed or name in sys.modules:
                continue
            try:
                importlib.import_module(name)
            except BaseException as exc:  # noqa: BLE001  (SystemExit, recursion, ...)
                failed[name] = f"{type(exc).__name__}: {exc}"[:300]
                for loaded in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
                    sys.modules.pop(loaded, None)
    finally:
        sys.stdout = real_stdout
    print(json.dumps({"failed": failed, "modules": len(order)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
