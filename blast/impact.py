"""Layer 3: map deferred source changes to the tests that can fail.

Owner: strat/impact.

select(changes, ctx) gets every FileChange triage deferred and returns the test
numbers to run. Build-time maps go in ctx.layer_maps("impact"), produced by
build/impact.py. Default: every test (safe).
"""

from __future__ import annotations

from .context import Context
from .patch import FileChange


def select(changes: list[FileChange], ctx: Context) -> set[int]:
    if not changes:
        return set()
    return ctx.all_tests
