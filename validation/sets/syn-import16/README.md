# syn-import16: the 16 syn640 commits that leave some sqlglot module unable to import

Each patch was applied to a scratch clone, and every sqlglot module was imported in a fresh
interpreter. These 16 raised in at least one module (TypeError, KeyError, AttributeError,
ParseError from duckdb's import-time parse, ...).

`answers.json` lists **every test** as failing. It is not a real label, only a check that a
selector picks everything (any selection short of all tests shows up as a "miss"). Real failing
sets are probably large but smaller than everything.

    python harness/br.py eval --data validation/sets/syn-import16 -m "import check"
