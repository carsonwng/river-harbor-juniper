"""Record tests/test_executor.py::TestExecutor::test_execute_tpcds in one process
(strat/r2-replay). Run by build/replay.py with PYTHONHASHSEED=0, cwd = sqlglot.

    python br_replay_rec.py SQLGLOT OUTDIR REPO

It does what the test does, in order: setUpClass (tpc-h and tpc-ds tables, fixture
pairs), then per query parse_one, the pickle round trip to the pool, and for queries
marked `execute`, find_tables + sqlglot.executor.execute in the "child", then the
duckdb transpile of cached_execute. (duckdb itself and pandas are not run: they do not
execute sqlglot code.) It records:

- per stage, every sqlglot (file, line) executed: `import`, `setup`, `parse`, `serde`,
  `child`, `optimize`, `rule:<name>` (each optimizer rule called by optimize), `plan`,
  `exec`, `transpile`;
- per rule call: the pickled arguments at entry and the digest of (return value,
  arguments) at return.
"""

import os
import pickle
import sys
import time
import zlib

SG, OUT, REPO = sys.argv[1], sys.argv[2], sys.argv[3]
if os.environ.get("PYTHONHASHSEED") != "0":
    sys.exit("br_replay_rec: needs PYTHONHASHSEED=0")
SGPKG = os.path.join(os.path.abspath(SG), "sqlglot") + os.sep
mon = sys.monitoring
TOOL = 3
mon.use_tool_id(TOOL, "br-replay")
stage = ["import"]
cov: dict[str, set] = {}


def on_line(code, line):
    f = code.co_filename
    if f.startswith(SGPKG):
        cov.setdefault(stage[-1], set()).add((f[len(SGPKG):].replace(os.sep, "/"), line))
    return mon.DISABLE


def push(s):
    stage.append(s)
    mon.restart_events()


def pop():
    stage.pop()
    mon.restart_events()


mon.register_callback(TOOL, mon.events.LINE, on_line)
mon.set_events(TOOL, mon.events.LINE)

sys.path.insert(0, SG)
sys.path.insert(1, REPO)
os.chdir(SG)
import ast as _ast  # noqa: E402
import csv  # noqa: E402

from blast import replaylib as R  # noqa: E402
from sqlglot import find_tables, parse_one, transpile  # noqa: E402
import sqlglot.executor as EXMOD  # noqa: E402
from sqlglot.executor.python import PythonExecutor  # noqa: E402
from sqlglot.executor.table import Table  # noqa: E402
from sqlglot.optimizer import optimizer as O  # noqa: E402
from sqlglot.planner import Plan  # noqa: E402
from tests.helpers import FIXTURES_DIR, TPCDS_SCHEMA, TPCH_SCHEMA, load_sql_fixture_pairs  # noqa: E402
from tests.test_executor import open_file  # noqa: E402

RULE_CODES = {r.__code__: r.__name__ for r in O.RULES}
OPT_CODE = O.optimize.__code__
EXEC_CODE = EXMOD.execute.__code__
CALLEE = {Plan.__init__.__code__: "plan", PythonExecutor.execute.__code__: "exec",
          O.optimize.__code__: "optimize"}
active = {}
records: dict[str, list] = {}
qidx = [None]
errors = []


def on_start(code, off):
    fr = sys._getframe(1)
    caller = fr.f_back.f_code if fr.f_back else None
    if code in RULE_CODES and caller is OPT_CODE:
        name = "rule:" + RULE_CODES[code]
        n = code.co_argcount + code.co_kwonlyargcount + bool(code.co_flags & 4) + bool(code.co_flags & 8)
        loc = fr.f_locals
        args = {k: loc[k] for k in code.co_varnames[:n]}
        try:
            blob = R.dumps(args)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: pickle {exc!r}")
            blob = None
        active[id(fr)] = (name, args, blob)
        push(name)
    elif caller is EXEC_CODE and code in CALLEE:
        active[id(fr)] = (CALLEE[code], None, None)
        push(CALLEE[code])


