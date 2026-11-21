"""pytest plugin used by label.py.

* Records every failing node id to $BR_REPORT as JSON lines (controller only, so
  xdist worker reports and pytest 9 subtest reports all arrive exactly once).
  A failing subtest report carries its parent's node id, so the parent fails.
* If $BR_TESTS names a tests.json, runs the slowest known tests first so that
  xdist's wall time is close to max(longest test, total / workers).
"""
import json
import os

_out = None
_worker = False


def pytest_configure(config):
    global _worker
    _worker = hasattr(config, "workerinput")


def pytest_collection_modifyitems(session, config, items):
    path = os.environ.get("BR_TESTS")
    if not path:
        return
    secs = {t["id"]: t["seconds"] for t in json.load(open(path, encoding="utf-8"))["tests"]}
    order = {id(it): i for i, it in enumerate(items)}
    items.sort(key=lambda it: (-secs.get(it.nodeid, 0.0), order[id(it)]))


def _write(rec):
    global _out
    if _worker:
        return
    if _out is None:
        _out = open(os.environ["BR_REPORT"], "a", encoding="utf-8")
    _out.write(json.dumps(rec) + "\n")
    _out.flush()


def pytest_runtest_logreport(report):
    if report.failed:
        text = str(report.longrepr) if report.longrepr is not None else ""
        _write({"t": "fail", "id": report.nodeid, "when": report.when,
                "sub": type(report).__name__,
                "timeout": "Timeout" in text or report.when == "???" or "crashed" in text,
                "msg": text[-300:]})
    elif report.when == "call" and report.passed and type(report).__name__ == "TestReport":
        _write({"t": "pass", "id": report.nodeid})


def pytest_collectreport(report):
    if report.failed:
        _write({"t": "collect", "id": report.nodeid, "msg": str(report.longrepr)[-300:]})
