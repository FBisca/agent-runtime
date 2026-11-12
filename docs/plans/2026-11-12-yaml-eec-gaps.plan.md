# Plan: yaml-eec gaps

Slug: `yaml-eec-gaps`. Contract: `2026-11-12-yaml-eec-gaps.plan.yaml`.

## Why now

The yaml-eec plan is archived (2026-11-11), but its gaps review
(`~/.local/state/orchestrate/agent-runtime-13d3a0f9f7e6/yaml-eec/gaps.md`, read at 4bee059) left decisions the code
does not keep. D14 promises a negative control that `plan check` never asks for. Receipt tampering is caught, but no
test pins the case a Pi worker could cause. attention.md keeps telling the person a finished ticket needs them. These
are cheap to close now, while the contract is fresh and no other YAML plan relies on the looser behaviour.

## Sources

| Source | What it settles |
|---|---|
| The request (`driver gaps <gaps.md>`) | Scope: the ten gaps of the yaml-eec review |
| `gaps.md` (yaml-eec run state) | The ten gaps, each with its suggested edit |
| `docs/plans/2026-11-10-yaml-eec.plan.md` | D14 (l.94), D19 (l.99), D20 (l.100), D31 (l.111), D34 (l.114), D39 (l.119), Migration (l.271, l.301), worked example 1 (l.211) |
| yaml-eec ledger (`yaml-eec.json`) | Archived at 4279206; S24 HUMAN_ACCEPTED 2026-11-11T18:27Z; T9 COMPLETE (agent-skills#73) |
| `docs/specs/plan-contract.md` | Exists (#168); R1 lists the `plan check` defects, R20 the receipts verify refuses |
| `plan.py:72,355-470` | `negative` is optional and checked only for existence |
| `verify.py:306-331` | `read_receipt` already refuses a receipt whose sha256 changed |
| `driver.py:1729-1741, 5106-5118` | attention.md is append-only; nothing rewrites or clears it |
| `tests/test_decider.py:411` | Pins the append-only behaviour (`count("sign-off") == 1` after COMPLETE) |
| `driver.py:6141, 6413`; `tests/test_plan.py:94` | `plan schema` exists and is asserted inside an S1 test only |
| `agent-skills` `test_delegation.py:100`, `test_portability.py:181` | S26's converge-absence is already asserted |
| Gather count over `docs/plans/*.plan.yaml` | 0 of 9 YAML plans has risk-tagged baseline-pass evidence without `negative` |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | `plan check` refuses risk-tagged `baseline: pass` evidence with no `negative`; a waiver, `human` evidence, `baseline: fail` or an untagged ticket is exempt | Amend D14 to "optional" | Evidence that already passes at base proves nothing without a control; no existing plan breaks | yaml-eec D14; gather count; user, grill Q1 |
| D2 | D34's mid-run upgrade gaps (ledger import stranding, stale absolute paths in briefs, stale contract.md) go to a separate plan | Build them here | That work is migration and state, larger than this cleanup | User, grill Q2 |
| D3 | Edit the archived yaml-eec narrative: strike D20, drop the panel/T10 lines in Migration and Risks, record S24's acceptance (2026-11-11, ledger), point D34 at the follow-up | Leave the archive as written | The narrative contradicts D40 and confuses the next reader; D23 never mentioned T10, so it is left alone | User, grill Q3; gather |
| D4 | Pin the Pi write path with a test: a receipt rewritten in place, digest, tree and result kept, is UNKNOWN with the sha256 reason. verify.py is not changed | Record it as an accepted risk only | The sha256 check is the only guard a hookless Pi worker meets, and today's test never asserts its reason | yaml-eec D31; explorer; user, grill Q4 |
| D5 | `plan schema` becomes a requirement with its own test | Drop it from D19 | It is built and shipped; the spec should say so | yaml-eec D19; user, grill Q5 |
| D6 | Delete `wait_checks.py` and its test | Keep it | Only its own test imports it | yaml-eec worked example 1; gather; user, grill Q6 |
| D7 | D39's agent-skills extras (marketplace.json, chunked-dispatch.md, test-suite R1) stay out | Add tests for them here | Another repository; already recorded as decisions | User, grill Q7 |
| D8 | The driver rebuilds attention.md from the ledger on every tick: one line per ticket still reading "needs you", the header alone when none | Clear it only at finish; append and strike | The ledger is the truth `status --json` already reads; a rebuild cannot drift | Explorer root cause; user, grill Q8 |
| D9 | One living spec: `docs/specs/plan-contract.md` | Split the attention rule into orchestrator-engine.md | Four of the five behaviours are plan-contract ones; one spec keeps the archive simple | User, grill Q10 |

Gaps 2, 3 and 6 were already closed when this plan was written (the archive and R24/R26 landed in #168), so they
have no scenario.

## Out of scope

- D34's upgrade gaps: a follow-up plan (D2).
- D39's agent-skills edits (D7).
- Making the S24 receipt visible in the repository beyond the narrative line (D3); the ledger stays its record.
- Guarding the ledger itself against a hookless worker: the ledger lives in the state dir, outside every worktree.

## Risks

- **Security:** S3 pins the sha256 check that stops a rewritten receipt from counting. Its evidence passes at base,
  so it carries `ignore-receipt-sha.patch`, which removes the check: the oracle must fail then. This is the first
  plan held to D1's rule.
- **Performance:** n/a. The attention rebuild writes one small file per tick from data already in memory.
- **Migration:** a running plan's existing attention.md is overwritten on its next tick, which drops lines for
  tickets already complete: the intended fix. D1 applies to any plan `plan check` reads; 0 of 9 existing YAML plans
  is affected, and editing an approved one to comply needs a new `plan approve`.
- **Rollback:** each ticket is one squash-merged PR and can be reverted alone; T4's deletion comes back by revert.

## Seams

- `plan check` and `plan schema`: `load_script().run_cli([...])` in process, as in `tests/test_plan.py`.
- `verify`: the CLI subprocess through `YamlPlanFixture` (`tests/test_yaml_eec.py`), on a plan driven to ARCHIVED
  with the fake runtime and gh.
- attention.md: `DeciderFixture` (`tests/test_decider.py`), the real driver in process over a person-owned ticket.
- wait_checks: an import and grep test beside `tests/test_markdown_removed.py`.

The outcome oracle `test_yaml_eec_gaps.py` drives S1, S3 and S4 through these seams, one class per scenario, each
with control cases that pass before and after.
