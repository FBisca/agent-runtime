# Codex default role

Routine tasks need no implicit implementer instructions. All CLI families and named profiles default to no role; callers select a role for specific work.

- **S1** WHEN Codex spawn omits `agent`, THEN it uses the same roleless prompt and report protocol as `agent="none"`, without reading implementer.toml.
- **S2** WHEN Codex spawn explicitly selects `implementer`, THEN its instructions are loaded; a missing file fails before starting a terminal.
- **S4** WHEN dispatch is disabled, THEN a roleless worker receives the legacy `report(text)` protocol without an assignment ID.
- **S3** WHEN Claude or Pi spawn omits `agent`, THEN it matches `agent="none"` and receives the report protocol; explicitly selecting `implementer` preserves its role.

| Ticket | Scenarios | Status |
|---|---|---|
| Change all CLI defaults, tests, schema and documentation | S1–S4 | COMPLETE |
