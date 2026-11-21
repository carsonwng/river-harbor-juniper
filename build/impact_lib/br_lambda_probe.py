"""Which sqlglot lambdas run while sqlglot is imported (strat/gap-mix).

    python br_lambda_probe.py SQLGLOT_ROOT OUT_JSON

Imports every sqlglot module (sorted, like build/impact.py module_names) with a
sys.monitoring PY_START callback and writes {"version": 1, "complete": true,
"started": {rel: [co_firstlineno, ...]}} for every `<lambda>` code object of a
sqlglot file that started during those imports. blast/impact.py only narrows an
import-time table edit to a lambda's own coverage when that lambda never runs at
import (its body then only runs when a test calls it).
"""

import importlib
import json
import sys
from pathlib import Path


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2])
    sys.path.insert(0, str(root))
    prefix = str(root / "sqlglot")
    started: dict[str, set[int]] = {}
    mon = sys.monitoring
    tool = mon.PROFILER_ID if hasattr(mon, "PROFILER_ID") else 2

    def on_start(code, offset):
        if code.co_name == "<lambda>" and code.co_filename.startswith(prefix):
            rel = Path(code.co_filename).resolve().relative_to(root).as_posix()
            started.setdefault(rel, set()).add(code.co_firstlineno)
        return None

    mon.use_tool_id(tool, "br_lambda_probe")
    mon.register_callback(tool, mon.events.PY_START, on_start)
    mon.set_events(tool, mon.events.PY_START)
    errors = []
    try:
        for path in sorted((root / "sqlglot").rglob("*.py")):
            parts = list(path.relative_to(root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            name = ".".join(parts)
            if name == "sqlglot.__main__":
                continue
            try:
                importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc!r}"[:200])
    finally:
        mon.set_events(tool, 0)
        mon.free_tool_id(tool)
    out.write_text(json.dumps({"version": 1, "complete": True, "errors": errors,
                               "started": {k: sorted(v) for k, v in sorted(started.items())}}),
                   "utf-8")


if __name__ == "__main__":
    main()
