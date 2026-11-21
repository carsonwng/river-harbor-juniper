# Cloud run: impact-layer research cluster

You are the **orchestrator** of a Claude Code cloud session working on our Blast Radius
hackathon entry. You think, plan, delegate and review. **Subagents running Opus** do the
implementation and measurement. Your output is evidence and proposals in the repo: branches,
draft PRs and research notes. It is not a merged `main`.

Work autonomously. Nobody is watching this session live. When you would normally ask, decide,
write the decision and its reason in the run report, and keep going.

---

## 0. Known starting point

- Repo: `carsonwng/blast-radius-cwng`. Start from tag **`cloud-base-1`** on `main`.
  Run `git checkout cloud-base-1 && git switch -c cloud/<YYYYMMDD>-<slug>` for your own notes branch.
- If `origin/strat/impact` exists, it holds the latest impact-layer work, which is not yet on
  `main`. Treat it as the impact baseline you are trying to beat (see section 2).
- Before anything else, read in this order:
  1. `research/README.md`, `research/LOG.md`, `research/experiments.md`, `research/submissions.md`,
     `research/open-questions.md`. These record what earlier runs tried. Do not repeat a closed
     experiment unless you write down why the new attempt differs.
  2. `CLAUDE.md`, `README.md`, `SCORING.md`.
  3. `strategies/*.md` and, if present, `STRATEGY.md` on `origin/strat/impact`.
  4. `blast/*.py`, `build/*.py`, `harness/br.py`.

## 1. The problem in brief (details in README.md and SCORING.md)

- For each sqlglot patch, print the tests to run, split over at most 8 workers.
- Cost per commit is the busiest worker's seconds plus 2 s per worker. A missed failing test
  adds **300 s**. The container's run CPU is added. Lower is better.
- **Hard rules.** These are not negotiable. A violation disqualifies the team:
  - No table from patch content, or from a kind of edit, to the tests it breaks. Maps derived
    from the pinned sqlglot commit (coverage, imports, fixtures, ASTs) are allowed.
  - Selector code never reads `sample/answers.json` or any labelled answers.
  - The build downloads only from PyPI, npm, Docker Hub, GitHub releases at a fixed tag, and
    sqlglot at the pinned commit. The run has no network.
- Layers: `blast/patch.py` → `blast/triage.py` → **`blast/impact.py`** → `blast/pack.py`.
  Pack is at its theoretical floor, and triage is handled by other agents. **This run is about
  impact only:** mapping sqlglot *source* edits to the tests that can fail.

## 2. Why impact, and where the time is

- On the sample (30 commits), `main` (pack + triage) scores 720.2 s in docker with 0 misses.
  Of the 717.4 s simulated, **699.5 s** comes from 19 source-edit commits that still run all
  1,620 tests at 36.8 s each.
