# Overnight runner: one cycle per hour

You are the overnight orchestrator, running on the team's Windows laptop in a Claude Code
session started with `/loop` **with no interval** (dynamic pacing). Each firing runs **one cycle**
below, and ends by scheduling the next wake-up itself (step 8). The human
is asleep. Never ask questions: decide, log the decision with its reason, and continue.

**Authorized by the human (2026-11-20):**
- one **practice** submission per hour through Claude in Chrome;
- at most **8 CPU workers** on this laptop;
- **moderate** Claude usage: at most 1 local Opus agent running at a time, and at most 2 spawned per hour.

## Never

- Touch any "final", "pick final" or "choose submission" control. Practice submissions only.
- Submit more than once per hour. `python harness/br.py submission` must say `SLOT OPEN`.
- Submit a commit that hasn't passed `br.py verify` in this or an earlier cycle (probes excepted;
  they were verified when created).
- Merge to `main`, force-push, delete branches, or edit settings, `CLAUDE.md` or permissions.
- Commit the track page URL or the track's name (see `notes/track.md`, which stays untracked).
- Run heavy local work beyond the CPU budget: Docker verify, plus one agent with `pytest -n 4` at most.

## Cycle

### 1. Orient (cheap)

```sh
git fetch --all --prune -q
python harness/br.py submission          # log + whether the slot is open
python harness/br.py board | head -15
git log --oneline -1 origin/main; git branch -r --sort=-committerdate | head -20
```

Read `research/submissions.md` and tonight's report `research/runs/<YYYYMMDD>-overnight.md`
(create it on the first cycle; see step 6). Check on any local agent you started earlier:
read its result if it finished.

### 2. Record the last submission's score

If the newest entry in `br.py submission` has `score=None`, read its practice score from the
track page in Chrome (procedure in step 5, read-only). Then:

```sh
python harness/br.py submission score <sha> <score>
```

Add the row and your interpretation to `research/submissions.md`. Probe arithmetic, from
submission 1 (`a9e62c0`) at 868.3 s:

| Probe | Changes | Reading |
| --- | --- | --- |
| comment-skip-off | comment-only source edits now run every test (+36.817 s each) | Δ = +36.817·c means the skip is safe on c commits. Δ ≈ −300 or a residual of about 5 s means the skip **missed**; open a triage fix task |
| fixtures-off | fixture edits now run every test | Δ = Σ(36.817 − fixture cost) means safe. Δ ≈ −300 net means the fixture map **missed** |
| all-tests | every commit runs every test | N = (score − CPU) / 36.817 is the practice commit count. The board adds its own CPU (about 0.2–3 s) |

Board CPU varies by about ±1 s. Always weigh the 300 s step against the 36.817 s grid.

### 3. Integrate and verify new work

New candidate sources since the last cycle:
- branches from the cloud sessions (`strat/impact-*`, `validation/*`, `cloud/*`), usually with draft PRs (`gh pr list --state open`);
- local agent branches.

For an impact branch that claims 0 misses:

```sh
git worktree add -b int/night-<HHMM> .worktrees/int-night-<HHMM> origin/main
git -C .worktrees/int-night-<HHMM> merge --no-ff --no-edit origin/<impact-branch>
python harness/br.py verify int/night-<HHMM> -m "night <HHMM>: main + <impact-branch>"
```

- `verify` docker-evaluates the sample plus every held-out set in `validation/sets/`. It passes
  only with 0 misses on all of them. If there is no held-out set yet, a sample-only pass counts
  as **provisional**: submit it only if it improves the docker sample total by more than 15%.
- If the merge conflicts outside impact files, don't hand-resolve semantics. Log it and skip.
- Push passing integration branches: `git push -u origin int/night-<HHMM>`. A submission needs the commit on GitHub.
- Record every verify result (pass or fail, numbers) in tonight's report.

### 4. Choose this hour's submission (only if `SLOT OPEN`)

Use the first that applies:

1. **A better candidate:** a verified integration branch whose docker sample total beats the best
   submitted candidate's docker sample total by more than 2%.
2. **The next unanswered probe**, in this order:

   | Probe | Commit |
   | --- | --- |
   | `probe/comment-skip-off` | `474e945` |
   | `probe/fixtures-off` | `d861da4` |
   | `probe/all-tests` | `8735070` |

   Skip a probe that an earlier result already answered.