def on_return(code, off, retval):
    a = active.pop(id(sys._getframe(1)), None)
    if a is None:
        return
    name, args, blob = a
    if args is not None:
        try:
            digest = R.odigest((retval, args)) if blob is not None else None
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: digest {exc!r}")
            digest = None
        records.setdefault(name, []).append((qidx[0], blob, digest))
    pop()


def on_unwind(code, off, exc):
    if id(sys._getframe(1)) in active:
        errors.append(f"unwind {code.co_qualname}: {exc!r}")
        active.pop(id(sys._getframe(1)))
        pop()


codes = list(RULE_CODES) + list(CALLEE)
mon.register_callback(TOOL, mon.events.PY_START, on_start)
mon.register_callback(TOOL, mon.events.PY_RETURN, on_return)
mon.register_callback(TOOL, mon.events.PY_UNWIND, on_unwind)
mon.set_events(TOOL, mon.events.LINE | mon.events.PY_UNWIND)
for c in codes:
    mon.set_local_events(TOOL, c, mon.events.PY_START | mon.events.PY_RETURN)

t0 = time.process_time()
push("setup")


def load(schema, d):
    out = {}
    for table, columns in schema.items():
        file_name = f"{FIXTURES_DIR}/optimizer/{d}/{table}.csv.gz"
        reader = csv.reader(open_file(file_name), delimiter="|")
        rows, ctypes = [], []
        next(reader)
        for row in reader:
            if not ctypes:
                for v in row:
                    try:
                        ctypes.append(type(_ast.literal_eval(v)))
                    except (ValueError, SyntaxError):
                        ctypes.append(str)
            rows.append(tuple(None if (t is not str and v == "") else t(v) for t, v in zip(ctypes, row)))
        out[table] = Table(columns=columns, rows=rows)
    return out


load(TPCH_SCHEMA, "tpc-h")
tables = load(TPCDS_SCHEMA, "tpc-ds")
list(load_sql_fixture_pairs("optimizer/tpc-h/tpc-h.sql"))
sqls = list(load_sql_fixture_pairs("optimizer/tpc-ds/tpc-ds.sql"))
pop()
for i, (meta, sql, _) in enumerate(sqls):
    qidx[0] = i
    push("parse")
    e = parse_one(sql)
    pop()
    push("serde")
    e2 = pickle.loads(pickle.dumps(e))
    pop()
    if not meta.get("execute"):
        continue
    push("child")
    tb = {t.name: tables[t.name] for t in find_tables(e2)}
    EXMOD.execute(e2, schema=TPCDS_SCHEMA, tables=tb)
    pop()
    push("transpile")
    transpile(sql, write="duckdb")
    pop()
mon.set_events(TOOL, 0)
for c in codes:
    mon.set_local_events(TOOL, c, 0)
mon.free_tool_id(TOOL)

os.makedirs(OUT, exist_ok=True)
files = {}
for name, recs in records.items():
    if any(b is None or d is None for _, b, d in recs):
        errors.append(f"{name}: incomplete")
        continue
    fname = name.replace(":", "_") + ".pkl"
    with open(os.path.join(OUT, fname), "wb") as f:
        pickle.dump([(q, zlib.compress(b, 6), d) for q, b, d in recs], f)
    files[name] = fname
stages = sorted(cov)
lines: dict[str, dict[int, int]] = {}
for si, s in enumerate(stages):
    for rel, line in cov[s]:
        d = lines.setdefault(rel, {})
        d[line] = d.get(line, 0) | (1 << si)
with open(os.path.join(OUT, "rec.pkl"), "wb") as f:
    pickle.dump({"files": files, "cov_stages": stages, "cov": lines, "errors": errors,
                 "modules": sorted(m for m in sys.modules if m == "sqlglot" or m.startswith("sqlglot.")),
                 "cpu": time.process_time() - t0}, f)
print(f"recorded {sum(len(r) for r in records.values())} calls, errors {errors[:3]}, "
      f"cpu {time.process_time() - t0:.1f}s", file=sys.stderr)