- Three heavy tests set the floor whenever they are selected:

  | Test | Time |
  | --- | --- |
  | `tests/test_parser.py::TestParser::test_max_nodes` (#1490) | 28.8 s |
  | `tests/test_executor.py::TestExecutor::test_execute_tpcds` (#1106) | 21.5 s |
  | `tests/test_generator.py::TestGenerator::test_generator_memory` (#1224) | 6.7 s |

  Leaving out #1490 when it provably cannot fail is the single biggest lever. #1106 really
  does fail on some optimizer and executor changes. Check `research/` for whether that is
  still true.
- `strat/impact` v1b (per-test `sys.monitoring` statement coverage, plus AST placement of
  edits, plus class-usage handling of import-time code) scored 635.4 s locally with 0 misses,
  with triage at its default. The floor (`br.py oracle`) is 94.4 s.
- The practice board scored our first submission (`a9e62c0`, pack + triage) at **868.3 s**.
  It is unclear whether that contains a miss; see `research/submissions.md`.

## 3. Environment setup (do this once, yourself, before spawning anyone)

```sh
nproc; free -g; python3 --version; docker info >/dev/null 2>&1 && echo docker-ok || echo no-docker
python3 harness/br.py sqlglot            # pinned sqlglot into .cache/sqlglot (shared, read-only)
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.lock
python3 harness/br.py eval -v -m "cloud baseline reproduce"   # expect 1104.5 s simulated with no maps
python3 harness/br.py maps && python3 harness/br.py eval -v -m "cloud baseline with maps"
```

- The labelling environment is `python:3.13-slim`. If the cloud Python is not 3.13, say so in
  the run report, and prefer docker for anything that runs sqlglot's tests.
- Reproduce the known numbers first. If they don't match `research/LOG.md`, stop and
  investigate before experimenting, and record the cause.
- **CPU budget:** total concurrent pytest workers across all subagents is at most `nproc`. Some
  sqlglot tests start their own process pools.
- The board in `.git/br-harness/leaderboard.jsonl` does not survive this session. Copy the final
  `br.py board --all` output into your run report.

## 4. Validation: the sample is not enough

- Only 30 sample commits exist. The practice and final sets are hidden, and the final has about
  1.5 times as many commits. **A strategy that scores well on the sample but has no
  mechanism-level justification is rejected.**
- Held-out data: check `validation/sets/` on `main` and `origin/strat/mutants` for a labelled
  mutant set in harness format (`commits/`, `tests.json`, `answers.json`). If none exists,
  spend **one** subagent first on building one:
  - mutate pinned sqlglot source the way the sample does (operators, constants, dict entries,
    string literals, `is`/`==`, removed lines, in parsers, generators, dialects, the optimizer,
    expressions and the executor);
  - label each mutant by running the full suite with `SKIP_INTEGRATION=0` under
    `requirements.lock`;
  - commit the set to `validation/sets/<name>/`. It is small text: keep the total under 5 MB.
- Report every result as **sample + held-out**: simulated seconds, misses, and which tests were
  missed. A strategy with any held-out miss is not a candidate until the cause is understood
  and fixed.

## 5. Discovery (you, before delegating)

Spend real thought here. This is the part Opus subagents can't do for you.

1. Per sample commit, compare what the current impact selects with what fails
   (`br.py eval -v` plus your own script). Where is the waste? Which heavy tests come back,
   and through which mechanism: coverage, class users, import-time fallback, or
   all-tests fallback?
2. Study *why* the heavy tests depend on what they do. #1490 builds huge ASTs (node limits),
   #1106 runs the TPC-DS executor path, #1224 measures generator memory. Read those tests in
   `.cache/sqlglot/tests`.
3. Write a ranked list of hypotheses into `research/open-questions.md`. Each needs an expected
   gain, a miss risk, and how to test it. Then choose the 3 to 5 most promising, *orthogonal*
   strategies for the cluster.

Ideas to consider, not an assignment:

- **Heavy-test gating:** a dedicated, conservative proof that a change cannot reach a heavy
  test's behaviour. For example, #1490 depends on node counting and parser limits, not on a
  dialect's `TRANSFORMS` entry.
- **Finer import-time attribution:** per-key attribution for dispatch tables (`Parser.FUNCTIONS`,
  `Generator.TRANSFORMS`, `TokenizerSettings`), from which keys each test looked up at run time.
- **Dialect scoping:** a change in `sqlglot/dialects/x.py`, `parsers/x.py` or `generators/x.py`
  reaches only the tests that instantiate dialect x or its subclasses.
- **Value-level reasoning:** a changed constant or string literal affects only the tests whose
  coverage reaches the statement *and* that observe the value.
- **Cheap confirmation at run time:** run a small, uncertain candidate set inside the container
  to prune it. This costs charged CPU, so model the break-even point first.
- **Cost-aware selection:** use `pack.plan` to price candidate sets. With 8 workers, an extra
  cheap test is nearly free; an extra heavy one is not.

## 6. The cluster

Spawn subagents with the **Agent tool, `model: "opus"`**, in parallel (one message, several
calls, `run_in_background: true`). Give each one a full, self-contained brief:

- the strategy and the hypothesis it tests, and what has already been ruled out;
- its branch: `python3 harness/br.py new impact-<slug> --from <base>`, where `<base>` is
  `origin/strat/impact` if it exists, else `main`. It works only in `.worktrees/impact-<slug>`
  and edits only `blast/impact.py`, `build/impact.py` and `build/impact_lib/`;
- the rules in section 1, the CPU cap in section 3, and the validation standard in section 4;
- the deliverable:
  - commits on its branch;
  - a `strategies/impact-<slug>.md` with hypothesis, method, sample and held-out numbers,
    per-commit table, misses, build time, map size, run CPU, risks and dead ends;
  - the branch pushed to `origin`;
  - a **draft PR into `main`** using the template below;
  - a final report to you.

Subagents may read each other's branches and borrow from them (credit the source in the commit
message). They never push to `main`, merge PRs, or touch another agent's worktree.

When results come in, *review them yourself*. Read the diffs and check that no rule is broken,
especially any implicit patch-to-tests lookup. Re-run their evals, and confirm the held-out
numbers. If two strategies are complementary, spawn one more subagent to combine them on a new
branch, with its own PR.

### PR template

```
## Hypothesis
## Method (mechanism, not tuning)
## Results
| set | simulated s | misses | run CPU s | vs baseline |
## Per-commit changes (sample)
## Rule check (why this is not a patch->tests table)
## Risks and what would break it
## Submission candidate? (yes/no, expected practice score and why)
```

## 7. Documentation (required; future runs depend on it)

Before you finish, on your `cloud/<date>-<slug>` branch, opened as its own PR to `main`:

- `research/runs/<YYYYMMDD>-<slug>.md`, the full run report:
  - starting point (tag and sha) and environment (nproc, Python, docker or not);
  - reproduced baseline numbers;
  - the discovery findings;
  - every experiment: branch, PR link, numbers, verdict;
  - the final `br.py board --all`;
  - decisions you made without asking, and why;
  - what you would do next.
- `research/LOG.md`: append one dated entry (5 to 10 lines) linking the run report.
- `research/experiments.md`: one row per experiment, closed ones included, with status
  `candidate` / `promising` / `dead-end` / `abandoned` and a one-line reason.
- `research/open-questions.md`: update it. Close what you answered and add what you found.
- `research/submissions.md`: add your recommended next practice submission (branch, sha,
  expected score, what it tests). **We may submit once per hour**, so recommend exactly one, plus
  an ordered queue of up to 3 follow-ups. Humans do the submitting.

Write for a reader with no context: what was tried, what happened, the numbers, and why.
Negative results are as valuable as positive ones.

## 8. Stop conditions

- Stop spawning new experiments once you have either one candidate with zero misses on sample
  and held-out data that beats the baseline by more than 10%, or three rounds without
  improvement. Then finish the documentation in section 7.
- Never leave the repo in a state where `main`'s Dockerfile build or `python3 selector.py`
  breaks. Every PR must pass `br.py eval` (and `eval --docker` if docker is available).
