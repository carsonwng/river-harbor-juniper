"""Record which sqlglot modules each test module loads when it is imported.

Usage: python br_test_imports.py ROOT OUTFILE TESTMODULE...
The parent imports `sqlglot`; every test module is then imported in its own
os.fork() (pristine apart from core sqlglot), and the sqlglot modules that appear
in sys.modules are recorded. Catches `from sqlglot.dialects import Snowflake`,
which loads sqlglot.dialects.snowflake through the package's lazy __getattr__ and
which no static reading of import statements sees. If an edit makes one of those
modules raise on import, the test file fails at collection, so all of its tests
fail. Writes {"<test module>": ["sqlglot.x", ...]} to OUTFILE. POSIX only.
"""

import importlib
import json
import os
import sys

root, outfile, modules = sys.argv[1], sys.argv[2], sys.argv[3:]
sys.path.insert(0, root)
os.chdir(root)
os.environ.setdefault("SKIP_INTEGRATION", "0")
import sqlglot  # noqa: E402,F401

result = {}
for module in modules:
    rfd, wfd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(rfd)
        before = set(sys.modules)
        try:
            importlib.import_module(module)
        except BaseException:  # noqa: BLE001
            pass
        loaded = sorted(m for m in set(sys.modules) - before if m.startswith("sqlglot"))
        os.write(wfd, json.dumps(loaded).encode())
        os._exit(0)
    os.close(wfd)
    data = b""
    while True:
        chunk = os.read(rfd, 1 << 16)
        if not chunk:
            break
        data += chunk
    os.close(rfd)
    os.waitpid(pid, 0)
    try:
        result[module] = json.loads(data.decode() or "[]")
    except ValueError:
        result[module] = None
with open(outfile, "w", encoding="utf-8") as handle:
    json.dump(result, handle)
