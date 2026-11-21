# Experiments

| ID | Layer | Branch | Idea | Best result (set/mode) | Misses | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E1 | pack | `strat/pack` | Per-commit worker count by lower bound; LPT, then move/swap, then capped branch and bound | at the lower bound on 167 sets; full set 36.817 s | 0 | merged (`a9e62c0`) | `strategies/pack.md` |
| E2 | triage | `strat/triage` | Static fixture-to-test map; per-function AST diff for test modules; skip AST-identical source edits | sample/local 785.2 s | 0 | merged (`a9e62c0`) | `strategies/triage.md`. Comment skip checked by 654 injected comments with the full suite still passing |
| E3 | impact | `strat/impact` | Per-test `sys.monitoring` statement coverage, AST placement of edits, class-usage handling of import-time code | sample/local 635.4 s (triage at its default) | 0 | promising | Build: coverage run about 163 s, maps 6.6 MB, built in the image |
| E4 | validation | `strat/mutants` | Synthetic mutant patches labelled by the full suite | in progress | – | promising | The data set is not yet committed |
