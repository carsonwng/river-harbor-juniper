# usage (in image): bash runverify.sh SETDIR SELFILE COMMIT...
# For each commit: copy pinned sqlglot, apply the patch, run every test NOT selected
# (from SELFILE, the selector's stdout for SETDIR) and report failures.
set -u
SET=$1; SEL=$2; shift 2
for c in "$@"; do
  rm -rf /tmp/sg && cp -r /opt/sqlglot /tmp/sg && cd /tmp/sg
  python - "$SET/commits/$c.patch" <<'P' || { echo "$c APPLY FAILED"; continue; }
import sys
from pathlib import Path
sys.path.insert(0, "/app")
from blast import patch, impact
p = Path(sys.argv[1])
for ch in patch.parse(p.stem, p.read_text()).files:
    assert ch.old_path == ch.new_path and not ch.binary, ch.path
    f = Path("/tmp/sg", ch.path)
    new, _ = impact._apply(f.read_text().splitlines(), ch)
    f.write_text(chr(10).join(new) + chr(10))
P
  python - "$SET" "$SEL" "$c" > /tmp/ids.txt <<'P'
import json, sys
s, sel, c = sys.argv[1:]
tests = json.load(open(f"{s}/tests.json"))["tests"]
for line in open(sel):
    o = json.loads(line)
    if o["commit"] == c:
        chosen = {t for w in o["workers"] for t in w}
        break
import os
base = None
if os.environ.get("BASESEL"):
    for line in open(os.environ["BASESEL"]):
        o = json.loads(line)
        if o["commit"] == c:
            base = {t for w in o["workers"] for t in w}
for t in tests:
    if t["number"] not in chosen and (base is None or t["number"] in base):
        print(t["id"])
P
  n=$(wc -l < /tmp/ids.txt)
  if [ "$n" = 0 ]; then echo "$c dropped=0"; continue; fi
  SKIP_INTEGRATION=0 timeout 900 python -m pytest -q -p no:cacheprovider -n 2 $(cat /tmp/ids.txt | tr '\n' ' ') > /tmp/out.txt 2>&1
  echo "$c dropped=$n exit=$? $(tail -1 /tmp/out.txt)"
  grep -E "^FAILED|^ERROR" /tmp/out.txt | head -5
done
