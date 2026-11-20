"""Layer 1: parse a unified diff into file changes and hunks.

Owner: strat/triage (shared by every layer; extend fields, never remove them).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str  # text after the second @@, often the enclosing def/class
    removed: list[tuple[int, str]] = field(default_factory=list)  # (old line no, text)
    added: list[tuple[int, str]] = field(default_factory=list)  # (new line no, text)

    def old_lines_touched(self) -> set[int]:
        """Old-file line numbers a coverage map can match: removed lines, or the
        neighbours of a pure insertion."""
        if self.removed:
            return {n for n, _ in self.removed}
        return {max(self.old_start, 1), self.old_start + 1}


@dataclass
class FileChange:
    old_path: str | None  # None when the file is new
    new_path: str | None  # None when the file is deleted
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


@dataclass
class Patch:
    commit: str
    files: list[FileChange]
    text: str


def _strip(path: str) -> str | None:
    path = path.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    return path[2:] if path[:2] in ("a/", "b/") else path


def parse(commit: str, text: str) -> Patch:
    files: list[FileChange] = []
    current: FileChange | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" ")
            current = FileChange(_strip(parts[2]) if len(parts) > 2 else None,
                                 _strip(parts[3]) if len(parts) > 3 else None)
            files.append(current)
            hunk = None
        elif current is None:
            continue
        elif hunk is None and line.startswith("--- "):
            current.old_path = _strip(line[4:])
        elif hunk is None and line.startswith("+++ "):
            current.new_path = _strip(line[4:])
        elif line.startswith("Binary files"):
            current.binary = True
        elif (m := HUNK.match(line)):
            hunk = Hunk(int(m[1]), int(m[2] if m[2] is not None else 1),
                        int(m[3]), int(m[4] if m[4] is not None else 1), m[5].strip())
            current.hunks.append(hunk)
            old_no, new_no = hunk.old_start, hunk.new_start
        elif hunk is not None:
            if line.startswith("-"):
                hunk.removed.append((old_no, line[1:]))
                old_no += 1
            elif line.startswith("+"):
                hunk.added.append((new_no, line[1:]))
                new_no += 1
            elif line.startswith("\\"):
                pass  # "\ No newline at end of file"
            else:
                old_no += 1
                new_no += 1
    return Patch(commit, files, text)
