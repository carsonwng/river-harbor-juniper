
## Excess by rule family: marginal saving if this rule selected only failing tests (ms per commit; [commits hit])
| key | m1 | m1-extra | local | syn640 | sample | mean(m1,m1x,local) |
|---|---|---|---|---|---|---|
| fn-body | 842 [14] | 1567 [8] | 2984 [6] | 3148 [219] | 209 [3] | 1798 |
| class-stmt:users(name-read-at-import) | 1240 [13] | 964 [3] | 1836 [5] | 1876 [144] | 676 [3] | 1346 |
| triage:records of tpch_sqls: fields [1]  | 274 [1] | 798 [1] | 516 [1] | 112 [3] | 0 [0] | 530 |
| fn-header | 0 [0] | 1066 [1] | 0 [0] | 63 [3] | 0 [0] | 355 |
| CORE-FILE lambda-only | 0 [0] | 0 [0] | 667 [1] | 80 [2] | 0 [0] | 222 |
| import-check-fail | 555 [2] | 0 [0] | 0 [0] | 510 [9] | 0 [0] | 185 |
| CORE-FILE module-stmt:users(name-read-at-import) | 513 [1] | 0 [0] | 0 [0] | 288 [5] | 0 [0] | 171 |
| module-stmt:users(name-read-at-import) | 351 [1] | 89 [1] | 0 [0] | 594 [21] | 0 [0] | 147 |
| import-fn:module | 369 [1] | 0 [0] | 0 [0] | 6 [2] | 0 [0] | 123 |
| fn-docstring | 43 [1] | 0 [0] | 166 [2] | 158 [5] | 0 [0] | 70 |
| lambda-only | 0 [0] | 94 [1] | 24 [1] | 118 [21] | 2 [1] | 39 |
| exprattr | 73 [1] | 20 [1] | 0 [0] | 146 [17] | 69 [1] | 31 |
| triage:fixture | 4 [3] | 0 [0] | 69 [3] | 410 [124] | 0 [0] | 24 |
| triage:records of tpch_sqls: fields [2]  | 0 [0] | 0 [0] | 0 [0] | 119 [3] | 0 [0] | 0 |
| CORE-FILE fn-body | 0 [0] | 0 [0] | 0 [0] | 65 [3] | 0 [0] | 0 |
| triage:records of tpcds_sqls: fields [1] | 0 [0] | 0 [0] | 0 [0] | 80 [2] | 0 [0] | 0 |
| CORE-FILE module-stmt:users(no-names) | 0 [0] | 0 [0] | 0 [0] | 58 [1] | 0 [0] | 0 |
| CORE-FILE import-fn:core | 0 [0] | 0 [0] | 0 [0] | 329 [9] | 0 [0] | 0 |

