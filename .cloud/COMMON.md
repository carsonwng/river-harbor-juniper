# Common brief for impact-cluster strategy agents (2026-11-21 cloud run)

You are one of three Opus strategy agents working in parallel on the **impact layer** of a
hackathon entry (sqlglot test selection). Read `/home/user/river-harbor-juniper/CLAUDE.md`,
`README.md`, `SCORING.md`, `research/*.md` and `cloud/impact-cluster.md` first, then
`blast/impact.py`, `build/impact.py`, `build/impact_lib/*` on branch `strat/impact-base`.

## Setup
- Main checkout: `/home/user/river-harbor-juniper` (never edit it; never edit `.cache/sqlglot`).
- Python: always `/home/user/river-harbor-juniper/.venv/bin/python` (3.13, `requirements.lock`
  installed). Call it PY below. Use absolute paths; the shell cwd may reset.
- Create your branch and worktree from the main checkout:
  `PY harness/br.py new impact-<slug> --from strat/impact-base`
  -> branch `strat/impact-<slug>`, worktree `/home/user/river-harbor-juniper/.worktrees/impact-<slug>`.
  `strat/impact-base` = tag `cloud-base-1` (pack + triage) merged with `origin/strat/impact` (v1b).
- Prebuilt maps for the base code (so you can score at once without building):
  `cp -r /tmp/claude-0/-home-user-river-harbor-juniper/75afbcd1-98bf-593d-a558-6b4691522f5d/scratchpad/base-maps <worktree>/.maps`
- Score from inside your worktree: `PY /home/user/river-harbor-juniper/harness/br.py eval -v -m "<note>"`.
  Base scores **sample/local 177.811 s simulated, 0 misses** (oracle floor 94.351 s).
- Rebuild impact maps after changing the build step:
  `BR_IMPACT_JOBS=1 nice -n 10 PY /home/user/river-harbor-juniper/harness/br.py maps --only impact`
  (about 4 to 5 min at 1 job). `build/impact.py --work DIR --reuse` reuses raw coverage results.
- No docker here: all numbers are "local". Say so.

## Shared machine: hard CPU cap
4 cores for 3 agents. **You may run at most ONE pytest worker process at a time**
(`BR_IMPACT_JOBS=1`, never `-n 2+`), always under `nice -n 10`. One test job at a time.

## Rules (a break disqualifies the team)
- No table from patch content, or from a kind of edit, to the tests it breaks. Maps derived from
  the pinned sqlglot commit (coverage, imports, value/usage profiles, ASTs) are allowed. The selector
  must *reason* from such maps; it must not look up answers.
- Selector/build code never reads `sample/answers.json` or any labelled answers. You may read
  them yourself for analysis.
- Never tune to the 30 sample commits. Every rule you add needs a mechanism-level soundness
  argument. A miss costs 300 s, about 1.7 times the whole current sample score: **when unsure,
  select more**. Anything unexpected must fall back to the current behaviour (or every test).
- Edit only `blast/impact.py`, `build/impact.py`, `build/impact_lib/` and
  `strategies/impact-<slug>.md`. Do not change function signatures used by other layers.
- Build budget: the whole image build has 10 min on 4 cores, 8 GB. The base impact build takes
  about 71 s wall at 4 jobs (one suite pass, 60 s). Keep the total impact build under about
  4 min wall at 4 jobs, and report it (estimate from your 1-job timings). Maps are built in the
  image: keep them under about 25 MB and world-readable. Run: read-only disk, user `nobody`,
  no network, CPU charged (the base selector uses about 0.7 s for 30 commits; stay near it).

