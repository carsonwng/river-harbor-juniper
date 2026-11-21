"""Finalize a labelled set and copy it into the repo in harness format.

    python validation/publish.py --set .cache/mutants/m1 --to validation/sets/m1

Writes commits/*.patch, tests.json, answers.json (harness format) and edits.json (the
generator's description of each edit, for humans analysing misses).
Validation data only: nothing under validation/ may be read by the selector or its build.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--to", required=True)
    o = ap.parse_args()
    src, dst = Path(o.set).resolve(), Path(o.to).resolve()
    subprocess.run([sys.executable, str(HERE / "label.py"), "--set", str(src), "--finalize"], check=True)
    answers = json.loads((src / "answers.json").read_text("utf-8"))["commits"]
    if dst.exists():
        shutil.rmtree(dst / "commits", ignore_errors=True)
    (dst / "commits").mkdir(parents=True)
    edits = {}
    for cid in sorted(answers):
        shutil.copyfile(src / "commits" / f"{cid}.patch", dst / "commits" / f"{cid}.patch")
        lab = json.loads((src / "labels" / f"{cid}.json").read_text("utf-8"))
        edits[cid] = lab.get("edits")
    shutil.copyfile(src / "tests.json", dst / "tests.json")
    (dst / "answers.json").write_text(json.dumps({"commits": answers}, indent=1) + "\n", "utf-8")
    (dst / "edits.json").write_text(json.dumps(edits, indent=1) + "\n", "utf-8")
    print(f"published {len(answers)} commits to {dst}", file=sys.stderr)


if __name__ == "__main__":
    main()
