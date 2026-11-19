# Results (40 cases, 80 paired case-seasons)

## Season: nov (n=40, mean p_wet=0.066)

### T1 TravelPlanner metrics

| system | Delivery Rate | Commonsense Micro | Commonsense Macro | Hard Micro | Hard Macro | Final |
|---|---|---|---|---|---|---|
| greedy | 100.0 | 100.0 | 100.0 | 87.2 | 75.0 | 75.0 |
| llm_pipeline | 100.0 | 100.0 | 100.0 | 89.7 | 80.0 | 80.0 |
| llm_raw | 100.0 | 73.2 | 12.5 | 92.3 | 85.0 | 7.5 |
| llm_modulo | 100.0 | 78.2 | 22.5 | 100.0 | 100.0 | 22.5 |
| milp | 100.0 | 100.0 | 100.0 | 96.2 | 92.5 | 92.5 |
| milp_robust | 100.0 | 100.0 | 100.0 | 96.2 | 92.5 | 92.5 |

### T2 Utility (% of best per case), cost

| system | expected | dry | all-wet | LLM calls | seconds | drive min |
|---|---|---|---|---|---|---|
| greedy | 58.7 | 58.0 | 57.8 | 0 | 0.001 | 104.1 |
| llm_pipeline | 57.6 | 57.5 | 55.6 | 1 | 24.456 | 110.3 |
| llm_raw | 98.0 | 98.2 | 94.2 | 1 | 24.455 | 393.2 |
| llm_modulo | 92.8 | 92.6 | 91.3 | 3.48 | 37.589 | 451.6 |
| milp | 63.3 | 62.4 | 61.4 | 0 | 1.011 | 120.6 |
| milp_robust | 63.6 | 62.4 | 65.6 | 0 | 1.134 | 122.6 |

### T3 milp_robust minus system (paired, bootstrap 95% CI)

| vs | expected utility (pp) | 95% CI | final pass | 95% CI |
|---|---|---|---|---|
| greedy | 4.93 | [2.93, 7.15] | 0.175 | [0.075, 0.3] |
| llm_pipeline | 6.01 | [3.54, 9.15] | 0.125 | [0.025, 0.225] |
| llm_raw | -34.39 | [-44.66, -24.08] | 0.85 | [0.725, 0.95] |
| llm_modulo | -29.16 | [-39.79, -18.94] | 0.7 | [0.55, 0.825] |
| milp | 0.35 | [0.03, 0.84] | 0.0 | [0.0, 0.0] |

### T4 Per-constraint pass rate (%)

| system | Within Sandbox | Within Current City | Diverse Attractions | Complete Information | Day Window | Drive Limit | Opening Hours | Pace | Interest Coverage | Weather Safety |
|---|---|---|---|---|---|---|---|---|---|---|
| greedy | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 74 | n/a |
| llm_pipeline | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 79 | n/a |
| llm_raw | 95 | 52 | 100 | 100 | 25 | 45 | 95 | 100 | 84 | n/a |
| llm_modulo | 100 | 62 | 100 | 100 | 30 | 55 | 100 | 100 | 100 | n/a |
| milp | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 92 | n/a |
| milp_robust | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 92 | n/a |

## Season: monsoon (n=40, mean p_wet=0.422)

### T1 TravelPlanner metrics

| system | Delivery Rate | Commonsense Micro | Commonsense Macro | Hard Micro | Hard Macro | Final |
|---|---|---|---|---|---|---|
| greedy | 100.0 | 100.0 | 100.0 | 78.2 | 55.0 | 55.0 |
| llm_pipeline | 100.0 | 100.0 | 100.0 | 79.1 | 55.0 | 55.0 |
| llm_raw | 100.0 | 75.4 | 20.0 | 78.8 | 57.5 | 7.5 |
| llm_modulo | 100.0 | 76.4 | 20.0 | 87.2 | 72.5 | 17.5 |
| milp | 100.0 | 100.0 | 100.0 | 86.2 | 70.0 | 70.0 |
| milp_robust | 100.0 | 99.3 | 95.0 | 96.5 | 92.5 | 87.5 |

### T2 Utility (% of best per case), cost

| system | expected | dry | all-wet | LLM calls | seconds | drive min |
|---|---|---|---|---|---|---|
| greedy | 60.5 | 61.4 | 58.8 | 0 | 0.001 | 104.1 |
| llm_pipeline | 58.1 | 58.7 | 58.0 | 1 | 14.078 | 103.6 |
| llm_raw | 93.8 | 93.7 | 93.5 | 1 | 14.078 | 454.6 |
| llm_modulo | 90.3 | 92.2 | 90.1 | 3.3 | 35.146 | 520.4 |
| milp | 65.6 | 65.8 | 62.3 | 0 | 1.109 | 120.6 |
| milp_robust | 66.8 | 64.7 | 65.2 | 0 | 1.116 | 122.9 |

### T3 milp_robust minus system (paired, bootstrap 95% CI)

| vs | expected utility (pp) | 95% CI | final pass | 95% CI |
|---|---|---|---|---|
| greedy | 6.34 | [3.09, 10.36] | 0.325 | [0.2, 0.475] |
| llm_pipeline | 8.7 | [4.71, 13.14] | 0.325 | [0.175, 0.475] |
| llm_raw | -27.02 | [-37.84, -15.88] | 0.8 | [0.675, 0.925] |
| llm_modulo | -23.53 | [-35.21, -12.63] | 0.7 | [0.55, 0.85] |
| milp | 1.17 | [-1.26, 3.84] | 0.175 | [0.075, 0.3] |

### T4 Per-constraint pass rate (%)

| system | Within Sandbox | Within Current City | Diverse Attractions | Complete Information | Day Window | Drive Limit | Opening Hours | Pace | Interest Coverage | Weather Safety |
|---|---|---|---|---|---|---|---|---|---|---|
| greedy | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 74 | 0 |
| llm_pipeline | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 74 | 0 |
| llm_raw | 100 | 55 | 100 | 100 | 32 | 45 | 95 | 95 | 76 | 0 |
| llm_modulo | 100 | 60 | 95 | 100 | 30 | 52 | 98 | 100 | 92 | 0 |
| milp | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 92 | 0 |
| milp_robust | 100 | 100 | 100 | 95 | 100 | 100 | 100 | 100 | 92 | 100 |

## T5 Rain-loss sensitivity (monsoon): milp_robust + Plan B vs milp

| rho | expected gain % | all-wet gain % | n |
|---|---|---|---|
| 0.3 | -0.89 | -0.56 | 37 |
| 0.5 | 0.29 | 1.49 | 37 |
| 0.7 | 2.3 | 5.73 | 37 |
| 0.9 | 5.99 | 20.2 | 37 |

## T6 Solver

| system | optimal | iterations (mean/max) | cuts | seconds (mean/max) |
|---|---|---|---|---|
| milp | 100% | 9.15/167 | 8.4 | 1.06/28.994 |
| milp_robust | 100% | 8.75/166 | 7.92 | 1.125/30.171 |
