# Open questions (ranked)

| # | Question | Why it matters | How to answer | Status |
| --- | --- | --- | --- | --- |
| Q1 | Does practice submission 1 (868.3 s) contain a miss, and from which rule? | A systematic miss costs 300 s on every such commit in the final | Ablation probes in `submissions.md` | open |
| Q2 | When can `test_max_nodes` (#1490, 28.8 s) be proven unaffected? | It sets a 28.8 s floor on every commit that selects it | Read the test; trace what it depends on; heavy-test gate | open |
| Q3 | Which changes really reach `test_execute_tpcds` (#1106, 21.5 s)? | It fails on some sample optimizer and executor edits, so it can't be dropped blindly | Coverage plus executor-path analysis | open |
| Q4 | Per-key attribution for dispatch tables (`FUNCTIONS`, `TRANSFORMS`, …) | Import-time code is impact's main source of over-selection | Record dict lookups per test at build time | open |
| Q5 | How different are maps built on Windows from those built in the Linux image? | Maps are built in the image; local development is on Windows | Build both and diff | open |
| Q6 | Is the mutant set representative of the hidden practice and final sets? | Validation is only as good as its distribution | Compare the kind mix and the failing-set sizes with the sample | open |

## Build-time budget (measured 2026-11-20)

Question: how much of the scorer's build limit (4 cores, 8 GB, 10 min) does our image use, against the
team target of **420 s**? Status: **answered** for `int/night-1948` (`1a5f961`).

**Setup.** buildx builder `br-scorer` (docker-container driver, `cpuset-cpus=0-3`, `memory=8g`,
`memory-swap=8g`). Checked from inside a build step: `cpuset.cpus.effective` = `0-3`, `nproc` = 4,
`sched_getaffinity` = {0,1,2,3}. A CPU burn of 8 processes x 2 s took 4.47 s of wall time, against 2.42 s for 4 processes,
so 4 CPUs is a real limit. The builder container has `memory.max` = 8 GiB and
`memory.swap.max` = 0, and the per-step cgroups are nested under it (they show `max` themselves).
Caveat: host CPUs 0-3 are not reserved. Other unpinned containers (label jobs, evals) can share them.

**Cold builds (`--no-cache`, base image already in the builder, includes `--load` export):**

| Step | Run 1 (quiet host) | Run 2 (contended) |
| --- | --- | --- |
| sqlglot tarball download + extract | 1.6 s | 2.3 s |
| `pip install -r requirements.lock` | 42.0 s | 51.6 s |
| impact: import probe (183 modules) | 4.0 s | 5.3 s |
| impact: coverage run (5 workers) | 95.9 s | 151.9 s |
| impact: postprocess | 8.9 s | 15.2 s |
| triage map | 9.1 s | 14.8 s |
| whole `run_all.py` step | 120.1 s | 189.0 s |
| export layers + load into docker | 26.8 s | 40.9 s |
| **total build** | **193.3 s** | **285.4 s** |
| headroom to 420 s | 226.7 s | 134.6 s |
| eval: simulated / misses | 177.819 s / 0 | 184.527 s / 0 |

Run 2 shared the host with another eval container and label jobs, and every step ran about 1.5x slower.
Use run 1 as the estimate for a dedicated 4-core machine and run 2 as a pessimistic bound.
The coverage run is the step that matters (about 50% of the build). Spend extra build time there.

**Maps are not reproducible build to build.** Run 2 produced 10258 distinct coverage sets instead of 10257,
and simulated rose by 6.7 s with 0 misses. Coverage depends on timing or worker scheduling, so the score
of one image build is not exactly the score of the next. Open: find which set differs (diff `maps/impact`
of the two builds) and make the coverage step deterministic.

Not counted: the base-image pull (cached in the builder here) and the scorer's own export/push.
Allow about 10-30 s for them.

**Reproduce** (from the worktree to measure, through the shared CPU budget):

```sh
BUILDKIT_PROGRESS=plain python harness/br.py cpu -n 4 --label buildtime -- \
  python harness/br.py eval --docker --scorer-limits --cold -m "cold build timing"
```

`build took …s` is printed and logged as `build_seconds` / `build_mode=cold-4cpu`. A warning is printed above 420 s.
