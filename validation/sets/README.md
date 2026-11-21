# Held-out validation sets

Each set is in harness format: `commits/<id>.patch`, `tests.json` (copy of the sample's),
`answers.json` (`failing` test numbers and `kind`), plus `edits.json` (what the generator did).

    python harness/br.py eval --data validation/sets/m1 -m "note"

**Validation data only.** Selector code and build steps must never read anything under
`validation/` (no patch-to-tests tables). Labels come from running the full suite
(`validation/label.py --docker`), see `research/runs/`.

| Set | Commits | Break nothing | Kinds | Notes |
| --- | --- | --- | --- | --- |
| `m1` | 64 | 25 (39%) | code 33, fixture 17, comment 8, multi 6 | Sample-like mix. Use this for headline held-out numbers |
| `m1-extra` | 22 | 0 | code 17, fixture 5 | Breaking commits split out of m1 to keep its mix; use for extra miss-hunting |

Details and caveats: `research/runs/20261121-validation-m1.md`.
