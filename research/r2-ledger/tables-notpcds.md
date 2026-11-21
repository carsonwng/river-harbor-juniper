
## Excess by rule family: marginal saving if this rule selected only failing tests (ms per commit; [commits hit])
| key | m1 | m1-extra | local | syn640 | sample | mean(m1,m1x,local) |
|---|---|---|---|---|---|---|
| class-stmt:users(name-read-at-import) | 1240 [13] | 964 [3] | 1799 [5] | 1845 [145] | 676 [3] | 1334 |
| fn-body | 328 [15] | 727 [8] | 722 [6] | 1459 [221] | 209 [3] | 592 |
| import-check-fail | 523 [2] | 0 [0] | 0 [0] | 482 [9] | 0 [0] | 174 |
| fn-header | 0 [0] | 494 [1] | 0 [0] | 44 [3] | 0 [0] | 165 |
| CORE-FILE module-stmt:users(name-read-at-import) | 482 [1] | 0 [0] | 0 [0] | 272 [5] | 0 [0] | 161 |
| module-stmt:users(name-read-at-import) | 154 [1] | 89 [1] | 0 [0] | 483 [22] | 0 [0] | 81 |
| fn-docstring | 43 [1] | 0 [0] | 166 [2] | 54 [5] | 0 [0] | 70 |
| import-fn:module | 195 [1] | 0 [0] | 0 [0] | 12 [2] | 0 [0] | 65 |
| lambda-only | 0 [0] | 94 [1] | 24 [1] | 93 [21] | 2 [1] | 39 |
| CORE-FILE lambda-only | 0 [0] | 0 [0] | 103 [1] | 23 [2] | 0 [0] | 34 |
| exprattr | 73 [1] | 20 [1] | 0 [0] | 118 [17] | 69 [1] | 31 |
| triage:fixture | 4 [3] | 0 [0] | 69 [3] | 410 [126] | 0 [0] | 24 |
| CORE-FILE import-fn:core | 0 [0] | 0 [0] | 0 [0] | 369 [9] | 0 [0] | 0 |
| triage:records of tpch_sqls: fields [1]  | 0 [0] | 0 [0] | 0 [0] | 30 [3] | 0 [0] | 0 |
| triage:records of tpch_sqls: fields [2]  | 0 [0] | 0 [0] | 0 [0] | 37 [3] | 0 [0] | 0 |
| CORE-FILE module-stmt:users(no-names) | 0 [0] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 0 |
| CORE-FILE fn-body | 0 [0] | 0 [0] | 0 [0] | 60 [3] | 0 [0] | 0 |
| triage:records of tpcds_sqls: fields [1] | 0 [0] | 0 [0] | 0 [0] | 31 [2] | 0 [0] | 0 |

