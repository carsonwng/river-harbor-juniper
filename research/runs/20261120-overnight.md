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

## Cycle 3 (21:19 EST)

- **Orient:** slot reopens 21:20. Three cloud draft PRs (all sample-only, local numbers, no held-out data):
  #2 `strat/impact-relprof` 123.7 s (-30%), #1 `strat/impact-attrs` 156.0 s (-12%; also fixes 4 import-failure probe
  misses), #3 `strat/impact-isolate` 176.9 s (closes 5 probed miss classes, safety). A local `strat/impact-combo`
  (dirty, 179.4 s docker) exists from another session and was left alone. `label-local` is still labelling
  (`validation/local-20261120` `e72f246`, no `validation/sets/` yet).
- **Integrate:** `int/night-2120` = origin/main + origin/strat/impact-relprof (`53eff6e`), clean merge. It is the only
  draft above the provisional bar (>15% under 178.9, so < 152 s). attrs and isolate are below it, so they are not
  verified this cycle; they are worth combining once held-out data exists.
- **Verify:** `int/night-2120` `53eff6e` **PASS** on the sample in docker: sim 131.728, CPU 1.965, **total 133.693 s, 0 misses**
  (-25% vs 178.9; provisional, sample only). Docker sim is 8 s above relprof's local 123.7 s; check whether the
  relprof profile pass hit its build time limit and fell back for part of it. CPU rose 1.2 to 2.0 s. Pushed.
- **Submission 3 (21:27):** `int/night-2120` `53eff6e7380871fee1fdd96178854effaed31621`, receipt BB-C54059C4.
  Reason: runbook 4.1 with the provisional rule (>15% better on the sample). Expected practice score at the
  submission 2 practice/sample ratio of 1.37: about 183 s.
  The page now also shows a "Pick as our final entry" button; never touch it.
