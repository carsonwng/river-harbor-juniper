#!/usr/bin/env python3
"""Run every layer's build step: build/<layer>.py --sqlglot DIR --out MAPS/<layer>.

Runs in the Dockerfile (sqlglot at /opt/sqlglot, maps to /app/maps) and locally
via `python harness/br.py maps` (shared .cache/sqlglot, maps to <worktree>/.maps).
Build time is not charged, but the whole image build has 10 minutes on 4 cores.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlglot", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--only", help="run just this layer")
    options = parser.parse_args()
    for step in sorted(HERE.glob("*.py")):
        layer = step.stem
        if layer == "run_all" or (options.only and layer != options.only):
            continue
        out = options.out / layer
        out.mkdir(parents=True, exist_ok=True)
        start = time.time()
        print(f"[build] {layer} -> {out}", file=sys.stderr, flush=True)
        subprocess.run([sys.executable, str(step), "--sqlglot", str(options.sqlglot),
                        "--out", str(out)], check=True)
        print(f"[build] {layer} done in {time.time() - start:.1f}s", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
