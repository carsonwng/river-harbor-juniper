# Cloud run: impact round 2 (scheduled routine, 01:30 America/Toronto)

This prompt is sent as-is by a one-time cloud routine. It must be self-contained.

---

You are the orchestrator of **round 2** of our impact-layer research, on this repository
(`carsonwng/river-harbor-juniper`, a hackathon entry that selects which sqlglot tests to run for a
patch). You think, review and delegate. Subagents run **Opus**, through the Agent tool with
`model: "opus"`. Nobody is awake. Never ask questions: decide, write down why, and continue.

## Step 0. Gate: exit unless round 1 is done (do this first, cheaply)

```sh
git fetch --all --prune -q
git for-each-ref --sort=-committerdate --format='%(committerdate:iso8601) %(refname:short)' refs/remotes/origin | head -25
gh pr list --state all --limit 30 --json number,title,headRefName,state,updatedAt
```

Round 1 is the earlier cloud session that ran `cloud/impact-cluster.md`, from tag `cloud-base-1`.
Treat it as **done** if either of these holds:
- a `research/runs/*impact*` report exists on `origin/main` or on any `origin/cloud/*` branch, or
  a PR from a `cloud/*` branch mentions the impact cluster;
- no `origin/cloud/*` or `origin/strat/impact*` branch has had a commit in the last **120 minutes**,
  so round 1 has stopped. **Ignore `strat/impact-combo`**: it's a local integration branch from the team
  laptop, not round 1.

If round 1 is still active (commits in the last 120 minutes and no final report), **stop now**:
print one line saying so and end the session. Do nothing else. Two impact clusters must
never run at once.

## Step 1. Catch up (read before acting)

Read in order:
1. `CLAUDE.md`, `README.md`, `SCORING.md`.
2. `research/` on `origin/main`, then the newest versions on `origin/night/*` and `origin/cloud/*`:
   `LOG.md`, `experiments.md`, `submissions.md`, `open-questions.md`, `runs/*`.
3. Round 1's report, `strategies/impact-*.md` on its branches, and its PRs.
4. `cloud/impact-cluster.md`: round 1's brief. The **rules, validation standard, PR template and
   documentation duties there apply unchanged**. Read it in full.

Known facts as of 2026-11-20 20:30 (verify them; don't trust them blindly):
- Best verified candidate: `int/night-1948` `1a5f961` = main (pack + triage) + `strat/impact-base`.
  Sample in docker: 178.9 s, 0 misses. **Practice board: 245.2 s.** The oracle floor on the sample is 94.4 s.
- `strat/runcpu` `79a3b3e`: identical selections, with slightly less run CPU (178.655 s).
- Held-out set: tooling on `origin/validation/m1`, labelled by a parallel cloud session.
  Check whether `validation/sets/` exists on any branch.
- Round 1 opened PRs #1 `strat/impact-attrs`, #2 `strat/impact-relprof` and #3 `strat/impact-isolate`. A local agent
  on the team laptop is combining all three into **`strat/impact-combo`**, with docker and 4-CPU cold-build
  timing (cloud has no docker) and a deadline-aware build (internal deadline about 8.5 min). **Use
  `strat/impact-combo` as your base if it exists and has passed `br.py verify`** (see its
  `strategies/impact-combo.md`). Don't redo the combination work.
- Build budget: the 10 min limit is hard. A build over it rejects the submission, and the final rebuilds every
  image. Every extra suite pass you add must fit the combo's deadline mechanism.
- An overnight runner on the team laptop integrates `strat/impact-*` branches, verifies them in
  docker against the sample and held-out sets, and submits once per hour. **Your branches reach
  the board through it.** Name them `strat/impact-r2-<slug>` and open draft PRs.

## Step 2. Discovery (you)

Start from where round 1 ended, not from scratch:
- Take round 1's **open questions and untried hypotheses**, and its dead ends, so you don't repeat them.
- Measure where the remaining time is on the best candidate: per-commit selected vs failing,
  which heavy tests are still selected, and why. Heavy tests:
  - #1490 `test_parser.py::TestParser::test_max_nodes`, 28.8 s;
  - #1106 `test_executor.py::TestExecutor::test_execute_tpcds`, 21.5 s;
  - #1224 `test_generator.py::TestGenerator::test_generator_memory`, 6.7 s.
- If a held-out set exists, run the best candidate on it first. **Any held-out miss is the
  top priority**: a miss costs 300 s, more than all the remaining optimisation is worth.
- Rank the hypotheses in `research/open-questions.md`, each with expected gain, miss risk and test plan.

## Step 3. The cluster (capped)

- At most **3 Opus subagents at once**, and at most **2 rounds**. Stop early once a candidate beats
  `int/night-1948` by more than 10% on the sample with **0 misses on sample and held-out data**,
  or once a round brings no improvement.
- Each subagent works from the best verified base, on `strat/impact-r2-<slug>`, editing only the
  impact layer (`blast/impact.py`, `build/impact.py`, `build/impact_lib/`). Its deliverable:
  - `strategies/impact-r2-<slug>.md`;
  - the branch pushed;
  - a draft PR using round 1's template.
- Review every result yourself: re-run the evals, and check that it contains **no
  patch-to-tests lookup table** (hard rule) and never reads answers from the selector.
- Total concurrent pytest workers must not exceed `nproc`.

## Step 4. Document (required)

On branch `cloud/<YYYYMMDD>-impact-round2`, with its own draft PR to `main`:
- `research/runs/<YYYYMMDD>-impact-round2.md`: gate outcome, starting candidate, discovery
  findings, every experiment (branch, PR, numbers, verdict), and next steps;
- append to `research/LOG.md`, and update `research/experiments.md` and `research/open-questions.md`;
- in `research/submissions.md`, recommend which of your branches the overnight runner should submit, in order.

Never merge to `main`, force-push, or commit the hackathon's name or its track URL.
