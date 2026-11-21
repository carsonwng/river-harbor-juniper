# Experiments

| ID | Layer | Branch | Idea | Best result (set/mode) | Misses | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E1 | pack | `strat/pack` | Per-commit worker count by lower bound; LPT, then move/swap, then capped branch and bound | at the lower bound on 167 sets; full set 36.817 s | 0 | merged (`a9e62c0`) | `strategies/pack.md` |
| E2 | triage | `strat/triage` | Static fixture-to-test map; per-function AST diff for test modules; skip AST-identical source edits | sample/local 785.2 s | 0 | merged (`a9e62c0`) | `strategies/triage.md`. Comment skip checked by 654 injected comments with the full suite still passing |
| E3 | impact | `strat/impact` | Per-test `sys.monitoring` statement coverage, AST placement of edits, class-usage handling of import-time code | sample/local 635.4 s (triage at its default) | 0 | promising | Build: coverage run about 163 s, maps 6.6 MB, built in the image |
| E4 | validation | `strat/mutants`, `validation/m1` | Synthetic mutant patches labelled by the full suite in the labelling image | labeller 30/30 exact on sample; `validation/sets/m1` 64 commits (39% clean), `m1-extra` 22 | – | candidate | `runs/20261121-validation-m1.md`. Validation only, never read by the selector. Mutants more lethal than sample edits; none breaks #1490 |
