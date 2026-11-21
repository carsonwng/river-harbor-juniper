# Cloud session prompts

Each file here is a self-contained brief for a Claude Code cloud session. It starts from a
tagged commit so every run begins at a known point.

| Prompt | Start tag | Purpose |
| --- | --- | --- |
| [`impact-cluster.md`](impact-cluster.md) | `cloud-base-1` | Orchestrator plus Opus subagent cluster exploring impact-layer strategies |

## Launching

1. Open a Claude Code cloud session on `carsonwng/blast-radius-cwng`.
2. Choose **Fable** as the session model. It is the orchestrator and thinker. The prompt tells it
   to spawn subagents with `model: "opus"`.
3. Send: `Read cloud/impact-cluster.md and execute it exactly. Start from tag cloud-base-1.`
4. Results come back as draft PRs (one per experiment, plus the run's documentation PR) and in
   `research/`. Practice submissions stay manual, at most one per hour; see `research/submissions.md`.

## Adding a new run

Tag the commit the run should start from (`cloud-base-N`), and add or update a prompt here.
Every prompt must make the run read `research/` first and append to it last.
