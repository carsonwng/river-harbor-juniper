"""Blast Radius selector, in four independently owned layers:

    patch.parse -> triage.classify (per file) -> impact.select (deferred files) -> pack.split

Each layer has one owning branch (see CLAUDE.md). Change a layer's internals
freely; change its interface only on main.
"""