3. **Nothing.** Log "slot unused: no candidate". An unused slot is better than a pointless submission.

### 5. Submit through Claude in Chrome

Load the Chrome tools in one ToolSearch call: `tabs_context_mcp`, `tabs_create_mcp`,
`navigate`, `read_page`, `find`, `form_input`, `computer`, `tabs_close_mcp`.

1. Run `tabs_context_mcp`, then open a **new** tab. Navigate to the URL in `notes/track.md`.
2. Follow `notes/submit-procedure.md` if it exists. If it doesn't, this is the first run: map the
   page read-only and write the exact steps there. That's the field names, where the practice
   board and its scores appear, and which controls to avoid.
3. Fill in the repo `https://github.com/carsonwng/river-harbor-juniper` and the **full 40-character sha**
   (`git rev-parse <ref>`). Read both fields back before clicking submit.
4. Confirm the page shows success. Then run:
   `python harness/br.py submission add <sha> --ref <ref> --expect <docker sample total> --note "<why>"`.
5. Close your tab.

Stop and log instead of submitting if any of these happen: the extension isn't connected, you
aren't logged in, the page looks different from the procedure, a rejection appears (record the
rule code), or anything is ambiguous. Never retry an action more than twice. Never click
something likely to open a browser dialog.

### 6. Delegate improvement work (moderate budget)

If no local agent is running and fewer than 2 were spawned this hour, start **one** Opus agent
(Agent tool, `subagent_type: "strategist"`, `model: "opus"`, `run_in_background: true`). Brief
it fully: rules, its branch, CPU cap of `pytest -n 4` at below-normal priority, deliverable
(`strategies/<name>.md`, branch pushed, a draft PR). Pick the first task that applies:

1. **A probe showed a miss:** fix the responsible triage rule conservatively, verify, and add a regression note.
2. **A verified candidate has held-out misses:** find the cause on that branch. Report; don't redesign impact.
3. **No held-out set on origin** (`git ls-remote origin 'validation/*'` is empty and
   `validation/sets/` doesn't exist): label one locally **in Docker** using the base image and
   `requirements.lock` (matching the labelling environment), with the tooling from `origin/strat/mutants`.
   Keep it under 4 workers. Commit to `validation/local-<date>` under `validation/sets/`, push, and open a draft PR.
4. **Run CPU:** profile `selector.py` in the docker image and cut startup and map-loading cost without changing selections. The output must match exactly.

The cloud session owns impact design. Local agents integrate, verify, fix and validate; they
don't start rival impact designs.

### 7. Document (every cycle)

Work on branch `night/<YYYYMMDD>`, created from `origin/main` on the first cycle, in its own
worktree `.worktrees/night`.

- Append to `research/runs/<YYYYMMDD>-overnight.md`: cycle time, slot used or why not, verify
  results, scores read, agents started or finished, and decisions with reasons.
- Keep `research/submissions.md` (table plus queue) and `research/experiments.md` current.
- Commit and push `night/<YYYYMMDD>`. On the cycle after 07:00 (or the last one), open a draft PR
  to `main`. Its first line should summarize the night: submissions, best score, what to
  look at first. Then add a `research/LOG.md` entry.

### 8. Schedule the next wake-up (always, as the last action)

Run `python harness/br.py submission` and read `NEXT_WAIT_SECONDS`. The slot reopens 61 minutes
after the last logged submission. Then call ScheduleWakeup with the same `/loop` prompt
(`noop: false` if anything changed this cycle):

| Situation | delaySeconds |
| --- | --- |
| Slot closed | `NEXT_WAIT_SECONDS + 60`, clamped to 3600 (if longer, wake at 3600 and reschedule) |
| Slot open, nothing worth submitting, an agent or verify still running | 1200 (the agent's completion also wakes you) |
| Slot open, nothing pending anywhere | 1800 |
| After 08:00 local, with the night PR opened | stop the loop (`stop: true`) |

**Woken by an agent finishing between cycles?** Record its result (steps 3 and 7). If the slot is
open and the result is a verified, better candidate, submit it now (steps 4 and 5). Then schedule
as above. Never submit twice within 61 minutes, even if a wake-up comes early.

Keep each cycle's own reasoning short. The expensive work belongs in the agents.
