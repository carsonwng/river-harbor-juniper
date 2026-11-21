# strat/runcpu: cut the selector's run-time CPU without changing a selection

Branch `strat/runcpu`, cut from `int/night-1948` (origin/main + origin/strat/impact-base),
so the impact layer and its map loading are included.

## Rule

stdout must be byte-identical to the parent for every commit. Checked by `cmp` against the
parent image's stdout on the sample after every step. Every equivalence the change relies on
was also checked off-sample (below).

## Profile (parent, in the built image, sample, 30 commits)

| Item | CPU |
| --- | --- |
| `python -c pass` (interpreter start) | 13-20 ms |
| imports (`-X importtime`: dataclasses/inspect, json, re, blast.*) | about 30 ms |
| whole selector, in-process (`getrusage` children) | 0.73-0.92 s |
| cgroup total reported by `eval --docker` | 1.177 s |

cProfile (inflated, but the shape is clear): `ast.dump` 0.93 of 1.52 s. Triage
(`_source_noop`) and impact (`_change_bits`) each parse the pinned file and the patched
file and compare `ast.dump(old) == ast.dump(new)`, formatting every node of large modules
(parser.py is 424 KB, about 19 MB of AST) into strings. Then `compile` (0.28 s, which
includes those parses plus impact's `compile(new_text)` and `compile(old_source)` for
code lines), then file I/O. Maps (JSON) load in about 10 ms and were not worth changing.

## Changes

1. `blast/astcache.py` (new, shared, no selection logic):
   - `parse(text)`: `ast.parse` memoised by source text, LRU bounded to 4M source chars
     (about 180 MB of AST). Triage parses, impact reuses the same trees. Exceptions are
     never cached, so failures behave as before.
   - `same(a, b)`: exactly `ast.dump(a) == ast.dump(b)` (include_attributes=False) by an
     iterative walk that stops at the first difference and builds no strings. Leaf values
     compare by type plus repr, as dump renders them (so `1`/`True`/`1.0` and
     `0.0`/`-0.0` still differ). Checked against `ast.dump` equality on 79,504 pairs:
     random node pairs from every sqlglot source and test file, single-token source
     mutations, and a constant/syntax edge-case matrix. 0 disagreements.
   - `same_text(old, new)`: memoised, so impact gets triage's answer for free.
2. `blast/triage.py`: `_parse` uses `astcache.parse`; the two whole-module dump comparisons
   use `astcache.same`/`same_text`; `_read_src` memoised (read-only disk).
   The per-function `_dump` in `_units` (test-module diffs) is unchanged.
3. `blast/impact.py`: parses through `astcache`; AST-identical check uses `same_text`;
   `compile(new_tree)` instead of `compile(new_text)` (no second parse); `FileInfo` code
   lines from `compile(parse(old))`, memoised per file; pinned source reads memoised.
   `compile(tree)` vs `compile(text)` checked on 409 inputs (every sqlglot module plus
   variants with statements that parse but fail to compile: `return`/`yield`/`await`
   outside functions, `nonlocal`, `from __future__ import braces`, ...): same raise or
   not, same line tables.
4. `Dockerfile`: `python -m compileall -q /app/blast` at build (the run's disk is
   read-only, so every run recompiled blast/*.py); `CMD ["python", "-I", "-S", ...]`
   (selector uses only the stdlib; skips site and env). The local harness entry point
   `python selector.py` is unchanged.

## Measurements

| | parent | runcpu |
| --- | --- | --- |
| in-image selector CPU (getrusage, several runs) | 0.73-0.92 s | 0.43-0.65 s |
| docker eval cgroup CPU | 1.177 s | see board |
| sample stdout | md5 aebdcdd9... | identical |

Timings are noisy: other agents share the laptop, and `/data` is a Windows bind mount
whose file I/O (open/read/stat about 0.25 s profiled) the scorer won't have.

## Not done / dead ends

- Precomputing pinned-file ASTs at build time (pickle): unpickling a 19 MB AST is not
  cheaper than `ast.parse`, which is C. The in-run cache gets the same benefit for files
  touched by several commits.
- Token-stream comparison as a shortcut for AST equality: not exactly equivalent
  (`'a' 'b'` vs `'ab'`, parentheses), so rejected.
- Map format (JSON to pickle/marshal): maps load in about 10 ms on the sample; no gain
  worth a format change owned by another layer.
- What is left is mostly parsing each patched file once (unavoidable while the layers need
  the full new AST) and I/O.
