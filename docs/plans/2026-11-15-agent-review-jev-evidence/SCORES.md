# Score instrument: 6 severity questions with confidence, 12 cases, 2 repeats

The 6 questions that survived calibration, rewritten as Score questions (levels 0-4 of violation
severity, per-level criteria), same 12 cases, same chunking, same state. Jev returns per question a
score, a confidence and the probability of each level. 24 runs, 6/6 answered each.

CAVEAT: the level descriptions were authored after seeing the noul results, on the same 12 cases. This
is a new instrument version measured on its own authoring set — a design check, not a measurement.

| case | kind | max severity (0-4) | weighted severity (0-1) | confidence |
|---|---|---|---|---|
| `429625b-rmtree` | bad | 2.59 | 0.357 | 0.19 |
| `89bba2f-flaky` | bad | 2.29 | 0.202 | 0.56 |
| `e9bd1ad-checks` | bad | 2.11 | 0.187 | 0.54 |
| `silent-idle` | bad | 1.72 | 0.219 | 0.36 |
| `engine-gaps/t4r1` | bad | 1.85 | 0.163 | 0.61 |
| `plan-layout/t4r1` | bad | 2.51 | 0.361 | 0.21 |
| `stale-team/t1r1` | bad | 1.17 | 0.132 | 0.57 |
| `74addfe-rtk` | clean | 0.33 | 0.018 | 0.95 |
| `c239004-brief` | clean | 0.73 | 0.086 | 0.72 |
| `named-capture/t1` | clean | 1.06 | 0.083 | 0.76 |
| `review-cli/t2` | clean | 0.68 | 0.092 | 0.70 |
| `check-tiers/t2` | clean | 2.22 | 0.186 | 0.58 |

Stability: score mean |Δ| 0.055 on a 0-4 scale (1.4% of the scale), max 0.29; confidence mean |Δ| 0.037,
max 0.24. Compare luna's noul probabilities: mean 0.066, max 0.70 on a 0-1 scale.

## Routers on the same 12 cases

| router | rule | missed bad | false on clean | operating band |
|---|---|---|---|---|
| A: noul composite over the 6 keepers | escalate if composite < 0.700 | 0/7 | 0/5 | 0.690–0.692 (**0.002 wide**) |
| B: score + confidence | escalate if max severity ≥ 1.5 **or** confidence < 0.7 | 0/7 | 1/5 | S 1.17–2.22 (**1.05 wide**), C = 0.7 |
| B, narrower confidence | S ≥ 2.5, C = 0.5 | 4/7 | 0/5 | — |

The confidence axis is load-bearing: `stale-team/t1r1` (bad) has max severity **1.17** — it would pass
any severity threshold above 1.2 — and confidence **0.57** sends it to escalation. Two more bad cases
sit at confidence 0.19 and 0.21, where the model knows it cannot tell.

The single false escalation is `check-tiers/t2`, labelled clean, with `hidden-state` at 2.2 and
confidence 0.58. That label means "no reviewer raised a blocker"; the 2.2 may be a real finding nobody
raised. It is the weakest label in the set, not necessarily a false positive.
