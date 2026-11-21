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

## 2026-11-20/21: gap-mix (practice-mix forensics; strat/gap-mix)

- Practice has 21–23 source-edit commits (sample 19). Source edits cost 7.1–9.7 s each on
  impact-base (sample 8.4, m1 10.9, synthetic 10.9). The sample is the cheap outlier.
- relprof's map was present on practice. It saves 0.22 s per source edit off the sample.
- Recall: import failures (`{*x}` → `{+x}` and the like) break about 2.5% of synthetic commits
  and 2 of 42 m1 commits. impact-base misses them. Fixed by a run-time import check.
- Recall: cross-test caches (`_DISPATCH_CACHE`) make per-test coverage under-select. Verified
  miss on a `_build_dispatch` mutant. Not fixed.
- Cut: an import-time lambda-body-only edit selects only that lambda's coverage (synthetic −2.5%,
  verified by full-suite labels on 8 commits). Details are in strategies/gap-mix.md.

## 2026-11-21 strat/r2-ledger
- Exact cost ledger of f89594d (research/r2-ledger.md): practice-like excess 5.05 s/commit;
  tpcds 2.27, class-level tables 1.35, non-tpcds fn-body 0.59, tpc-h records 0.53, core
  fallback 0.19 (typing/__init__ only; tokens/trie/tokenizer_core/__main__ absent from
  m1/m1-extra/local). Floor empty.
- Typing-metadata cut (strategies/r2-ledger.md): per-test (table, key) lookups of
  EXPRESSION_METADATA + entry-level import-state diff; m1 -32.8 s, syn640 -0.23 s/commit,
  0 misses, 42 run-verified cases.
