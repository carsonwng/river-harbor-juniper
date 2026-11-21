# Overnight run 2026-11-20

Orchestrator: Claude Code `/loop 60m` on the laptop, following `overnight/RUNBOOK.md`.

## Cycle 1 (19:48 EST)

- **Orient:** slot OPEN (last submission 18:00). Submission 1 (`a9e62c0`) already scored 868.3 s.
  New on origin: `validation/m1` (mutant tooling plus docker labelling, pushed 19:43 by a cloud
  session, no `validation/sets/` yet), `cloud/20261121-impact-cluster` (brief and hypotheses only),
  `strat/impact-base` (= `strat/impact` 4ca11e3 rebased onto main). No open PRs.
- **Integrate:** `int/night-1948` = origin/main + origin/strat/impact-base (`1a5f961`), clean merge.
  `br.py verify` started in the background; result goes in the next cycle.
  No held-out set exists, so a pass is only provisional and needs >15% over 720.2 s (< 612.2 s).
  `strat/impact` was 631.1 s locally, so it probably won't qualify yet.
- **Submission: slot unused.** Claude in Chrome tools are not available in this session
  (ToolSearch finds no `mcp__claude-in-chrome__*`). The runbook says stop and log. The planned
  submission was `probe/comment-skip-off` `474e945`.
- **Agent:** started `runcpu` (Opus strategist, task 4: run CPU profile, identical stdout),
  based on `int/night-1948`. Task 3 (labelling) skipped: `validation/m1` exists on origin and a
  cloud session appears to be labelling now.
- **Verify result (about 20:05):** `int/night-1948` `1a5f961` **PASS** on the sample: simulated 177.739 s,
  CPU 1.184 s, **total 178.923 s, 0 misses** (docker). That is 75% under 720.2 s, so it qualifies
  as a *provisional* candidate (sample only, no held-out set yet). Pushed to origin. It is the next
  submission, ahead of the probes, as soon as Chrome is connected. Run it against a held-out set
  once `validation/sets/` appears.

## Note from the lead session (20:14 local)

- The cycle-1 session was closed by the human: Claude in Chrome was not attached. The loop is
  being restarted in a new session with Chrome connected, using `/loop` without an interval
  (self-paced, per RUNBOOK step 8).
- The local agent `runcpu` ended when that session closed. Its branch `strat/runcpu` holds one
  **unverified** commit, `79a3b3e` (AST parse cache, compileall, `python -I -S`), plus an
  uncommitted `strategies/runcpu.md`. Next cycle: verify `strat/runcpu`. Its stdout must be
  byte-identical to `int/night-1948` on the sample. Restart it only if work remains.
- Orphaned test processes from that session were killed.
- Next submission stands: `int/night-1948` `1a5f961` (provisional, sample docker 178.9 s,
  0 misses), ahead of the probes.

## Cycle 2 (20:15 EST, new session, self-paced `/loop`)

- **Orient:** slot OPEN (last submission 18:00). No new submission to score. No open PRs.
  Nothing new on origin since cycle 1. Still no `validation/sets/` anywhere (`validation/m1` has
  tooling only and is unchanged since 19:43).
- **Submission: slot unused.** Chrome tools loaded, but **two Chrome browsers are connected**
  ("Browser 1" Windows, "Browser 2" macOS) and none is selected for this session. The extension
  requires the human to pick one and won't let the agent choose. Per the runbook (ambiguous, so
  stop and log), nothing was submitted. **Human action needed:** select the browser for this session
  (or disconnect the other one). Planned submission is unchanged: `int/night-1948` `1a5f961b93a4c892f5ee6630a43912cd5f79a89c`.
- **Verify:** `strat/runcpu` `79a3b3e` **PASS** (sample docker: sim 177.739, CPU 0.916, total
  **178.655 s**, 0 misses). Selector stdout on the sample is **byte-identical** to `int/night-1948` (`cmp`).
  That is only 0.15% better than 178.9, below the 2% bar, so it doesn't replace the planned submission.
  It is a safe CPU-only improvement to stack on future integrations. The orphaned `strategies/runcpu.md`
  is committed (`6660055`) and `strat/runcpu` is pushed.
- **Agent:** started `label-local` (Opus strategist, runbook task 3). It labels a held-out mutant
  set in Docker from `validation/m1` tooling on `validation/local-20261120`, then verifies
  `int/night-1948` against it. Reason: our only candidate is provisional, and the cloud labelling
  that cycle 1 assumed was underway has produced nothing in 30+ minutes.
- **Retry on the human's request (20:18):** Chrome worked (browser selected). **Submission 2:** `int/night-1948`
  `1a5f961` submitted to `carsonwng/river-harbor-juniper`, receipt BB-69453A60, queued 20:18. Expected about 179 s
  plus practice drift. Score is read next cycle.
- **Submission 1 detail (from the track page):** all 3 runs 867.471 s simulated, 0.81-0.84 s CPU, **0 commits missed**.
  So triage's comment-only skip and fixture map did not miss on practice. **All three probes are retired**,
  because their question is answered directly. (Submission 1 was at 19:06, not 18:00.)
- **Submission 2 score (reported by the human): 245.2 s** (868.3 to 245.2, -72%). It is below 300, so there are 0 misses.
  The practice/sample ratio is 1.37 (submission 1: 1.21), so the impact selection is relatively larger on practice commits.
  Worth a look for the cloud impact cluster: precision on practice, not recall.