## Excess by area :: rule (ms per commit; [commits hit])
| key | m1 | m1-extra | local | syn640 | sample | mean(m1,m1x,local) |
|---|---|---|---|---|---|---|
| optimizer :: fn-body | 736 [5] | 0 [0] | 2309 [3] | 1687 [56] | 82 [1] | 1015 |
| dialects :: class-stmt:users(name-read-at-import) | 1092 [9] | 964 [3] | 683 [3] | 950 [84] | 592 [2] | 913 |
| tests :: triage:records of tpch_sqls: fields [1]  | 274 [1] | 798 [1] | 516 [1] | 112 [3] | 0 [0] | 530 |
| parsers :: class-stmt:users(name-read-at-import) | 148 [4] | 0 [0] | 1153 [2] | 281 [26] | 84 [1] | 434 |
| optimizer :: fn-header | 0 [0] | 1066 [1] | 0 [0] | 40 [1] | 0 [0] | 355 |
| expressions :: fn-body | 0 [0] | 968 [1] | 0 [0] | 257 [16] | 0 [0] | 323 |
| generators :: fn-body | 4 [3] | 4 [1] | 674 [2] | 321 [60] | 55 [1] | 227 |
| typing :: CORE-FILE lambda-only | 0 [0] | 0 [0] | 667 [1] | 80 [2] | 0 [0] | 222 |
| tests :: import-check-fail | 555 [2] | 0 [0] | 0 [0] | 510 [9] | 0 [0] | 185 |
| typing :: CORE-FILE module-stmt:users(name-read-at-import) | 513 [1] | 0 [0] | 0 [0] | 58 [1] | 0 [0] | 171 |
| expressions :: import-fn:module | 369 [1] | 0 [0] | 0 [0] | 0 [0] | 0 [0] | 123 |
| parsers :: fn-body | 34 [2] | 320 [3] | 0 [0] | 182 [46] | 72 [1] | 118 |
| optimizer :: module-stmt:users(name-read-at-import) | 351 [1] | 0 [0] | 0 [0] | 58 [1] | 0 [0] | 117 |
| dialects :: fn-body | 0 [0] | 263 [1] | 0 [0] | 73 [8] | 0 [0] | 88 |
| generators :: fn-docstring | 43 [1] | 0 [0] | 82 [1] | 0 [0] | 0 [0] | 42 |
| parsers :: lambda-only | 0 [0] | 94 [1] | 0 [0] | 48 [12] | 2 [1] | 31 |
| expressions :: exprattr | 73 [1] | 20 [1] | 0 [0] | 146 [17] | 69 [1] | 31 |
| executor :: module-stmt:users(name-read-at-import) | 0 [0] | 89 [1] | 0 [0] | 156 [4] | 0 [0] | 30 |
| optimizer :: fn-docstring | 0 [0] | 0 [0] | 84 [1] | 154 [4] | 0 [0] | 28 |
| tests :: triage:fixture | 4 [3] | 0 [0] | 69 [3] | 410 [124] | 0 [0] | 24 |
| executor :: fn-body | 31 [1] | 9 [1] | 0 [1] | 398 [15] | 0 [0] | 14 |
| planner.py :: fn-body | 33 [2] | 0 [0] | 0 [0] | 107 [4] | 0 [0] | 11 |
| tests :: triage:records of tpcds_sqls: fields [1] | 0 [0] | 0 [0] | 0 [0] | 80 [2] | 0 [0] | 0 |
| __main__.py :: CORE-FILE module-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 230 [4] | 0 [0] | 0 |
| expressions :: class-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 58 [1] | 0 [0] | 0 |
| schema.py :: fn-body | 0 [0] | 0 [0] | 0 [0] | 43 [1] | 0 [0] | 0 |
| __init__.py :: module-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 53 [1] | 0 [0] | 0 |
| parser.py :: class-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 58 [1] | 0 [0] | 0 |
| tests :: triage:records of tpch_sqls: fields [2]  | 0 [0] | 0 [0] | 0 [0] | 119 [3] | 0 [0] | 0 |
| tokens.py :: CORE-FILE module-stmt:users(no-names) | 0 [0] | 0 [0] | 0 [0] | 58 [1] | 0 [0] | 0 |
| generators :: lambda-only | 0 [0] | 0 [0] | 0 [0] | 21 [5] | 0 [0] | 0 |
| typing :: fn-body | 0 [0] | 0 [0] | 0 [0] | 42 [8] | 0 [0] | 0 |
| executor :: lambda-only | 0 [0] | 0 [0] | 0 [0] | 40 [1] | 0 [0] | 0 |
| trie.py :: CORE-FILE import-fn:core | 0 [0] | 0 [0] | 0 [0] | 59 [4] | 0 [0] | 0 |
| trie.py :: CORE-FILE fn-body | 0 [0] | 0 [0] | 0 [0] | 43 [1] | 0 [0] | 0 |
| generators :: class-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 477 [30] | 0 [0] | 0 |
| typing :: module-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 270 [14] | 0 [0] | 0 |
| tokenizer_core.py :: CORE-FILE fn-body | 0 [0] | 0 [0] | 0 [0] | 22 [2] | 0 [0] | 0 |
| tokens.py :: CORE-FILE import-fn:core | 0 [0] | 0 [0] | 0 [0] | 270 [5] | 0 [0] | 0 |
| generator.py :: class-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 52 [2] | 0 [0] | 0 |
| expressions :: module-stmt:users(name-read-at-import) | 0 [0] | 0 [0] | 0 [0] | 58 [1] | 0 [0] | 0 |

