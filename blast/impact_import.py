"""Does sqlglot still import with these sources? (impact layer helper, strat/gap-mix)

A class- or module-level statement runs at import. If the new one raises (a
starred set turned into `+set`, a bad key, ...), the module cannot be imported
and every test that imports it, directly or through another module, fails: the
names- and class-users rules of blast/impact.py cannot see that. check() imports
every sqlglot module (build/impact.py's sorted order) in a fresh child
interpreter with the edited files' sources swapped in, and returns the modules
that fail. With the build's import graph (maps/impact/imports.json) only the
edited modules and the modules whose import loads them are imported: a module
that does not load an edited one at import cannot stop importing because of it
(assuming import-time code does not read state another module's import left
behind). The build records the pinned failures (maps/impact/lambdas.json
"errors", from build/impact_lib/br_lambda_probe.py), so only new failures count.

Run as a script it reads {"root": ..., "overrides": {rel: source}} on stdin and
prints {"failed": {module: message}}.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 90


def check(root: Path, overrides: dict[str, str], modules: list[str] | None = None) -> dict[str, str]:
    """{module: error} for modules that fail to import (all sqlglot modules, or
    `sqlglot` plus `modules`). Raises if the child fails."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve())],
                          input=json.dumps({"root": str(root), "overrides": overrides,
                                            "modules": modules}),
                          capture_output=True, text=True, encoding="utf-8", env=env,
                          timeout=TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError(f"import child exit {proc.returncode}: {proc.stderr[-300:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])["failed"]


def _module_name(rel: str) -> str:
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _child() -> None:
    import importlib
    import importlib.abc
    import importlib.machinery
    import importlib.util

    request = json.loads(sys.stdin.read())
    root = Path(request["root"])
    here = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != here]
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True
    overrides = {}
    for rel, source in request["overrides"].items():
        overrides[_module_name(rel)] = (str(root / rel), source, rel.endswith("__init__.py"))

    class Loader(importlib.machinery.SourceFileLoader):
        def __init__(self, fullname, path, source):
            super().__init__(fullname, path)
            self._source = source

        def get_source(self, fullname):
            return self._source

        def get_code(self, fullname):
            return compile(self._source, self.path, "exec", dont_inherit=True)

    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname not in overrides:
                return None
            file, source, package = overrides[fullname]
            return importlib.util.spec_from_file_location(
                fullname, file, loader=Loader(fullname, file, source),
                submodule_search_locations=[str(Path(file).parent)] if package else None)

    sys.meta_path.insert(0, Finder())
    # the child's own stdout carries the answer; sqlglot may print while importing
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    failed = {}
    names = request.get("modules")
    if names is None:
        names = []
        for path in sorted((root / "sqlglot").rglob("*.py")):
            parts = list(path.relative_to(root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            names.append(".".join(parts))
    else:
        names = ["sqlglot"] + sorted(names)
    for name in names:
        if name == "sqlglot.__main__":
            continue
        try:
            importlib.import_module(name)
        except BaseException as exc:  # noqa: BLE001
            failed[name] = f"{type(exc).__name__}: {exc}"[:200]
    sys.stdout = real_stdout
    print(json.dumps({"failed": failed}))


if __name__ == "__main__":
    _child()
