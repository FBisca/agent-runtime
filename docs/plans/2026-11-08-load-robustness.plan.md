# Load robustness: a session's identity, a wait's probe and the harness's budgets

A parallel run made the suite red on the same tree: the ancestry walk, the status probe of a wait
and the harness's read budget all measured the machine instead of the behaviour they stand for.
A session's identity that changes between two calls forks its team, a probe that misses its budget
hides the failure it was sent to see, and a harness that gives up before the server answers reports
an empty queue for a reply that is on its way.

- **S1** WHEN a session's generation is resolved and a later call's ancestry walk misses its budget, THEN the session keeps the generation it resolved first, the team does not fork, and the workers of the first team do not go stale.
- **S2** WHEN the status probe of a wait needs more than 0.2s, THEN the wait still observes it and reports the failed or blocked worker instead of the queued report.
- **S3** WHEN the harness waits for a reply, THEN its budget outlasts the server's own deadline, and the wall-clock bounds a test asserts are margins the machine can meet.

| Ticket | Scenarios | Status |
|---|---|---|
| Resolve the session identity once, give the probe and the harness the budgets they need | S1–S3 | COMPLETE |
