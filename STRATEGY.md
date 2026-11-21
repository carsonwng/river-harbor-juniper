# strat/impact: per-test coverage + AST placement

## Hypothesis
A changed line can only change a test's outcome if the test runs the code the line
belongs to. Function-body edits map exactly through per-test line coverage at the
*statement* level (not the line: `a and b` -> `a or b` on a continuation line must
count tests that ran the statement's first line). Import-time code (module and class
bodies: tables, class attributes, decorators) has no per-test coverage, so it is
handled by *names*: the tests that ran code loading the bound name, intersected
with the tests that used the enclosing class (instantiated it or a subclass, ran a
method of it or a subclass, or referenced it by name). If import-time code itself
reads the name (metaclass, other class bodies), the value propagates and we take
every user of the class/module instead.

## Build (build/impact.py)
1. Import probe: per sqlglot module, lines executed during its import (fork on
   POSIX, subprocess elsewhere). "core" = `import sqlglot`.
2. pytest -n 4 with build/impact_lib/br_cov_plugin.py: sys.monitoring LINE events
   (DISABLE after the first hit, restart_events per phase), PY_START on `__init__`
   to record instantiated classes (whole MRO). Setup/teardown coverage counts for
   the whole class (setUpClass), collection coverage for the whole module.
   ProcessPoolExecutor is swapped for an in-process thread pool that round-trips
   tasks through pickle (so serde/pickling and child work is attributed).
3. Postprocess to bitsets over the build-time test order.

## Runtime (blast/impact.py)
Rebuild new file from pinned source + hunks; AST-identical -> nothing; no compile ->
all. Each removed/added line is placed in the old/new AST (see module docstring).
Anything unexpected -> all tests.

## Measurements (local, Windows)
- coverage run: 163 s (plain suite 110 s on -n 4 here); probe 190 s on Windows
  (subprocess per module; POSIX forks instead).
- maps 6.6 MB (built in the image, not committed).
- sample v0: 0 misses, simulated 655.6 (with triage default = fixtures fall back
  to all tests).

## Windows vs Linux
- Probe uses os.fork on Linux, a subprocess per module on Windows (slow).
