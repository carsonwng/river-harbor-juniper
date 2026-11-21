20 single-fixture edits of tpc-ds.sql / tpc-h.sql (t000-t015 from validation/generate.py's
fixture_edit, seeds 1000+k; t016-t019 hand-made edge cases, see edits.json).
Labelled by running tests/test_executor.py (whole module, normal order) plus
TestOptimizer::test_tpch/test_tpcds in the br-label image with SKIP_INTEGRATION=0.
Only those 62 tests were run: the static fixture map (dynamically validated on
strat/triage) says no other test reads these files. Use tests.json from sample/.
