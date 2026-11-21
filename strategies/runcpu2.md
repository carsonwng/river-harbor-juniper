# strat/runcpu2: cut the selector's run CPU on submission 6 without changing a selection

Branch `strat/runcpu2`, cut from `int/night-0405-expr` @ `ae47472` (practice submission 6,
213.9 s = 210.365 s simulated + about 3.55 s CPU). CPU counts 1:1 in the score.

## Rule

stdout must be byte-identical to ae47472's on every set. After every step the selector's
stdout from the ae47472 image (`br-int-night-0405-expr`, checked file-for-file against
the commit) was `cmp`-ed with the new code's stdout on sample, m1, m1-extra and
local-20261120 (no syn-import16 set exists in main's validation/sets/). Every
equivalence the code relies on was also checked off-sample (below).

## Profile (ae47472, in the image, `--cpus 2`, cgroup CPU)

| set | commits | cgroup CPU |
| --- | --- | --- |
| sample | 30 | 3.65 s |
| m1 | 64 | 11.97 s |
| m1-extra | 22 | 3.35 s |
| local-20261120 | 32 | 2.91 s |

m1 split: selector process 7.9 s, children 4.0 s (18 import checks at 0.12-0.35 s, one
expression state diff at 0.75 s). Interpreter start + imports + maps: about 0.05 s.
cProfile of the selector process on m1 (29.5 s profiled):

- `copy.deepcopy` 12.3 s cumulative, all from `impact._blank_bodies` (14.2 s cum.):
  `_runs_at_import` deep-copied the whole pinned and patched module (expressions/*.py,
  parser.py: hundreds of thousands of nodes) to blank function and lambda bodies, then
  `ast.dump`-ed both copies. Also `_lambda_only_bits` (deepcopy of statement pairs).
- `ast.dump` (`_format`) 7.9 s: the copies above plus the whole-module equality checks in
  impact and triage, and the statement-level comparisons.
- `compile` 1.6 s: impact compiled the pinned file for code lines on every commit and
  the patched text (a second parse) for the compile check.
- children: `import sqlglot` is about 0.14 s of each import-check child; 18 of 22 checks
  on sample+m1 are module lists (edited module not in sqlglot's core import).

## Changes (all CPU-only)

1. `blast/astcache.py` (new; ported from strat/runcpu 79a3b3e and extended):
   - `parse(text)`: `ast.parse` memoised by text (LRU, 3M source chars). Triage and
     impact share trees. No caller mutates them (`_compare_edits` swaps fields and
     restores in a `finally`; `_header_copy` copies).
   - `same(a, b)`: exactly `ast.dump(a) == ast.dump(b)` by a walk that stops at the first
     difference (leaves by type + repr, as dump renders them).
   - `same(a, b, funcs=True, lambdas=keep)`: exactly `ast.dump(_blank_bodies(a, keep)) ==
     ast.dump(_blank_bodies(b, keep))` without copying: a function body counts as
     `[Pass()]` on both sides, a lambda body whose line is not in `keep` counts as
     `Constant(None)` (so an unblanked `lambda: None` still equals a blanked one, as in
     the dump), and a replaced body is never walked. `lambdas=frozenset()` is
     `_lambda_only_bits`' blank-every-lambda copy.
   - `code_lines(rel, text)`: code-object line numbers, memoised, compiled from the
     cached tree.
   - Checked in the image (Python 3.13) against the old deepcopy + `ast.dump` code on
     18,382 pairs: every sqlglot source and test file against single-token mutants, for
     keep = none / all-blank / random half of the lambda lines, funcs on/off, plus every
     top-level statement pair and a 25x25 constant/lambda/def edge matrix. 0 disagreements.
2. `blast/impact.py`: every `ast.dump(x) ==/!= ast.dump(y)` is `astcache.same`; the two
   deepcopy blanks use the virtual blanking above; `compile(new_tree)` instead of
   `compile(new_text)`; `FileInfo.code_lines` from `astcache.code_lines`.
   `compile(tree)` vs `compile(text)`: same raise or not and same line tables on 443
   inputs (every sqlglot module, 360 line mutants with stray `return`/`yield`/`break`/
   dedent, and a matrix of compile-only errors: `nonlocal`, `from __future__ import
   braces`, late `__future__`, duplicate args, `return` outside a function, ...).
3. `blast/triage.py`: `_parse` through `astcache.parse`; the two whole-module dump
   equalities through `astcache.same`. The per-function `_dump` keys of `_units` stay.
4. `blast/impact_import.py`: an import server for module-list checks. A server child
   (same command `python -I impact_import.py`, same environment, same setup: sys.path,
   no bytecode writes, override finder first on meta_path, stdout to stderr) imports
   `sqlglot` once, the first thing a fresh child does. Each check forks it; the fork
   installs the overrides and imports `["sqlglot"] + sorted(modules)` exactly as a
   fresh child does after its own `import sqlglot`. If any edited module is in
   `sys.modules` after the pristine import, or the pristine import even looked it up
   (the finder records every lookup, so a failed optional import counts), the fork
   reports a conflict and the check runs in a fresh child as before. Any server error or
   timeout kills the server's session and falls back to fresh children for the rest of
   the run. `modules=None` (an edit inside the core import) always uses a fresh child.
   Checked: server answer == fresh answer on all 34 checks of the four sets (including
   m1's 8owm hive import crash) and on 58 synthetic overrides (29 modules incl. core
   ones, appended `raise ValueError` or a harmless assignment): 0 mismatches,
   38 served, 20 conflicts. `BR_IMPORT_SERVER=0` turns it off.
5. `Dockerfile`: `python -m compileall -q /app/blast` at build (the run's disk is
   read-only, so every run recompiled blast/*.py) and `CMD ["python", "-I", "-S",
   "/app/selector.py"]`. The local `python selector.py` entry point is unchanged.

## Results

`python harness/br.py verify strat/runcpu2` at `f89594d`: **VERIFY PASS**, 0 misses on
every set, simulated time equal to ae47472's on every set (the selections are the same).

cgroup CPU, docker, `--cpus 2`. The ae47472 column is its own verify on the board
(04:25), with my same-session run of the ae47472 image in brackets where I made one.

| set | ae47472 | f89594d (verify) | saved |
| --- | --- | --- | --- |
| sample (30) | 4.30 (3.65) | 2.40 | 1.3-1.9 s |
| m1 (64) | 12.05 (11.97) | 4.77 | 7.2 s |
| m1-extra (22) | 3.54 (3.35) | 2.20 | 1.2-1.3 s |
| local-20261120 (32) | 3.16 (2.91) | 1.54 | 1.4-1.6 s |
| gap-tests-tpc (20) | 0.66 | 0.54 | 0.1 s |
| syn-import16 (16) | 4.02 | 3.00 | 1.0 s |

Steps, each measured by running the new blast/ over the ae47472 image (cgroup CPU, s):

| step | sample | m1 | m1-extra | local |
| --- | --- | --- | --- | --- |
| ae47472 | 3.65 | 11.97 | 3.35 | 2.91 |
| + astcache (no deepcopy/dump) | 2.55 | 6.67 | 2.67 | 2.09 |
| + import fork server | 2.02 | 4.54 | 2.03 | 1.53 |
| + compile from trees, `-I -S` | 2.06-2.17 | 4.50-4.85 | 2.00-2.07 | 1.38-1.46 |

Timings move by about ±0.2 s between runs on the shared laptop. The last step is within
that noise and is kept because it is verified equivalent.

**stdout cmp**: the built images `br-int-night-0405-expr` (ae47472) and
`br-strat-runcpu2` (f89594d), each run through its own CMD, give byte-identical stdout on
sample, m1, m1-extra, local-20261120, gap-tests-tpc and syn-import16.

Practice estimate: submission 6 spent about 3.55 s of CPU, close to the sample's
3.65-4.3 s. If practice behaves like the sample, f89594d should spend about 2.0-2.4 s,
which saves about 1.2-1.5 s on the practice board (about 212.5 s instead of 213.9 s).

Build: one `RUN` step gained `compileall` of blast/ (under a second). Otherwise the build
is the same as ae47472's.

## Not done / dead ends

- The expression state diff child (`build/impact_lib/br_state.py diff`, 0.7-1.0 s, about
  once per set) imports all of sqlglot with an edited core module and snapshots every
  class: no pristine state to share, and its snapshot code is the build's. Left alone.
- Pre-importing more than `import sqlglot` in the server (dialects not on the list)
  would add registry entries the fresh child never had: not exactly equivalent.
- Skipping the whole-module `same()` walk by comparing only statements whose source text
  differs: not provably equal to the dump (future imports change parsing), so rejected.
