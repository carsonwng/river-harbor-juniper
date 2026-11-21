"""Record the sqlglot lines that run while a module is imported.

Usage: python br_import_probe.py ROOT OUTDIR MODULE...
"core" imports `sqlglot` itself (alone in its process). Any other module is
imported after `sqlglot` (unrecorded), so its lines are the ones its own import
adds. Each module needs a pristine interpreter: on POSIX a fork of a parent that
has imported sqlglot; elsewhere a fresh subprocess per module.
Writes OUTDIR/<module>.json.
"""

import importlib
import json
import os
import subprocess
import sys

root_arg = sys.argv[1]
root = os.path.normcase(os.path.realpath(root_arg))
outdir = sys.argv[2]
modules = sys.argv[3:]
prefix = os.path.join(root, "sqlglot") + os.sep
M = sys.monitoring


def probe(module: str) -> None:
    lines: dict[str, set] = {}
    rel: dict[str, object] = {}

    def on_line(code, line):
        name = code.co_filename
        r = rel.get(name, 0)
        if r == 0:
            norm = os.path.normcase(os.path.realpath(name)) if not name.startswith("<") else ""
            r = os.path.relpath(norm, root).replace(os.sep, "/") if norm.startswith(prefix) else None
            rel[name] = r
        if r is not None:
            lines.setdefault(r, set()).add(line)
        return M.DISABLE

    before = set(sys.modules)
    M.use_tool_id(3, "probe")
    M.register_callback(3, M.events.LINE, on_line)
    M.set_events(3, M.events.LINE)
    error = None
    try:
        importlib.import_module("sqlglot" if module == "core" else module)
    except BaseException as exc:  # noqa: BLE001
        error = repr(exc)
    M.set_events(3, 0)
    loaded = sorted(m for m in set(sys.modules) - before if m.startswith("sqlglot"))
    with open(os.path.join(outdir, module + ".json"), "w", encoding="utf-8") as handle:
        json.dump({"module": module, "error": error, "loaded": loaded,
                   "lines": {k: sorted(v) for k, v in lines.items()}}, handle)


sys.path.insert(0, root_arg)
if modules == ["core"] or (len(modules) == 1 and os.environ.get("BR_PROBE_CHILD")):
    if modules != ["core"]:
        import sqlglot  # noqa: F401
    probe(modules[0])
    sys.exit(0)

import sqlglot  # noqa: F401,E402

for module in modules:
    if module == "core":
        subprocess.run([sys.executable, __file__, root_arg, outdir, "core"])
    elif hasattr(os, "fork"):
        pid = os.fork()
        if pid == 0:
            try:
                sys.stdout.flush()
                probe(module)
            finally:
                os._exit(0)
        os.waitpid(pid, 0)
    else:
        subprocess.run([sys.executable, __file__, root_arg, outdir, module],
                       env=dict(os.environ, BR_PROBE_CHILD="1"))
