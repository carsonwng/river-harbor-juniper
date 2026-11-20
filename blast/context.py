"""Shared run context: the test list plus any maps built at image build time.

Maps live in BR_MAPS (default /app/maps in the image, `.maps/` in a worktree for
local runs). Each layer reads only its own subdirectory, maps/<layer>/.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Context:
    tests: list[dict]
    maps_dir: Path
    by_file: dict[str, list[int]] = field(default_factory=dict)
    by_id: dict[str, int] = field(default_factory=dict)
    seconds: dict[int, float] = field(default_factory=dict)

    @property
    def all_tests(self) -> set[int]:
        return set(self.seconds)

    def layer_maps(self, layer: str) -> Path:
        return self.maps_dir / layer


def default_maps_dir() -> Path:
    if os.environ.get("BR_MAPS"):
        return Path(os.environ["BR_MAPS"])
    image = Path("/app/maps")
    return image if image.exists() else Path(__file__).resolve().parent.parent / ".maps"


def load(data_dir: Path) -> Context:
    tests = json.loads((data_dir / "tests.json").read_text("utf-8"))["tests"]
    ctx = Context(tests=tests, maps_dir=default_maps_dir())
    for test in tests:
        ctx.by_file.setdefault(test["file"], []).append(test["number"])
        ctx.by_id[test["id"]] = test["number"]
        ctx.seconds[test["number"]] = test["seconds"]
    return ctx
