// Answers as `agent-orchestrator workbench plans|plan --json` prints them (what the read commands guarantee).

export const REPO = "/work/agent-runtime";
const DIR = REPO + "/docs/plans/";

export const PLANS = {
  schema_version: 1,
  repo: REPO,
  plans: [
    {
      slug: "old-plan", format: "markdown", path: DIR + "2026-10-30-old-plan.plan.md", legacy: true,
      state: "ARCHIVED", tickets: { total: 3, by_state: { COMPLETE: 3 } },
    },
    {
      slug: "broken", format: "yaml", path: DIR + "2026-11-09-broken.plan.yaml", legacy: false,
      state: null, tickets: null, error: { kind: "invalid_plan", message: "cycle: T1 -> T2 -> T1" },
    },
    {
      slug: "agent-workbench", format: "yaml", path: DIR + "2026-11-11-agent-workbench.plan.yaml", legacy: false,
      state: "RUNNING", tickets: { total: 3, by_state: { COMPLETE: 1, "IN PROGRESS": 1, "TO-DO": 1 } },
    },
    {
      slug: "second", format: "yaml", path: DIR + "2026-11-10-second.plan.yaml", legacy: false,
      state: "APPROVED", tickets: { total: 1, by_state: { "TO-DO": 1 } },
    },
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

export const ok = (value) => ({ code: 0, stdout: JSON.stringify(value), stderr: "" });
export const refusal = (kind, message, wayOut) => ({
  code: 2,
  stdout: JSON.stringify({ schema_version: 1, error: { kind, message, way_out: wayOut } }),
  stderr: "workbench: " + message + "\n",
});
