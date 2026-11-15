# Acceptance

`test_evidence_selectors.py` is placed at `packages/agent-orchestrator/acceptance/plan/` for S1's check. It
reproduces the two real cases (checks over tests not yet written; `-k "S1 or control"` selecting `test_S10_...`)
through `plan check` and `plan approve`, and the controls the rule must leave alone. The plan discards: the file is
not promoted; the unit-level tests live in `packages/agent-orchestrator/tests/test_plan_check_selectors.py`.
