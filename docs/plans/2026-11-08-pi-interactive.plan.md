# Pi interactive workers

Status: OPEN

Pi's print mode exits after one task, preventing follow-ups and hiding its interactive terminal.

- S1 WHEN a Pi worker is spawned with or without model and effort THEN Pi starts interactively with the task last and without `--print`.
- S2 WHEN an interactive Pi worker completes a task THEN it remains available for a resumed assignment and reports that assignment.

| Ticket | Scenarios | Status |
|---|---|---|
| Launch Pi interactively and verify live resume | S1, S2 | IN PROGRESS |
