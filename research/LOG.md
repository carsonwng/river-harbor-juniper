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

## 2026-11-21: cloud session, held-out validation set m1 (`validation/m1`)

- Labeller now runs pytest inside the Dockerfile's base image with `requirements.lock`
  (`validation/label.py --docker`). Agreement with `sample/answers.json`: **30 / 30 exact**.
- `validation/sets/m1`: 64 labelled mutants in harness format with the sample's mix (code 33,
  fixture 17, comment 8, multi 6; 25 break nothing, 39%). `validation/sets/m1-extra`: 22 more
  breaking commits. 928 KB together. Oracle floor: m1 146.9 s, m1-extra 102.0 s.
- About 140 s per full-suite run on the 4-core cloud box; 86 labels took 3 h 31 min.
- Caveats: `7lc8` has a borderline timeout label on #1106; no mutant breaks #1490; each commit
  labelled once. Report: `runs/20261121-validation-m1.md`.
