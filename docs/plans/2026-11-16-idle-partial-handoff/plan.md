# Idle partial handoff

- [x] S1: WHEN a worker writes a valid current-attempt contract 3 PARTIAL or BLOCKED report, sends progress, and becomes idle or done, THEN the driver asks the Lead to resolve the blocker, including the report's summary and remaining work.
- [x] S2: WHEN an idle worker's report is stale, invalid, or belongs to another task, THEN it is not adopted; progress text alone never becomes a blocker.
- [x] S3: WHEN an idle worker has a valid current-attempt COMPLETE report, THEN existing recovery continues. Other roles retain their own handoff rules.

Seam: driver receives runtime wait results and processes the worker report file. Integration fixture exercises the real driver and fake runtime boundary.
