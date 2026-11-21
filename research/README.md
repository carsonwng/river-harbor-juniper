# Research record

This folder is the team's memory. Every session, local or cloud, human or agent, reads it
first and appends to it last. If it isn't written here, the next run will repeat it.

| File | What it holds | How to update |
| --- | --- | --- |
| [`LOG.md`](LOG.md) | Dated, append-only log of every run or session | One entry per run: what was done, headline numbers, link to the report |
| [`experiments.md`](experiments.md) | Every experiment ever tried, one row each | Add a row per experiment. Change status; never delete rows |
| [`submissions.md`](submissions.md) | Practice-board submissions, scores, the next queue | Add each submission and its score; keep the queue current |
| [`open-questions.md`](open-questions.md) | Ranked hypotheses and unknowns | Close answered ones (with the answer), add new ones |
| `runs/<YYYYMMDD>-<slug>.md` | Full report of one run | One per run, never edited afterwards |

Per-branch detail lives in `strategies/<name>.md` on each branch (merged branches keep theirs
on `main`).

## Conventions

- Numbers always name their set and mode, e.g. "sample/local 635.4 s, 0 misses" or
  "practice board 868.3 s".
- Absolute dates (YYYY-MM-DD), commit shas, and PR links.
- Status values: `candidate` (ready to submit), `promising`, `dead-end`, `abandoned`,
  `merged`.
- Rule check: any experiment touching selection states why it is not a patch-to-tests table.
