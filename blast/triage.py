"""Layer 2: triage each changed file before impact analysis.

Owner: strat/triage.

For every FileChange, return one of:
  Decision.skip()          the change provably cannot fail a test
  Decision.tests({...})    exactly these tests can fail (e.g. an edited test or fixture)
  Decision.defer()         a source change; let the impact layer decide

Default: defer everything (safe).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .context import Context
from .patch import FileChange


@dataclass
class Decision:
    kind: str  # "skip" | "tests" | "defer"
    tests: set[int] = field(default_factory=set)
    reason: str = ""

    @staticmethod
    def skip(reason: str = "") -> "Decision":
        return Decision("skip", reason=reason)

    @staticmethod
    def select(tests: set[int], reason: str = "") -> "Decision":
        return Decision("tests", set(tests), reason)

    @staticmethod
    def defer(reason: str = "") -> "Decision":
        return Decision("defer", reason=reason)


def classify(change: FileChange, ctx: Context) -> Decision:
    return Decision.defer("default")
