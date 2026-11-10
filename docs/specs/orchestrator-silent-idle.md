# Orchestrator-Silent-Idle

Paths: packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_gates.py, packages/agent-orchestrator/tests/test_wake.py, packages/agent-orchestrator/tests/test_decider.py, packages/agent-orchestrator/tests/test_report_timing.py, packages/agent-orchestrator/tests/test_envelope.py, packages/agent-orchestrator/tests/test_pr.py

An agent whose turn ended while it is still live (a reviewer, worker or answerer idle on a background job) is not
treated as finished. The driver waits for its complete report under the assignment it was given, and only the timed
idle watchdog asks the Lead, naming the agent and the way out. The engine's general requirements stay in
`orchestrator-engine.md`; this file states the silent-idle contract.

## Requirements

- **R1** WHEN a live reviewer ends its turn idle or done several times with no review file and no report, then writes a valid review and reports complete under its original assignment before the idle watchdog fires, THEN the review is accepted and the unit completes as usual (reviewed_commit recorded), with no resume, no assignment replaced, no payload-warning or contract-error for the review, and no Lead request (from orchestrator-silent-idle/S1)
- **R2** WHEN a live reviewer is idle or done with a valid review file on disk but has sent no complete report, THEN its gate stays reviewing and no verdict is recorded (from orchestrator-silent-idle/S2)
- **R3** WHEN an idle reviewer is observed on many loop iterations before the next timed idle tick, THEN the observations consume no idle ticks and raise no request (from orchestrator-silent-idle/S3)
- **R4** WHEN a live reviewer never reports and its unit's signature stays unchanged through the baseline and two timed idle ticks, THEN exactly one request with event idle and one decision wake are written, naming the reviewer, the expected review file, the inactivity and the `decide` options; no warning resume and no payload error precede it (from orchestrator-silent-idle/S4)
- **R5** WHEN a live worker ends its turn idle or done with no valid report on disk, THEN it stays pending, with no immediate exited request, until its matching complete or the same idle watchdog (from orchestrator-silent-idle/S5)
- **R6** WHEN a worker ends its turn idle with a valid report for its task already on disk, THEN the existing report_landed acceptance advances it as today (from orchestrator-silent-idle/S6)
- **R7** WHEN a live answerer ends its turn idle or done before reporting, THEN it stays working with no additional resume, and its later matching complete is accepted (from orchestrator-silent-idle/S7)
- **R8** WHEN that answerer never reports, THEN the idle watchdog raises one idle request, and the unit's PR-ready stays blocked on the answers (from orchestrator-silent-idle/S8)
- **R9** WHEN a complete report announces a review or report file that is missing or unreadable, THEN the R18 grace and the capped sender recovery still apply, and each recovery message names the file to write or repair and says to report complete under the current assignment, without claiming the sender's report was read (from orchestrator-silent-idle/S9)
- **R10** WHEN a reviewer whose runtime state is failed, a gone reviewer, or a restart-orphaned reviewer has no usable review, THEN the Lead is asked at once with zero warning resumes; an orphan with a valid review file is still accepted (from orchestrator-silent-idle/S10)
- **R11** WHEN the runtime reports unreported or error, or an agent reports blocked or decision, THEN the attention is handled at once, as today, not deferred to the idle watchdog (from orchestrator-silent-idle/S11)
- **R12** WHEN a reviewer brief is written, THEN it says the reviewer may wait on background jobs, that ending a turn does not finish the review, and that the review is written after the jobs it needs finish and then reported complete with the brief's assignment id (from orchestrator-silent-idle/S12)
- **R13** WHEN a reviewer's complete report arrives after its unit's gate moved to requested, THEN the open Lead request stays open and the unit does not advance on its own (from orchestrator-silent-idle/S13)
- **R14** WHEN a unit is at awaiting-merge, THEN the idle ticks still skip it (from orchestrator-silent-idle/S14)
