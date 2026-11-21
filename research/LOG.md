# Run log (append-only, newest last)

## 2026-11-20: local session (lead + 4 Opus strategy agents, Windows host)

- Initial commit of the starter kit and `SCORING.md`. Built the harness (`harness/br.py`: worktrees,
  local and docker eval, shared board, oracle, maps) and the layered selector
  (`blast/` patch → triage → impact → pack).
- Baselines on sample: starter 1210.5 s simulated; oracle floor 94.4 s.
- `strat/pack`: per-commit worker count plus LPT, local search and capped branch and bound.
  At the lower bound on 167 benchmark sets. All tests now cost 36.817 s per commit (4 workers).
- `strat/triage`: static fixture-to-test map, per-function AST diff of test modules, skip of
  AST-identical source edits. Sample 785.2 s locally, 0 misses.
- `int/2026-11-20` = pack + triage, fast-forwarded to `main` `a9e62c0`. Docker on sample:
  **720.2 s** (717.4 simulated + 2.75 CPU), 0 misses, build about 2 min, run 2.7 s.
- Practice board for `a9e62c0`: **868.3 s**. Whether it contains a miss is unresolved; see
  `submissions.md`.
- `strat/impact` (not on `main`): v1b sample 635.4 s locally, 0 misses, triage at its default.
- `strat/mutants`: generator plus full-suite labeller in progress. The data set lives in the
  local `.cache` only for now.
- Next: an impact research cluster in the cloud (`cloud/impact-cluster.md`, tag `cloud-base-1`).