- **Submission 3 score (read 21:5x on the lead's request): 243.2 s**, BB-C54059C4, wait 174 ms. Per run:
  - Run 1 of 3: 242.04 s simulated, 1.18 s CPU, 0 commits missed.
  - Run 2 of 3: 242.04 s simulated, 1.16 s CPU, 0 commits missed.
  - Run 3 of 3: 242.04 s simulated, 1.17 s CPU, 0 commits missed.
  Only 2.0 s simulated less than submission 2 (244.047). The CPU is about 1.17 s, the same as submission 2 (1.19), not the
  2.0 s relprof showed on the sample in local docker. **Reading: the relprof map was most likely missing in their
  build (the profile pass hit its time limit and fell back)**, so practice ran essentially impact-base. Per the lead:
  hold further relprof-based submissions unless they're clearly better on held-out data.

## Policy from the lead (relayed from the human, received about 21:55): build-deadline ladder

Followed from now on, on top of RUNBOOK.md.
- `strat/impact-combo` (relprof + attrs + isolate) uses `ENV BR_BUILD_DEADLINE_S=<s>` (default 555). Optional precision passes stop at the deadline.
- When combo passes `br.py verify` (0 misses), integrate and submit it at **555**.
- If 555 is rejected for build time (or any timeout-like code), submit the same commit at **510** next slot and stay at 510.
- If 555 succeeds, a later slot may try **570**. If 570 is rejected, go back to 555 and stay there. Never go above 570.
- A variant is `int/night-<HHMM>-d<s>`, changing only the ENV line. Verify it (selections must be identical), then push.
- Compare variants by practice score. 570 must beat 555 by more than 1 s, otherwise prefer 555 (margin for the final rebuild).
- Record each variant's deadline, receipt, score and per-run lines in research/submissions.md.
- Hold relprof-based submissions (other than combo under this ladder) unless they're clearly better on held-out data.

## Held-out set m1 and the final-pick safety rule (lead, about 22:00)

- `origin/validation/m1` now has `validation/sets/m1/` (28 labelled commits, still growing). It is copied untracked
  into main's checkout `validation/sets/m1` so `br.py verify` includes it.
- Lead reports that **`int/night-1948` and `int/night-2120` both miss m1 commit `8owm`**: an import-time edit in
  `sqlglot/dialects/hive.py` makes the module raise at import, 1161 tests fail, and 355 are selected. A local eval without maps was
  inconclusive (it selects everything); a docker verify of `int/night-2120` on sample + m1 is running to confirm.
- **Rule from now on: final-pick safety requires 0 misses on m1.** No further submission of any candidate that fails m1.
  An unused slot is preferred. Submissions 2 and 3 stay on the board but are **not safe final picks**.
- The fix is expected in `strat/impact-combo` (isolate crash-users rule + attrs import check), which is pushed only once
  the sample and m1 both show 0 misses.
- **Confirmed by docker verify (about 22:10):** `int/night-2120` `53eff6e` sample 133.683 s, 0 misses; **m1 577.186 s
  (sim 573.012, CPU 4.174), 1 miss: `8owm`. VERIFY FAIL.** `int/night-1948` shares the base impact layer and is
  taken as failing too (per the lead; not re-run). No candidate on origin passes m1, so the next slot goes unused
  unless `strat/impact-combo` lands and passes.

## Cycle 4 (22:30 EST)

- **Orient:** slot OPEN. `strat/impact-combo` is not on origin yet. m1 is unchanged (28 commits, `a4dc791`). The three cloud
  drafts are unchanged. `label-local` is still labelling.
- **Submission: slot unused.** No candidate on origin is known to pass m1. The only other option, main + `impact-isolate`
  (sample 176.9 s, -1%), doesn't beat the best submitted sample total by 2%, so it doesn't qualify under 4.1. Per the lead, an
  unused slot is better than an m1-failing candidate.
- **Agent:** none started (`label-local` still running; budget is one at a time).

## Cycle 5 (22:51 EST)

- **Orient:** slot OPEN. `strat/impact-combo` still isn't on origin. m1 grew to **42** commits; the local untracked copy is
  refreshed. origin/main gained `012cc2f` (scorer-like build timing, shared CPU budget) and `efc2ea4` (build budget research).
- **Submission: slot unused** (no m1-safe candidate on origin). `label-local` is still running.

## Cycle 6 (23:12 EST)

- **Orient:** slot OPEN. Still no `strat/impact-combo` on origin. m1 has 42 commits (local copy current). New: `origin/strat/gap-tests`
  `7b06945` (from the lead session): record-level narrowing of setUpClass-loaded fixtures in triage, built on
  `int/night-2120`. That base has the `8owm` m1 miss in impact, so the branch can't pass m1 as is. It's for the lead to fold
  into combo, not a submission candidate.
- **Submission: slot unused** (no m1-safe candidate). `label-local` is still running (no commits since `e72f246`).
- **Incident (lead, about 23:2x):** another local agent killed up to 4 `br-label` containers by mount pattern
  (funny_hodgkin, objective_mendel, unruffled_torvalds, zen_driscoll, quirky_neumann). `label-local` was told to find runs that
  didn't finish cleanly, relabel those commits before publishing `validation/local-*`, and record it.
- **`label-local` interim (about 23:25):** held-out set `validation/sets/local-20261120` (16 commits: 8 code, 4 fixture,
  3 comment/docstring, 1 multi; 5 break nothing) on `validation/local-20261120` `7dac844`, draft PR #4. Labels come from full-suite
  docker runs (`Dockerfile.label`, same base digest + lock), and 5 relabelled sample commits match answers.json exactly. None of its
  containers were among the killed ones. `int/night-1948`: **0 misses on local-20261120** (docker total 172.2 s,
  oracle 93.4 s), and **2 misses on m1 (42 commits): `8owm` (hive.py) and `doay` (parsers/risingwave.py)**, both
  `* -> +` import-time edits. Sample 0 misses, 180.2 s. Batch B (16 more) is labelling, about 65 min.
  local-20261120 is copied untracked into main's `validation/sets/` next to m1, so verify now covers both.

## Cycle 7 (23:33 EST)

- **Orient:** slot OPEN. Still no `strat/impact-combo` on origin. m1 grew to **56** (local copy refreshed). PR #4
  (`validation/local-20261120`) is open as a draft.
- **Submission: slot unused** (no m1-safe candidate). `label-local` batch B is labelling.

## Cycle 8 (23:54 EST)

- **Orient:** slot OPEN. **`strat/impact-combo` `760d8bd` is on origin** (30 commits over main). Also new: `strat/gap-heavy`.
  m1 is at **70** (refreshed), local-20261120 at 16.
- **Integrate:** `int/night-2354` = origin/main + origin/strat/impact-combo (`2dd8f15`), clean merge. Docker verify on
  sample + m1 + local-20261120 started.
- **Deadline knob mismatch:** combo reads `BR_BUILD_DEADLINE` (default 510 in `build/impact.py`, no Dockerfile ENV line),
  but the ladder names `BR_BUILD_DEADLINE_S`, default 555. Asked the lead. Default plan: variant `int/night-2354-d555` adding
  `ENV BR_BUILD_DEADLINE=555`.
- **Lead reply (00:0x):** submit `int/night-2354` as is (combo's 510 default, no ENV line), only with 0 misses on sample, m1 (70)
  and local-20261120. No d555 variant yet: it's unclear whether combo's 75 s reserve sits inside the deadline or on top. The lead will
  send corrected ladder values in `BR_BUILD_DEADLINE` units. On any miss: don't submit; send the lead the commits and failing tests.
- **Deadline semantics (lead, confirmed by combine):** in `760d8bd`, `BR_BUILD_DEADLINE=510` is the **total build target** from the
  first-RUN stamp. Optional passes are cut at 510 - 50. The tail after the cut is 40-41 s quiet, up to 98 s under load, so the worst case is
  about 575 s (under the 600 s limit, over our 570 cap). Decisions: (1) submitting `int/night-2354` at the 510 default is fine with 0 misses on all
  three sets; (2) **the ladder is paused**, with no 555/570 variants of 760d8bd; (3) combine's next version commits pinned maps
  (`maps-pinned/`, so the normal build runs no expensive pass) and adds `ENV BR_BUILD_DEADLINE_S`. When it lands and verifies, submit it at
  its default. Revisit the ladder only if its build is still near the limit.
- **Verify (00:00):** `int/night-2354` `2dd8f15` **PASS, 0 misses on all three sets** (docker, loaded laptop):
  sample 194.082 s (sim 184.771, CPU 9.311); local-20261120 176.629 s (sim 170.771, CPU 5.858); **m1 (70) 770.081 s
  (sim 735.828, CPU 34.253)**. This is the first m1-safe candidate. The sample sim is worse than base (177.7), most likely because optional
  passes were cut under load (see combo's v4 row). CPU is high (the attrs import check per import-time edit). Pushed.
- **Submission 4 (00:01):** `int/night-2354` `2dd8f15bea79fda14cb64655048fe2541ecddc01`, receipt BB-0083E2D6, combo's 510
  default. Reason: the lead's instruction (submit if 0 misses on all three sets) overrides runbook 4.1's 2% bar. The goal is an m1-safe
  entry that builds on the scorer. Expected practice score: above 243.2 is possible because of the CPU, but the build-pass outcome on their
  builder decides.

## Cycle 9 (01:46 EST; the 01:01 wake-up was late)

- **Submission 4 score: 284.7 s** (BB-0083E2D6, wait 119 ms). Runs: 279.936 s simulated, 4.76 / 4.77 / 4.87 s CPU, 0 commits missed.
  Worse than submission 3 (243.2) by 41.5 s: combo's sim is +37.9 s and CPU +3.6 s. Its optional passes were probably cut on their builder too,
  and the in-process registry rule is coarser. It is the only **m1-safe** entry so far.
- m1 on origin is final: **64** commits (`9e15871`, "sample mix") plus **m1-extra** (22 breaking commits). Both are copied untracked into
  main's `validation/sets/` with local-20261120.
- **Human request (01:46): submit the best run we have, watch results, and run thin until 03:40.** Candidate: `strat/gap-mix`
  `5fc0c56` (lead's branch: int/night-2120 + lambda cut + import-check fix for 8owm/doay; lead's docker eval sample 129.6 s,
  m1(42) 0 misses). `int/night-0147` = origin/main + gap-mix (`24f5e6d`); docker verify on sample + m1(64) + m1-extra + local started.
- **Verify (01:54):** `int/night-0147` `24f5e6d` **PASS, 0 misses on every set** (docker): sample 135.523 (sim 131.362, CPU 4.161);
  local-20261120 173.234; **m1 (64) 509.858** (sim 495.517, CPU 14.341); **m1-extra (22) 260.319**; syn-import16 594.049 (a set the
  branch ships). Best m1-safe candidate: sample 135.5 vs combo's 194.1. Pushed.
- **Submission 5 (01:54):** `int/night-0147` `24f5e6deb2a8feacc039cc6c4cd547aa8f592bba`, receipt BB-46097CB1. Reason: the human asked to
  submit the best run, and it beats every other m1-safe candidate. Caveat: it inherits relprof, whose map was dropped on the scorer in
  submission 3, so expect about 243 s minus gap-mix's lambda-cut gains plus import-check CPU, not the sample ratio.