## Excess by edited area (whole commit excess) (ms per commit; [commits hit])
| key | m1 | m1-extra | local | syn640 | sample | mean(m1,m1x,local) |
|---|---|---|---|---|---|---|
| optimizer | 767 [5] | 1066 [1] | 2393 [4] | 1818 [54] | 82 [1] | 1409 |
| dialects | 1066 [8] | 1227 [4] | 683 [3] | 961 [76] | 592 [2] | 992 |
| parsers | 590 [6] | 413 [4] | 1153 [2] | 562 [77] | 157 [3] | 719 |
| tests | 279 [4] | 798 [1] | 585 [4] | 678 [114] | 0 [0] | 554 |
| expressions | 458 [2] | 988 [2] | 0 [0] | 474 [32] | 69 [1] | 482 |
| typing | 513 [1] | 0 [0] | 692 [2] | 423 [23] | 0 [0] | 402 |
| generators | 43 [3] | 4 [1] | 756 [3] | 808 [84] | 55 [1] | 268 |
| executor+generators+optimizer | 320 [1] | 0 [0] | 0 [0] | 0 [0] | 0 [0] | 107 |
| dialects+tests | 172 [1] | 0 [0] | 0 [0] | 25 [6] | 0 [0] | 57 |
| executor | 31 [1] | 99 [2] | 0 [0] | 580 [19] | 0 [0] | 43 |
| planner.py | 32 [1] | 0 [0] | 0 [0] | 83 [3] | 0 [0] | 11 |
| generator.py | 4 [1] | 0 [0] | 0 [0] | 46 [1] | 0 [0] | 1 |
| executor+optimizer | 0 [0] | 0 [0] | 0 [1] | 80 [2] | 0 [0] | 0 |
| optimizer+parsers+typing | 0 [0] | 0 [0] | 0 [0] | 40 [1] | 0 [0] | 0 |
| executor+tests | 0 [0] | 0 [0] | 0 [0] | 38 [1] | 0 [0] | 0 |
| expressions+parsers+tokens.py | 0 [0] | 0 [0] | 0 [0] | 58 [1] | 0 [0] | 0 |
| errors.py | 0 [0] | 0 [0] | 0 [0] | 58 [1] | 0 [0] | 0 |
| parsers+tests | 0 [0] | 0 [0] | 0 [0] | 110 [5] | 0 [0] | 0 |
| __main__.py | 0 [0] | 0 [0] | 0 [0] | 230 [4] | 0 [0] | 0 |
| generators+optimizer+typing | 0 [0] | 0 [0] | 0 [0] | 20 [1] | 0 [0] | 0 |
| planner.py+typing | 0 [0] | 0 [0] | 0 [0] | 43 [1] | 0 [0] | 0 |
| __init__.py+tests | 0 [0] | 0 [0] | 0 [0] | 58 [1] | 0 [0] | 0 |
| errors.py+generators | 0 [0] | 0 [0] | 0 [0] | 54 [1] | 0 [0] | 0 |
| expressions+tests | 0 [0] | 0 [0] | 0 [0] | 43 [1] | 0 [0] | 0 |
| __init__.py+dialects+tests | 0 [0] | 0 [0] | 0 [0] | 58 [1] | 0 [0] | 0 |
| tokens.py | 0 [0] | 0 [0] | 0 [0] | 288 [5] | 0 [0] | 0 |
| dialects+parsers | 0 [0] | 0 [0] | 0 [0] | 37 [2] | 0 [0] | 0 |
| schema.py | 0 [0] | 0 [0] | 0 [0] | 43 [1] | 0 [0] | 0 |
| parser.py | 0 [0] | 0 [0] | 0 [0] | 106 [3] | 0 [0] | 0 |
| optimizer+tests | 0 [0] | 0 [0] | 0 [0] | 40 [1] | 0 [0] | 0 |
| generator.py+optimizer | 0 [0] | 0 [0] | 0 [0] | 46 [1] | 0 [0] | 0 |
| dialects+optimizer | 0 [0] | 0 [0] | 0 [0] | 70 [4] | 0 [0] | 0 |
| generators+typing | 0 [0] | 0 [0] | 0 [0] | 23 [2] | 0 [0] | 0 |
| trie.py | 0 [0] | 0 [0] | 0 [0] | 273 [5] | 0 [0] | 0 |
| executor+tokens.py | 0 [0] | 0 [0] | 0 [0] | 58 [1] | 0 [0] | 0 |
| dialects+generators | 0 [0] | 0 [0] | 0 [0] | 37 [3] | 0 [0] | 0 |
| tokenizer_core.py | 0 [0] | 0 [0] | 0 [0] | 180 [4] | 0 [0] | 0 |

## Heavy tests: saving from dropping each (when not failing) (ms per commit; [commits hit])
| key | m1 | m1-extra | local | syn640 | sample | mean(m1,m1x,local) |
|---|---|---|---|---|---|---|
| ALL5 together | 2336 [13] | 2625 [5] | 4174 [8] | 4088 [204] | 111 [1] | 3045 |
| tpcds | 1226 [7] | 2209 [3] | 3380 [7] | 2457 [122] | 0 [0] | 2272 |
| gen_memory | 384 [5] | 304 [2] | 209 [1] | 583 [84] | 111 [1] | 299 |
| max_nodes | 345 [3] | 0 [0] | 268 [1] | 388 [33] | 0 [0] | 204 |

## Excess by test that tops the busiest worker (ms per commit; [commits hit])
| key | m1 | m1-extra | local | syn640 | sample | mean(m1,m1x,local) |
|---|---|---|---|---|---|---|
| tpcds | 1690 [5] | 2931 [5] | 4167 [8] | 3607 [90] | 0 [0] | 2929 |
| other | 685 [24] | 827 [9] | 744 [9] | 1823 [364] | 667 [7] | 752 |
| max_nodes | 1068 [3] | 0 [0] | 1062 [1] | 1893 [33] | 0 [0] | 710 |
| gen_memory | 839 [5] | 840 [2] | 289 [1] | 1380 [80] | 288 [1] | 656 |