## Excess by area :: rule (ms per commit; [commits hit])
| key | m1 | m1-extra | local | syn640 | sample | mean(m1,m1x,local) |
|---|---|---|---|---|---|---|
| dialects :: class-stmt:users(name-read-at-import) | 1092 [9] | 964 [3] | 683 [3] | 933 [84] | 592 [2] | 913 |
| parsers :: class-stmt:users(name-read-at-import) | 148 [4] | 0 [0] | 1115 [2] | 278 [27] | 84 [1] | 421 |
| optimizer :: fn-body | 218 [5] | 0 [0] | 717 [3] | 593 [57] | 82 [1] | 312 |
| tests :: import-check-fail | 523 [2] | 0 [0] | 0 [0] | 482 [9] | 0 [0] | 174 |
| optimizer :: fn-header | 0 [0] | 494 [1] | 0 [0] | 20 [1] | 0 [0] | 165 |
| typing :: CORE-FILE module-stmt:users(name-read-at-import) | 482 [1] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 161 |
| parsers :: fn-body | 34 [2] | 320 [3] | 0 [0] | 182 [47] | 72 [1] | 118 |
| dialects :: fn-body | 0 [0] | 263 [1] | 0 [0] | 73 [8] | 0 [0] | 88 |
| expressions :: import-fn:module | 195 [1] | 0 [0] | 0 [0] | 0 [0] | 0 [0] | 65 |
| optimizer :: module-stmt:users(name-read-at-import) | 154 [1] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 51 |
| expressions :: fn-body | 4 [1] | 129 [1] | 0 [0] | 167 [15] | 0 [0] | 44 |
| generators :: fn-docstring | 43 [1] | 0 [0] | 82 [1] | 0 [0] | 0 [0] | 42 |
| typing :: CORE-FILE lambda-only | 0 [0] | 0 [0] | 103 [1] | 23 [2] | 0 [0] | 34 |
| parsers :: lambda-only | 0 [0] | 94 [1] | 0 [0] | 48 [12] | 2 [1] | 31 |
| expressions :: exprattr | 73 [1] | 20 [1] | 0 [0] | 118 [17] | 69 [1] | 31 |
| executor :: module-stmt:users(name-read-at-import) | 0 [0] | 89 [1] | 0 [0] | 53 [4] | 0 [0] | 30 |
| optimizer :: fn-docstring | 0 [0] | 0 [0] | 84 [1] | 50 [4] | 0 [0] | 28 |
| tests :: triage:fixture | 4 [3] | 0 [0] | 69 [3] | 410 [126] | 0 [0] | 24 |
| executor :: fn-body | 31 [1] | 9 [1] | 0 [1] | 71 [15] | 0 [0] | 14 |
| planner.py :: fn-body | 33 [2] | 0 [0] | 0 [0] | 22 [4] | 0 [0] | 11 |
| generators :: fn-body | 4 [3] | 4 [1] | 4 [2] | 228 [60] | 55 [1] | 4 |
| trie.py :: CORE-FILE import-fn:core | 0 [0] | 0 [0] | 0 [0] | 115 [4] | 0 [0] | 0 |
| __init__.py :: module-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 50 [1] | 0 [0] | 0 |
| generators :: lambda-only | 0 [0] | 0 [0] | 0 [0] | 21 [5] | 0 [0] | 0 |
| schema.py :: fn-body | 0 [0] | 0 [0] | 0 [0] | 24 [1] | 0 [0] | 0 |
| errors.py :: fn-body | 0 [0] | 0 [0] | 0 [0] | 29 [1] | 0 [0] | 0 |
| tokenizer_core.py :: CORE-FILE fn-body | 0 [0] | 0 [0] | 0 [0] | 36 [2] | 0 [0] | 0 |
| trie.py :: CORE-FILE fn-body | 0 [0] | 0 [0] | 0 [0] | 23 [1] | 0 [0] | 0 |
| expressions :: class-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 0 |
| tests :: triage:records of tpcds_sqls: fields [1] | 0 [0] | 0 [0] | 0 [0] | 31 [2] | 0 [0] | 0 |
| tokens.py :: CORE-FILE import-fn:core | 0 [0] | 0 [0] | 0 [0] | 254 [5] | 0 [0] | 0 |
| tokens.py :: CORE-FILE module-stmt:users(no-names) | 0 [0] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 0 |
| typing :: module-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 271 [15] | 0 [0] | 0 |
| tests :: triage:records of tpch_sqls: fields [1]  | 0 [0] | 0 [0] | 0 [0] | 30 [3] | 0 [0] | 0 |
| generator.py :: class-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 48 [2] | 0 [0] | 0 |
| tests :: triage:records of tpch_sqls: fields [2]  | 0 [0] | 0 [0] | 0 [0] | 37 [3] | 0 [0] | 0 |
| parser.py :: class-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 0 |
| __main__.py :: CORE-FILE module-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 218 [4] | 0 [0] | 0 |
| expressions :: module-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 0 |
| typing :: fn-body | 0 [0] | 0 [0] | 0 [0] | 42 [8] | 0 [0] | 0 |
| generators :: class-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 477 [30] | 0 [0] | 0 |