## Validation
- Until held-out data exists, validate on the sample. A parallel session is building a labelled
  held-out mutant set on branch `validation/m1` under `validation/sets/m1/` (`commits/`,
  `tests.json`, `answers.json`). **Do not build your own labelled set.** Check for it each time
  you finish a milestone:
  `git -C /home/user/river-harbor-juniper fetch -q origin validation/m1 && mkdir -p <worktree>/.heldout && git -C /home/user/river-harbor-juniper archive FETCH_HEAD validation/sets/m1 | tar -x -C <worktree>/.heldout`
  then `PY .../harness/br.py eval -v --data <worktree>/.heldout/validation/sets/m1 --answers <worktree>/.heldout/validation/sets/m1/answers.json -m "<note> held-out m1"`.
  (`.heldout` and `.maps` must stay untracked.) If the fetch fails, the set does not exist yet.
- Report every result as sample + held-out (or "held-out not available yet"): simulated seconds,
  misses, which tests were missed and why. With any held-out miss, or without held-out numbers,
  call your result "promising", never "candidate".
- You may hand-craft a few probe mutants to test a specific soundness question (copy
  `.cache/sqlglot` into your worktree's untracked `.scratch/`, run just the affected tests with
  one worker and `SKIP_INTEGRATION=0`, `PYTHONPATH=<copy>`). That is a probe, not a data set.

## Deliverables
1. Commits on `strat/impact-<slug>` (commit each improvement; end messages with the two lines
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
   `Claude-Session: https://claude.ai/code/session_01W8pKDxP4RXrvnJMjxqyzC7`; no model names elsewhere).
2. `strategies/impact-<slug>.md`: hypothesis, method, sample and held-out numbers, per-commit
   table (sample), misses, build time, map size, run CPU, risks, dead ends, and a final section
   "PR body" using this template:
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
3. Push: `git push -u origin strat/impact-<slug>` once. UPDATE: pushes now work (access was granted after this brief was written). Earlier they were refused with HTTP
   403 in this session. If yours fails, do not retry and do not try other routes: the
   orchestrator will push and open the draft PR. If it succeeds, open a **draft** PR into `main`
   with the PR body (GitHub MCP tool `mcp__github__create_pull_request`, loaded through
   ToolSearch; `draft: true`; end the body with
   `🤖 Generated with [Claude Code](https://claude.com/claude-code)` and the session URL above).
   Never merge anything, never push to `main`.
4. Final report to the orchestrator: branch, head sha, sample and held-out numbers, misses,
   build time, map size, what worked, what failed, what others should adopt, open risks.

You may read sibling branches (`strat/impact-isolate`, `strat/impact-relprof`,
`strat/impact-attrs`) and borrow from them with credit in the commit message. Never touch
another agent's worktree.

## What the sample looks like (so you do not rediscover it)
The hidden practice and final sets resemble the sample: per 30 commits about 19 source edits
(single-line mutants: operator swaps, `is`<->`==`, constants, dict entries, string literals,
`and`/`or`, argument values, removed lines; about half break nothing), 8 fixture edits and 3
comment-only edits (triage handles the last two). Base selection per sample source commit
(selected tests, truly failing, heavy tests selected):
`2haz` 86/0 (#1106 21.5 s, `is`->`==` in `Dialect`), `66ks` 51/0 (#1106, `is None`->`== None` in
executor `Context.table`), `2jrq` 29/8 and `3utz` 37/37 (#1106 truly fails), `535z` 171/1
(`TSQL.WEEK_OFFSET = -1`->`-2`, pulls #1224 6.7 s), `4d63` 288/0 (`Snowflake.Tokenizer.NUMERIC_ESCAPES`
entry), `ktz3` 52/0 (`arg_types` entry of an Expression class), `gbav` 35/0 (argument of a call in a
class-level parser table), `2qn5` 6/0 (`<`->`<=`), `5y72` 3/0, `fryt` 3/0, `6ze8` 31/1, `ary6` 10/10,
`bj7g` 3/1, `j6uz` 2/2, `rszg` 3/3, `8y7j` 0/0.
The cost of a commit is its busiest worker plus 2 s per worker, so what matters most is
whether a heavy test (#1490 28.8 s, #1106 21.5 s, #1224 6.7 s, #1107 3.9 s, #1428 2.5 s,
#1440 2.2 s, #1295 2.0 s) is selected, then whether anything is selected at all (`[]` costs 0).
