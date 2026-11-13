// Answers as `agent-orchestrator workbench plans|plan --json` prints them (what the read commands guarantee).

export const REPO = "/work/agent-runtime";
const DIR = REPO + "/docs/plans/";

export const PLANS = {
  schema_version: 1,
  repo: REPO,
  plans: [
    // Printed first although its file is the oldest: the order is the command's (newest activity), not the file name's.
    {
      slug: "broken", format: "yaml", path: DIR + "2026-11-09-broken.plan.yaml", legacy: false,
      state: null, group: "active", last_activity: "2026-11-12T11:00:00+00:00", tickets: null,
      error: { kind: "invalid_plan", message: "cycle: T1 -> T2 -> T1" },
    },
    {
      slug: "agent-workbench", format: "yaml", path: DIR + "2026-11-11-agent-workbench.plan.yaml", legacy: false,
      state: "RUNNING", group: "active", last_activity: "2026-11-12T10:00:00+00:00",
      tickets: { total: 3, by_state: { COMPLETE: 1, "IN PROGRESS": 1, "TO-DO": 1 } },
    },
    {
      slug: "second", format: "yaml", path: DIR + "2026-11-10-second.plan.yaml", legacy: false,
      state: "APPROVED", group: "active", last_activity: "2026-11-10T09:00:00+00:00",
      tickets: { total: 1, by_state: { "TO-DO": 1 } },
    },
    {
      slug: "old-plan", format: "markdown", path: DIR + "2026-10-30-old-plan.plan.md", legacy: true,
      state: "ARCHIVED", group: "older", last_activity: "2026-10-30T09:00:00+00:00",
      tickets: { total: 3, by_state: { COMPLETE: 3 } },
    },
  ],
  needs_me: [
    { plan: "agent-workbench", ticket: "T2", kind: "needs_you" },
    { plan: "second", ticket: null, kind: "ready_to_drive" },
    { plan: "broken", ticket: null, kind: "fix_plan" },
  ],
};

export const WORKBENCH_PATH = DIR + "2026-11-11-agent-workbench.plan.yaml";
export const SECOND_PATH = DIR + "2026-11-10-second.plan.yaml";
export const BROKEN_PATH = DIR + "2026-11-09-broken.plan.yaml";

export const PLAN = {
  schema_version: 1,
  slug: "agent-workbench",
  format: "yaml",
  legacy: false,
  path: WORKBENCH_PATH,
  repo: REPO,
  goal: "From one native Herdr pane, a person sees a plan's real ticket graph",
  state: "RUNNING",
  revision: { approved: true, drift: false, approved_digest: "aaa111", current_digest: "aaa111" },
  counts: { total: 3, by_state: { COMPLETE: 1, "IN PROGRESS": 1, "TO-DO": 1 } },
  tickets: [
    {
      id: "T1", delivers: "`workbench plans` and\n`workbench plan`, read-only", blocked_by: [], layer: 0,
      state: "COMPLETE", reason: null, pr: "https://github.com/o/r/pull/1", verified: true,
      attempts: [{
        task_id: "t1", kind: "worker", status: "COMPLETE", started_at: "2026-11-11T10:00:00+00:00",
        finished_at: "2026-11-11T10:30:00+00:00", assignment_id: "a1", address: "t1sf", claimed: true, latest: true,
      }],
      gates: [{ scenario: "S1", phase: "ticket", result: "PASS", reason: null }],
    },
    {
      id: "T2", delivers: "Attempts, gates and outcome", blocked_by: ["T1"], layer: 1,
      state: "IN PROGRESS", reason: null, pr: null, verified: false, attempts: [], gates: [],
    },
    {
      id: "T3", delivers: "The mod foundation", blocked_by: ["T1", "T2"], layer: 2,
      state: "TO-DO", reason: null, pr: null,
    },
  ],
};

export const TOTALS = {
  schema_version: 1,
  window: { days: 7 },
  cards: {
    merged: 3, first_pass_rate: 0.5, escalations: 2,
    tokens: { input: 100, output: 40, measured: 1, tickets: 3 }, median_ticket_seconds: 2400,
  },
};

export const ok = (value) => ({ code: 0, stdout: JSON.stringify(value), stderr: "" });
export const refusal = (kind, message, wayOut) => ({
  code: 2,
  stdout: JSON.stringify({ schema_version: 1, error: { kind, message, way_out: wayOut } }),
  stderr: "workbench: " + message + "\n",
});

// What `workbench plan --json` adds for a driven plan (`metrics`), as the orchestrator computes it. T3 never ran.
export const METRICS = {
  wall_seconds: 3725,
  span: { start: "2026-11-11T10:00:00+00:00", end: "2026-11-11T12:00:00+00:00" },
  first_pass_rate: 0.5,
  errors: { by_gate: { ci: 2, review: 1 }, last: "gate ci failed" },
  tickets: {
    T1: {
      elapsed_seconds: 1800, phases: { work: 1200, review: 540, ci: null }, fix_rounds: 1, review_rounds: 2,
      findings: 3, ci_failures: 0, escalations: { review_loop: 1 }, model: "opus", tokens: null,
    },
    T2: {
      elapsed_seconds: null, phases: { work: 0, review: null, ci: null }, fix_rounds: 0, review_rounds: 0,
      findings: 0, ci_failures: 1, escalations: {}, model: null, tokens: null,
    },
  },
  timeline: {
    T1: [
      { phase: "work", start: "2026-11-11T10:00:00+00:00", end: "2026-11-11T10:20:00+00:00" },
      { phase: "review", start: "2026-11-11T10:20:00+00:00", end: "2026-11-11T10:30:00+00:00" },
    ],
    T2: [{ phase: "work", start: "2026-11-11T11:00:00+00:00", end: null }],
  },
};
