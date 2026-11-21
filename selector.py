"""Entry point: one JSON line per patch on stdout, logs on stderr.

The pipeline is patch.parse -> triage.classify -> impact.select -> pack.split
(see blast/). Any layer that raises falls back to running every test for that
commit, because a crash rejects the run and a miss costs 300 s.
"""

import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blast import context, impact, pack, patch, triage  # noqa: E402

# BR_DATA lets the harness run this on the host; the scorer always mounts /data.
DATA = Path(os.environ.get("BR_DATA", "/data"))
VERBOSE = os.environ.get("BR_VERBOSE") == "1"


def choose(commit: str, text: str, ctx: context.Context) -> set[int]:
    parsed = patch.parse(commit, text)
    selected: set[int] = set()
    deferred = []
    for change in parsed.files:
        decision = triage.classify(change, ctx)
        if VERBOSE:
            print(f"{commit} {change.path}: {decision.kind} {decision.reason}", file=sys.stderr)
        if decision.kind == "tests":
            selected |= decision.tests
        elif decision.kind == "defer":
            deferred.append(change)
    selected |= impact.select(deferred, ctx)
    return ctx.all_tests  # probe: every test on every commit


def main() -> None:
    ctx = context.load(DATA)
    for patch_path in sorted((DATA / "commits").glob("*.patch")):
        commit = patch_path.stem
        try:
            selected = choose(commit, patch_path.read_text("utf-8", "replace"), ctx)
            workers = pack.split(selected, ctx)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            workers = pack.split(ctx.all_tests, ctx)
        print(json.dumps({"commit": commit, "workers": workers}), flush=True)


if __name__ == "__main__":
    main()