## Excess by edited area (whole commit excess) (ms per commit; [commits hit])
| key | m1 | m1-extra | local | syn640 | sample | mean(m1,m1x,local) |
|---|---|---|---|---|---|---|
| dialects | 1066 [8] | 1227 [4] | 683 [3] | 940 [76] | 592 [2] | 992 |
| parsers | 559 [6] | 413 [4] | 1115 [2] | 555 [77] | 157 [3] | 696 |
| optimizer | 290 [5] | 494 [1] | 801 [4] | 685 [54] | 82 [1] | 528 |
| typing | 482 [1] | 0 [0] | 127 [2] | 346 [23] | 0 [0] | 203 |
| expressions | 285 [2] | 149 [2] | 0 [0] | 366 [31] | 69 [1] | 144 |
| dialects+tests | 172 [1] | 0 [0] | 0 [0] | 25 [6] | 0 [0] | 57 |
| generators | 43 [3] | 4 [1] | 86 [3] | 716 [84] | 55 [1] | 44 |
| executor | 31 [1] | 99 [2] | 0 [0] | 181 [19] | 0 [0] | 43 |
| executor+generators+optimizer | 82 [1] | 0 [0] | 0 [0] | 0 [0] | 0 [0] | 27 |
| tests | 4 [3] | 0 [0] | 69 [3] | 492 [114] | 0 [0] | 24 |
| generator.py | 4 [1] | 0 [0] | 0 [0] | 31 [1] | 0 [0] | 1 |
| executor+optimizer | 0 [0] | 0 [0] | 0 [1] | 33 [2] | 0 [0] | 0 |
| errors.py | 0 [0] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 0 |
| __main__.py | 0 [0] | 0 [0] | 0 [0] | 218 [4] | 0 [0] | 0 |
| parsers+tests | 0 [0] | 0 [0] | 0 [0] | 79 [5] | 0 [0] | 0 |
| schema.py | 0 [0] | 0 [0] | 0 [0] | 24 [1] | 0 [0] | 0 |
| expressions+tests | 0 [0] | 0 [0] | 0 [0] | 25 [1] | 0 [0] | 0 |
| __init__.py+tests | 0 [0] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 0 |
| dialects+optimizer | 0 [0] | 0 [0] | 0 [0] | 50 [4] | 0 [0] | 0 |
| planner.py+typing | 0 [0] | 0 [0] | 0 [0] | 23 [1] | 0 [0] | 0 |
| dialects+parsers | 0 [0] | 0 [0] | 0 [0] | 37 [2] | 0 [0] | 0 |
| __init__.py+dialects+tests | 0 [0] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 0 |
| expressions+parsers+tokens.py | 0 [0] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 0 |
| optimizer+parsers+typing | 0 [0] | 0 [0] | 0 [0] | 20 [1] | 0 [0] | 0 |
| trie.py | 0 [0] | 0 [0] | 0 [0] | 241 [5] | 0 [0] | 0 |
| executor+tokens.py | 0 [0] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 0 |
| generators+typing | 0 [0] | 0 [0] | 0 [0] | 23 [2] | 0 [0] | 0 |
| parser.py | 0 [0] | 0 [0] | 0 [0] | 87 [3] | 0 [0] | 0 |
| tokens.py | 0 [0] | 0 [0] | 0 [0] | 272 [5] | 0 [0] | 0 |
| tokenizer_core.py | 0 [0] | 0 [0] | 0 [0] | 170 [4] | 0 [0] | 0 |
| errors.py+generators | 0 [0] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 0 |
| dialects+generators | 0 [0] | 0 [0] | 0 [0] | 37 [3] | 0 [0] | 0 |
| generators+optimizer+typing | 0 [0] | 0 [0] | 0 [0] | 20 [1] | 0 [0] | 0 |
| generator.py+optimizer | 0 [0] | 0 [0] | 0 [0] | 31 [1] | 0 [0] | 0 |

## Heavy tests: saving from dropping each (when not failing) (ms per commit; [commits hit])
| key | m1 | m1-extra | local | syn640 | sample | mean(m1,m1x,local) |
|---|---|---|---|---|---|---|
| ALL5 together | 1111 [10] | 416 [3] | 794 [2] | 1632 [140] | 111 [1] | 774 |
| max_nodes | 561 [3] | 0 [0] | 527 [1] | 755 [33] | 0 [0] | 363 |
| gen_memory | 410 [6] | 304 [2] | 209 [1] | 593 [88] | 111 [1] | 308 |
| opt_tpch | 32 [2] | 54 [1] | 0 [0] | 53 [27] | 0 [0] | 29 |
| tpch | 20 [1] | 58 [1] | 0 [0] | 50 [20] | 0 [0] | 26 |

## Excess by test that tops the busiest worker (ms per commit; [commits hit])
| key | m1 | m1-extra | local | syn640 | sample | mean(m1,m1x,local) |
|---|---|---|---|---|---|---|
| other | 846 [26] | 955 [10] | 1564 [14] | 2589 [427] | 667 [7] | 1122 |
| gen_memory | 1050 [6] | 840 [2] | 289 [1] | 1563 [87] | 288 [1] | 726 |
| max_nodes | 1005 [3] | 0 [0] | 1025 [1] | 1795 [33] | 0 [0] | 677 |
| tpch | 154 [1] | 494 [1] | 0 [0] | 144 [10] | 0 [0] | 216 |
| tpcds | 0 [0] | 99 [2] | 4 [2] | 0 [0] | 0 [0] | 34 |
| opt_tpch | 0 [0] | 0 [0] | 0 [0] | 168 [10] | 0 [0] | 0 |
