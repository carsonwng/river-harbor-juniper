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

Import server (strat/runcpu2; CPU only, the answer is unchanged): for a module list, a
fresh child first imports `sqlglot`, and when no edited module is part of that import
it is the pristine sqlglot core, the same every time. A server child (same command,
same environment, same setup) does that setup and `import sqlglot` once, then forks
one child per check, which installs the overrides and imports the modules exactly as
the fresh child does after its own `import sqlglot`. If an edited module is already in
sys.modules after the pristine import, or that import looked it up at all, the
forked child reports a conflict and the
check runs in a fresh child. Any server trouble also falls back to a fresh child.
BR_IMPORT_SERVER=0 turns the server off.
"""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 90


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def check(root: Path, overrides: dict[str, str], modules: list[str] | None = None) -> dict[str, str]:
    """{module: error} for modules that fail to import (all sqlglot modules, or
    `sqlglot` plus `modules`). Raises if the child fails."""
    if modules is not None and not _SERVER["disabled"]:
        try:
            got = _server_check(root, overrides, modules)
        except Exception as exc:  # noqa: BLE001
            print(f"impact_import: server failed ({exc!r}); fresh child", file=sys.stderr)
            _server_stop()
            got = None
        if got is not None:
            return got
    return _fresh_check(root, overrides, modules)


def _fresh_check(root: Path, overrides: dict[str, str], modules: list[str] | None) -> dict[str, str]:
    proc = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve())],
                          input=json.dumps({"root": str(root), "overrides": overrides,
                                            "modules": modules}),
                          capture_output=True, text=True, encoding="utf-8", env=_env(),
                          timeout=TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError(f"import child exit {proc.returncode}: {proc.stderr[-300:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])["failed"]


# ---------------------------------------------------------------- import server, parent side

_SERVER: dict = {"proc": None, "root": None, "atexit": False,
                 "disabled": os.name != "posix" or not hasattr(os, "fork")
                 or os.environ.get("BR_IMPORT_SERVER", "1") != "1"}


def _server_stop() -> None:
    """Kill the server (and any forked child: own session) and stop using it."""
    proc = _SERVER["proc"]
    _SERVER["proc"] = None
    _SERVER["disabled"] = True
    if proc is not None:
        try:
            os.killpg(proc.pid, 9)
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


def _server_close() -> None:
    proc = _SERVER["proc"]
    if proc is None:
        return
    try:
        proc.stdin.close()
        proc.wait(timeout=5)
        _SERVER["proc"] = None
    except Exception:  # noqa: BLE001
        _server_stop()


def _read_reply(proc, timeout: float) -> dict:
    import selectors
    import time

    fd = proc.stdout.fileno()
    data = b""
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as sel:
        sel.register(fd, selectors.EVENT_READ)
        while not data.endswith(b"\n"):
            left = deadline - time.monotonic()
            if left <= 0 or not sel.select(left):
                raise TimeoutError("import server timed out")
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                raise RuntimeError("import server closed")
            data += chunk
    return json.loads(data.decode("utf-8"))


def _send(proc, value: dict) -> None:
    proc.stdin.write((json.dumps(value) + "\n").encode("utf-8"))
    proc.stdin.flush()


def _server_check(root: Path, overrides: dict[str, str], modules: list[str]):
    """The fresh child's answer, from a fork of the server; None = use a fresh child."""
    if _SERVER["proc"] is not None and _SERVER["root"] != str(root):
        _server_close()
    proc = _SERVER["proc"]
    if proc is None:
        proc = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve())],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=_env(), start_new_session=True)
        _SERVER["proc"], _SERVER["root"] = proc, str(root)
        if not _SERVER["atexit"]:
            atexit.register(_server_close)
            _SERVER["atexit"] = True
        _send(proc, {"serve": True, "root": str(root)})
        if not _read_reply(proc, TIMEOUT_S).get("ready"):
            raise RuntimeError("pristine sqlglot does not import")
    _send(proc, {"overrides": overrides, "modules": modules})
    reply = _read_reply(proc, TIMEOUT_S)
    if reply.get("conflict"):
        return None  # an edited module is part of the pristine import
    if "failed" not in reply:
        raise RuntimeError(f"import server: {reply.get('error')}")
    return reply["failed"]


# ---------------------------------------------------------------- child


def _module_name(rel: str) -> str:
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _setup(root: Path, overrides: dict, asked: set | None = None) -> None:
    """The child's state before its first import: sys.path, no bytecode writes, and
    the override finder (it reads `overrides`, which the server fills after a fork)."""
    import importlib.abc
    import importlib.machinery
    import importlib.util

    here = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != here]
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True

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
            if asked is not None:
                asked.add(fullname)
            if fullname not in overrides:
                return None
            file, source, package = overrides[fullname]
            return importlib.util.spec_from_file_location(
                fullname, file, loader=Loader(fullname, file, source),
                submodule_search_locations=[str(Path(file).parent)] if package else None)

    sys.meta_path.insert(0, Finder())


def _fill(root: Path, overrides: dict, sources: dict[str, str]) -> None:
    for rel, source in sources.items():
        overrides[_module_name(rel)] = (str(root / rel), source, rel.endswith("__init__.py"))


def _import_all(names: list[str]) -> dict[str, str]:
    import importlib

    failed = {}
    for name in names:
        if name == "sqlglot.__main__":
            continue
        try:
            importlib.import_module(name)
        except BaseException as exc:  # noqa: BLE001
            failed[name] = f"{type(exc).__name__}: {exc}"[:200]
    return failed


def _child() -> None:
    request = json.loads(sys.stdin.readline())
    if request.get("serve"):
        _serve(Path(request["root"]))
        return
    root = Path(request["root"])
    overrides: dict = {}
    _setup(root, overrides)
    _fill(root, overrides, request["overrides"])
    # the child's own stdout carries the answer; sqlglot may print while importing
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
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
    failed = _import_all(names)
    sys.stdout = real_stdout
    print(json.dumps({"failed": failed}))


def _serve(root: Path) -> None:
    overrides: dict = {}
    asked: set = set()  # every module the pristine import looked up (loaded or not)
    _setup(root, overrides, asked)
    out = sys.stdout.fileno()
    sys.stdout = sys.stderr

    def reply(data: bytes) -> None:
        os.write(out, data + b"\n")

    # the fresh child's first import for a module list, done once
    if _import_all(["sqlglot"]):
        reply(json.dumps({"ready": False}).encode("utf-8"))
        return
    asked = frozenset(asked) | frozenset(sys.modules)
    reply(json.dumps({"ready": True}).encode("utf-8"))
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
                if any(_module_name(rel) in asked or _module_name(rel) in sys.modules
                       for rel in req["overrides"]):
                    value = {"conflict": True}
                else:
                    _fill(root, overrides, req["overrides"])
                    value = {"failed": _import_all(["sqlglot"] + sorted(req["modules"]))}
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
            reply(json.dumps({"error": f"forked child status {status}"}).encode("utf-8"))
        else:
            reply(data)


if __name__ == "__main__":
    _child()
