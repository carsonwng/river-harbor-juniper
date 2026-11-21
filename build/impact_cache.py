#!/usr/bin/env python3
"""Cache-filler spans of the pinned sqlglot (static; a few seconds).

blast/impact.py `_cache_scan` finds the functions that fill a process-wide cache and
the lines that read it. It only ever scans pinned files, so the build runs it once per
file and writes maps/impact_cache/fillers.json: rel -> [[lo, hi, [reader lines],
module_wide], ...] (files with no filler are listed with []). The selector falls back
to scanning at run time for a file that is missing here.
"""

import argparse
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blast.impact import _cache_scan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlglot", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    options = parser.parse_args()
    out = {}
    for path in sorted((options.sqlglot / "sqlglot").rglob("*.py")):
        rel = path.relative_to(options.sqlglot).as_posix()
        try:
            tree = ast.parse(path.read_text("utf-8"))
        except SyntaxError:
            continue
        out[rel] = [[lo, hi, sorted(readers), bool(mw)] for lo, hi, readers, mw in _cache_scan(tree)]
    options.out.mkdir(parents=True, exist_ok=True)
    (options.out / "fillers.json").write_text(json.dumps({"version": 1, "files": out}), "utf-8")
    print(f"[impact_cache] {len(out)} files, {sum(1 for v in out.values() if v)} with fillers",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
