"""Pure memo caches of the pinned sqlglot, emptied before every profiled phase and
before the end-of-pass state check (shared by br_prof and br_cov_plugin)."""

from __future__ import annotations

import sys

MEMO_CACHES = [("sqlglot.generator", "_DISPATCH_CACHE")]
SCHEMA_CACHES = ("_type_mapping_cache", "_normalized_table_cache", "_normalized_name_cache",
                 "_find_cache")


def clear_memos() -> None:
    for module, attr in MEMO_CACHES:
        cache = getattr(sys.modules.get(module), attr, None)
        if isinstance(cache, dict):
            dict.clear(cache)
    # the global default schema (sqlglot.schema) lives across tests: its memo
    # caches of normalized names and types are emptied too
    schema = getattr(sys.modules.get("sqlglot"), "schema", None)
    for attr in SCHEMA_CACHES:
        cache = getattr(schema, attr, None)
        if isinstance(cache, dict):
            dict.clear(cache)
