"""pytest plugin: run the slowest tests first (durations from an earlier pass).

Loaded with `-p br_order` when BR_ORDER_FILE names a JSON object nodeid -> seconds
(written by build/impact.py from the core coverage pass). With xdist every worker
sorts its collection the same way, so the scheduler sees one consistent order, and
the long tests start first instead of leaving three workers idle at the end. Only
the order changes; every test still runs.
"""
import json
import os


def pytest_collection_modifyitems(session, config, items):
    path = os.environ.get("BR_ORDER_FILE")
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as handle:
            secs = json.load(handle)
    except Exception:  # noqa: BLE001
        return
    # keep each test class together (setUpClass runs once): classes by their
    # slowest test, then the tests of a class slowest first
    order = {id(it): i for i, it in enumerate(items)}
    group = {}
    for it in items:
        key = it.nodeid.rsplit("::", 1)[0]
        group[key] = max(group.get(key, 0.0), float(secs.get(it.nodeid, 0.0)))
    items.sort(key=lambda it: (-group[it.nodeid.rsplit("::", 1)[0]], it.nodeid.rsplit("::", 1)[0],
                               -float(secs.get(it.nodeid, 0.0)), order[id(it)]))
