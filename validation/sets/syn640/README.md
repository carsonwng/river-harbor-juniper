# syn640: unlabelled synthetic patches (strat/gap-mix)

Made with the m1 generator from `origin/validation/m1` (`validation/generate.py`) on a clean
scratch clone of pinned sqlglot:

    python -I validation/generate.py --sqlglot <clean clone> --out <dir> -n 240 --seed 4242 --kinds code=0.8,multi=0.2 --areas

The command was given `-n 240` and `--kinds code=0.8,multi=0.2`, but it produced 640 patches in
the generator's default mix (code 416, fixture 111, multi 62, comment 51). Why is not
understood; `edits.json` records what was generated. There are **no labels**. Use the set for
selection cost (`harness/br.py eval --data` needs answers, so score cost from the selector's
output directly) and for checks that need no labels. Selector code must never read anything here.
