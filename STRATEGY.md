# strat/r2-tables: per-key reads of Dialect/Generator/Parser/Tokenizer class tables

Cut from `origin/strat/runcpu2` (`f89594d`, practice submission 8). Impact layer only.

## Hypothesis

An edit confined to class-level assignments in the dialect, generator, parser or
tokenizer families changes the program only through the values import binds. The
import-time state diff (br_state, strat/impact-attrs) gives every changed
(class, attribute[, dict key]) after all propagation (subclass merges, tries, the
_Dialect metaclass, `_TokenizerBase.__init_subclass__`). The tests that can observe
a change are the ones that read that attribute, and for a dict, that looked up that
key (or read the key set / whole table). Those reads can be recorded per test in the
existing coverage pass (as strat/gap-expr does for Expr classes), so no extra pass.

Measured target (runcpu2 image selections, single-family class-assign-only commits):
syn640 148 commits / 1270.8 s of 5578.3 s; m1 12 / 111.2 of 420.9; local 5 / 66.7 of 326.4;
m1-extra 4 / 31.4 of 203.1; sample 4 / 24.9 of 123.0.

(mechanism, measurements and dead ends below as they come)
