# Codex default role

Codex routine tasks need no implicit implementer instructions. Claude and Pi retain their defaults; named Codex-family profiles follow Codex.

- **S1** WHEN Codex spawn omits `agent`, THEN it uses the same roleless prompt and report protocol as `agent="none"`, without reading implementer.toml.
- **S2** WHEN Codex spawn explicitly selects `implementer`, THEN its instructions are loaded; a missing file fails before starting a terminal.
- **S3** WHEN Claude or Pi spawn omits `agent`, THEN its implementer default remains unchanged.

| Ticket | Scenarios | Status |
|---|---|---|
| Change Codex-family default, tests, schema and documentation | S1–S3 | COMPLETE |
