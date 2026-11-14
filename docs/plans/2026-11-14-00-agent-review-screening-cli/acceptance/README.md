# Acceptance evidence

`test_screening_cli.py` proves S2, S3 and S4 at the CLI seam the decisions fix (brief.md P0-D1–P0-D3). It drives the
`agent-review` console script as a subprocess over a real two-revision git repository, its own YAML configuration and
a canned screening response, so it imports nothing from the package and collects cleanly before the package exists;
it fails at base, and each class carries a control that passes either way.

The archive promotes it to `packages/agent-review/acceptance/screening/`, where it keeps proving the requirements the
archive writes into `docs/specs/agent-review.md`.

S1 stays human evidence in `plan.yaml`: it needs a real provider credential and a real charge, which no check in
this repository may spend. Do not substitute unit-test counts for behavioral acceptance.
