# Blast Radius (builders hackathon)

We submit a Docker image that reads sqlglot patches (`/data/commits/*.patch`) and
`/data/tests.json`, then prints one JSON line per commit naming which tests to run
and how to split them over at most 8 workers. Lower score wins. `README.md` has the
track brief and `SCORING.md` has the exact rules. Read both before changing anything.

## The score in one screen

- Each commit costs the load of its busiest worker plus **2 s per worker**. `[]` costs 0.
- A commit that leaves out any failing test adds **300 s**. One miss costs as much as
  about 7 commits of running everything, so recall comes first and precision second.
- CPU time of the whole container is added, so building maps at build time is free
  and the run is charged. The run has 2 cores, no network and 180 s (270 s in the final).
- Sample: the baseline of every test on 3 workers scores 1210 s. The perfect-selection floor
  (`br.py oracle`) is 94 s. 12 of the 30 sample commits break nothing.
- The sample has only 30 commits. Practice and final commits are hidden. **Never tune to
  the sample.** Prefer a mechanism (coverage, imports, AST diffing) over sample-fit thresholds.

## Hard rules (a break disqualifies or rejects the run)

- **No answers looked up by patch.** No table from a patch, or from a kind of edit, to the
  tests it breaks. Maps built from the pinned commit (coverage, imports, fixtures) are fine.
  `sample/answers.json` is for scoring only; selector code must never read it.
- Build: downloads only from PyPI, npm, Docker Hub, GitHub releases at a fixed tag, and
  sqlglot at `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`. 4 cores, 8 GB, 10 min.
- Repo ≤ 20 MB. No submodules or LFS. Committed maps are plain files.
- Run: read-only disk, 64 MB `/tmp`, user `nobody` (make files world-readable), ≤256 procs.
- stdout holds answer lines only, at most 2 MB. Logs go to stderr.
- Keep the Dockerfile's base image digest. It decides which sqlglot tests run.

## Branch model

- `main` is the top. It holds the best *verified* submission plus the harness.
- Every strategy is a sibling branch `strat/<name>`, cut from `main` (or from any sibling).
  There is no hierarchy among siblings. Cherry-pick, merge or rebase freely between them,
  and credit the source in the commit message.
- A branch is promoted to `main` (fast-forward or merge) only when a **docker** eval beats
  main's best docker total on the board, with the reasoning in its `STRATEGY.md`.
  The lead (the human, or the orchestrating session) does the promotion, not strategy agents.

## Harness (`harness/br.py`, stdlib only)

```sh
python harness/br.py new <name> [--from REF]  # branch strat/<name>, worktree .worktrees/<name>
python harness/br.py list                     # branches, worktrees, best score each
python harness/br.py eval -v -m "note"        # fast: host python, wall time as the CPU stand-in
python harness/br.py eval --docker -m "note"  # real: scorer's flags, cgroup CPU (needs Docker running)
python harness/br.py board [--all]            # shared leaderboard across all worktrees
python harness/br.py oracle                   # lower bound on sample (yardstick only)
python harness/br.py sqlglot                  # shared pinned sqlglot clone at .cache/sqlglot
```

- Run `eval` from inside the worktree you are scoring. It always scores with main's
  `score.py` and logs to `.git/br-harness/leaderboard.jsonl`, which every worktree shares.
- Local mode runs `python selector.py` with `BR_DATA=<data dir>`. Keep that entry point
  working for fast loops, even if the Docker image runs more (for example a prebuilt map).
- `--data DIR --answers FILE` scores against another data set, such as a synthetic
  mutant set. Use held-out data to check that a strategy generalizes.
- Commit before you log a result you want to claim. Dirty runs show `*` on the board.
- `.cache/sqlglot` is shared and read-only. Copy it, or `git -C .cache/sqlglot worktree add`,
  before running mutation experiments.

## Working as a strategy agent

1. `python harness/br.py new <name>`, then work only inside `.worktrees/<name>`.
2. Write `STRATEGY.md` on the branch: the idea, what you measured, what failed. Keep it current.
3. Loop: change, then `eval -v`, then commit. Run `eval --docker` before you claim a result.
4. Never edit `main`, another agent's worktree, or the shared sqlglot cache.
5. Report the branch, sha, docker total, misses, and what to steal from it.

## Strategy backlog (claim one by creating its branch)

- `coverage`: per-test line coverage of sqlglot at build time, mapping changed lines to tests.
- `imports`/`files`: map each module to the test files that reach it, as a coarse safe fallback.
- `ast-diff`: parse the patch and skip pure no-ops. Careful: a comment-only change is not
  promised to be harmless, and docstrings and `__doc__` can matter.
- `test-patch`: patches that touch `tests/` or fixtures (`tests/fixtures/*.sql`, the
  `optimizer` fixtures) map directly to the tests that read those files.
- `packing`: best worker count and split for a chosen set (the 2 s per worker vs the max load).
- `run-tests`: actually run the candidate tests in the container to confirm failures and
  prune. This costs CPU, so it only pays off when uncertain sets are large.
- `mutants`: generate a synthetic held-out set (mutate pinned sqlglot, label it by running
  pytest under `requirements.lock`) so everyone can validate beyond the 30 samples.
