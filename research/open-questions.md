# Open questions (ranked)

| # | Question | Why it matters | How to answer | Status |
| --- | --- | --- | --- | --- |
| Q1 | Does practice submission 1 (868.3 s) contain a miss, and from which rule? | A systematic miss costs 300 s on every such commit in the final | Ablation probes in `submissions.md` | open |
| Q2 | When can `test_max_nodes` (#1490, 28.8 s) be proven unaffected? | It sets a 28.8 s floor on every commit that selects it | Read the test; trace what it depends on; heavy-test gate | open |
| Q3 | Which changes really reach `test_execute_tpcds` (#1106, 21.5 s)? | It fails on some sample optimizer and executor edits, so it can't be dropped blindly | Coverage plus executor-path analysis | open |
| Q4 | Per-key attribution for dispatch tables (`FUNCTIONS`, `TRANSFORMS`, …) | Import-time code is impact's main source of over-selection | Record dict lookups per test at build time | open |
| Q5 | How different are maps built on Windows from those built in the Linux image? | Maps are built in the image; local development is on Windows | Build both and diff | open |
| Q6 | Is the mutant set representative of the hidden practice and final sets? | Validation is only as good as its distribution | Compare the kind mix and the failing-set sizes with the sample | partly answered 2026-11-21: m1 matches the kind mix and clean share by construction, but its code mutants break something 66% of the time against 44% in the sample, and one commit fails 1161 tests. Hidden-set edit shapes still unknown (`runs/20261121-validation-m1.md`) |
