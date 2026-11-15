# Acceptance evidence

`test_smart_screening_cli.py` proves S1–S6 through the `agent-review` console script. The evidence runner places it at
`packages/agent-review/acceptance/screening/`, which is where each `evidence.check` in `plan.yaml` looks for it.

Every run builds a scratch repository whose base holds `store.py`, `docs/guide.md` and `db/migrations/001_init.sql` and
whose head renames the guide into `server/`, adds a migration and appends a ~5k-character module — the shapes S2, S3
and S4 need. The provider is canned in 00b's shape (`{responses: [{model, answers, usage}]}`), so no run needs a
credential and the model the provider served is the fixture's own.

Two couplings are deliberate:

- **The controls pass at base, the scenarios do not.** `FixtureControlTests` asserts the fixture's own setup (the rename
  landed, the module is big) and must stay green before and after; S1–S6 fail at base because the tool has no
  repository configuration, no sensitive-path routing, no chunking, no coverage gap, no calibrated escalation and no
  price table. A control that fails means the fixture is wrong, not the tool.
- **`git mv` needs its destination directory.** The head edit renames into `server/`, which does not exist at base, so
  the setup creates it; without that the whole file fails in `setUp` and proves nothing.

Do not weaken a scenario to make a ticket pass: `usage.requests == len(chunks)`, `coverage.changed_regions ==
coverage.covered`, the `rule` names and the `unknown`-not-zero cost are the contract P2 and P3 read.
