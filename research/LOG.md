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

## 2026-11-21: impact research cluster (cloud)

- Report: [`runs/20261121-impact-cluster.md`](runs/20261121-impact-cluster.md). All numbers are
  local; Docker was not available in the cloud environment.
- Baseline `strat/impact-base` (`main` + `strat/impact`): sample 177.8 s, 0 misses. The
  recorded 635.4 s predates the last impact commit and had triage off.
- **The baseline is unsound.** On the held-out set m1 (70 commits) it scores 1280.1 s with
  2 misses: class-body edits that break dialect imports. Probe mutants also showed misses
  behind per-process caches (`_DISPATCH_CACHE`, `Dialect._classes`).
- Four experiments, each a draft PR: isolate (PR 3), relprof (PR 2), attrs (PR 1), and their
  combination (PR 6).
- **Candidate: `strat/impact-combo-sound` `7a82a66`.** Sample 103.6 s, m1 570.3 s, 0 misses
  on both (oracle 94.4 s and 220.5 s).
- Open before submitting: a docker eval (build is about 282 s for the impact step alone), and
  the human decision on whether relprof is within the rules (Q7).
