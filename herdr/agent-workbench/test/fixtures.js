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

// ---- a week's totals as the CLI prints them since it carries each card's series, last week and the change ----------------

export const GH = (n) => `https://github.com/acme/repo/pull/${n}`;
export const WINDOW_END = "2026-11-13T12:00:00+00:00";
const DAYS = ["2026-11-07", "2026-11-08", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"];
const series = (field, values) => DAYS.map((day, i) => ({ day, [field]: values[i] }));

export const RICH_TOTALS = {
  schema_version: 1,
  window: {
    days: 7, start: "2026-11-06T12:00:00+00:00", end: WINDOW_END,
    previous: { start: "2026-10-30T12:00:00+00:00", end: "2026-11-06T12:00:00+00:00" },
  },
  cards: {
    merged: 4, first_pass_rate: 0.75, escalations: 3,
    tokens: { input: 100, output: 40, measured: 1, tickets: 4 }, median_ticket_seconds: 2400,
  },
  previous: {
    merged: 1, first_pass_rate: 1, escalations: 1,
    tokens: { input: null, output: null, measured: 0, tickets: 1 }, median_ticket_seconds: 1800,
  },
  change: { merged: 3, first_pass_rate: -0.25, escalations: 2, tokens: null, median_ticket_seconds: 0.3333 },
  merged: {
    by_day: DAYS.map((day, i) => (i === 5
      ? { day, count: 3, by_plan: { alpha: 2, beta: 1 } }
      : i === 6 ? { day, count: 1, by_plan: { gamma: 1 } } : { day, count: 0, by_plan: {} })),
    tickets: [
      { plan: "gamma", ticket: "T1", pr: "https://example.invalid/pull/9", merged_at: "2026-11-13T11:55:00+00:00" },
      { plan: "alpha", ticket: "T2", pr: GH(2), merged_at: "2026-11-12T10:00:00+00:00" },
      { plan: "alpha", ticket: "T1", pr: GH(1), merged_at: "2026-11-12T09:00:00+00:00" },
      { plan: "beta", ticket: "T1", pr: null, merged_at: "2026-11-12T08:00:00+00:00" },
    ],
  },
  first_pass: {
    by_day: series("rate", [null, null, null, null, null, 0.6667, 1]),
    by_plan: { alpha: 0.5, beta: 1, gamma: 1 },
    by_reason: { "review/concern": 2, "verify/blocking": 1 },
    rounds: { 0: 3, 1: 1, 2: 0, "3+": 0 },
    fixed: [{ plan: "alpha", ticket: "T2", fix_rounds: 1, findings: 3, reasons: { "review/concern": 2, "verify/blocking": 1 } }],
  },
  escalations: {
    by_day: DAYS.map((day, i) => (i === 4 ? { day, count: 1, by_cause: { gate: 1 } }
      : i === 6 ? { day, count: 2, by_cause: { blocked: 1, ci: 1 } } : { day, count: 0, by_cause: {} })),
    by_cause: { gate: 1, blocked: 1, ci: 1 },
    by_plan: { alpha: 2, beta: 1 },
    by_plan_cause: { alpha: { gate: 1, ci: 1 }, beta: { blocked: 1 } },
    latest: [
      { plan: "alpha", ticket: null, unit: "archive", cause: "ci", at: "2026-11-13T11:55:00+00:00" },
      { plan: "beta", ticket: "T1", unit: "T1", cause: "blocked", at: "2026-11-13T09:00:00+00:00" },
      { plan: "alpha", ticket: "T2", unit: "T2", cause: "gate", at: "2026-11-11T10:00:00+00:00" },
    ],
  },
  tokens: {
    by_day: series("output", [null, null, null, null, null, 40, null]),
    by_plan: {
      alpha: { input: 100, output: 40, measured: 1, tickets: 2 },
      beta: { input: null, output: null, measured: 0, tickets: 1 },
      gamma: { input: null, output: null, measured: 0, tickets: 1 },
    },
    by_model: { sonnet: { input: 100, output: 40, tickets: 1 } },
    top: [{ plan: "alpha", ticket: "T1", input: 100, output: 40, model: "sonnet" }],
  },
  time: {
    by_day: series("median", [null, null, null, null, null, 2400, 1800]),
    steps: {
      work: { median: 600, p90: 1200, max: 1500 }, review: { median: 120, p90: 300, max: 400 },
      ci: { median: 60, p90: 90, max: 100 }, merge: { median: 900, p90: 3000, max: 3200 },
    },
    slowest: [{ plan: "alpha", ticket: "T2", pr: GH(2), seconds: 3660 }],
    tickets: [
      { plan: "alpha", ticket: "T2", pr: GH(2), seconds: 3660, phases: { work: 120, review: 120, ci: 120, merge: 3240 } },
      { plan: "beta", ticket: "T1", pr: null, seconds: 2460, phases: { work: 120, review: 120, ci: 120, merge: null } },
      { plan: "gamma", ticket: "T1", pr: "https://example.invalid/pull/9", seconds: 1860, phases: { work: 120, review: 120, ci: 120, merge: 1440 } },
      { plan: "alpha", ticket: "T1", pr: GH(1), seconds: 1260, phases: { work: 120, review: 120, ci: 120, merge: 840 } },
    ],
  },
};

// An answer as the CLI printed it before it carried the series: the cards and the old detail, none of the new sections.
export const OLD_TOTALS = {
  schema_version: 1,
  window: { days: 7, start: RICH_TOTALS.window.start, end: WINDOW_END },
  cards: RICH_TOTALS.cards,
  merged: { by_day: [{ day: "2026-11-12", count: 3 }, { day: "2026-11-13", count: 1 }], tickets: RICH_TOTALS.merged.tickets },
  first_pass: { by_plan: RICH_TOTALS.first_pass.by_plan, fixed: [{ plan: "alpha", ticket: "T2", fix_rounds: 1, findings: 3 }] },
  escalations: {
    by_cause: RICH_TOTALS.escalations.by_cause, by_plan: RICH_TOTALS.escalations.by_plan,
    latest: RICH_TOTALS.escalations.latest.map(({ unit, ...rest }) => rest),
  },
  tokens: { by_plan: RICH_TOTALS.tokens.by_plan, by_model: RICH_TOTALS.tokens.by_model, top: RICH_TOTALS.tokens.top },
  time: {
    steps: Object.fromEntries(Object.entries(RICH_TOTALS.time.steps).map(([k, { median, max }]) => [k, { median, max }])),
    slowest: RICH_TOTALS.time.slowest,
  },
};
