# Held-out validation sets

Each set is in harness format: `commits/<id>.patch`, `tests.json` (copy of the sample's),
`answers.json` (`failing` test numbers and `kind`), plus `edits.json` (what the generator did).

    python harness/br.py eval --data validation/sets/m1 -m "note"

**Validation data only.** Selector code and build steps must never read anything under
`validation/` (no patch-to-tests tables). Labels come from running the full suite
(`validation/label.py --docker`), see `research/runs/`.
