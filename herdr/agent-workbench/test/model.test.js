import test from "node:test";
import assert from "node:assert/strict";
import {
  readPlans, readPlan, planRows, pickPlan, ticketLayers, inspector, relatives, planHeader,
  layoutFor, screenFor, compactRow, notice, pickTicket, NARROW_BELOW,
} from "../model.js";
import { PLANS, PLAN, WORKBENCH_PATH, SECOND_PATH, BROKEN_PATH, ok, refusal } from "./fixtures.js";

const rows = () => planRows(readPlans(ok(PLANS)).value.plans);

test("S12 plans list YAML with state and progress and Markdown marked legacy, active before archived, newest first", () => {
  const list = rows();
  assert.deepEqual(list.map((r) => r.slug), ["agent-workbench", "second", "broken", "old-plan"]);
  const yaml = list[0];
  assert.equal(yaml.state, "RUNNING");
  assert.deepEqual(yaml.progress, { done: 1, working: 1, blocked: 0, total: 3 });
  assert.equal(yaml.legacy, false);
  assert.equal(yaml.selectable, true);
  const legacy = list[3];
  assert.equal(legacy.legacy, true);
  assert.equal(legacy.selectable, false, "a Markdown plan has no graph to open");
});

test("S12 the first runnable YAML plan is selected unless the person picked one that is still listed", () => {
  const list = rows();
  assert.equal(pickPlan(list, null), WORKBENCH_PATH);
  assert.equal(pickPlan(list, SECOND_PATH), SECOND_PATH);
  assert.equal(pickPlan(list, "/gone.plan.yaml"), WORKBENCH_PATH);
  assert.equal(pickPlan([], null), null);
  assert.equal(pickPlan(planRows([PLANS.plans[0]]), null), null, "only a legacy plan: nothing to open");
});

test("S12 tickets are grouped into layers in the answer's order, each with blocked-by chips that carry the blocker's state", () => {
  const layers = ticketLayers(PLAN.tickets);
  assert.deepEqual(layers.map((l) => [l.layer, l.tickets.map((t) => t.id)]), [[0, ["T1"]], [1, ["T2"]], [2, ["T3"]]]);
  const t3 = layers[2].tickets[0];
  assert.deepEqual(t3.chips.map((c) => [c.id, c.look.key]), [["T1", "done"], ["T2", "working"]]);
  assert.equal(t3.summary, "The mod foundation");
  assert.equal(layers[0].tickets[0].summary, "`workbench plans` and `workbench plan`, read-only".replace(/`/g, ""));
  assert.equal(layers[0].tickets[0].look.key, "done");
  assert.equal(layers[0].tickets[0].verified.word, "verified");
});

test("S12 one inspector gives delivers, blockers, state, verified, attempts and gates of the selected ticket", () => {
  const view = inspector(PLAN.tickets[0], PLAN.tickets);
  assert.match(view.delivers, /workbench plans/);
  assert.equal(view.state.word, "complete");
  assert.equal(view.verified.word, "verified");
  assert.equal(view.attempts.length, 1);
  assert.equal(view.attempts[0].taskId, "t1");
  assert.equal(view.attempts[0].latest, true);
  assert.equal(view.attempts[0].claimed, true);
  assert.deepEqual(view.gates.map((g) => [g.scenario, g.result]), [["S1", "PASS"]]);
  const t3 = inspector(PLAN.tickets[2], PLAN.tickets);
  assert.deepEqual(t3.blockers.map((b) => [b.id, b.look.word]), [["T1", "complete"], ["T2", "in progress"]]);
  assert.equal(t3.verified.word, "unknown", "a ticket the answer does not say is verified is unknown, not verified");
  assert.equal(t3.attempts, null, "an answer without attempts says so; none are made up");
  assert.equal(t3.gates, null);
  assert.equal(inspector(PLAN.tickets[1], PLAN.tickets).verified.word, "not verified");
});

test("S12 selecting a ticket names its blockers and dependents", () => {
  assert.deepEqual(relatives(PLAN.tickets, "T2"), { blockers: ["T1"], dependents: ["T3"] });
  assert.deepEqual(relatives(PLAN.tickets, "T1"), { blockers: [], dependents: ["T2", "T3"] });
  assert.deepEqual(relatives(PLAN.tickets, "nope"), { blockers: [], dependents: [] });
});

test("S12 the inspector opens on the ticket in progress unless the person picked one that is still there", () => {
  assert.equal(pickTicket(PLAN.tickets, null), "T2");
  assert.equal(pickTicket(PLAN.tickets, "T3"), "T3");
  assert.equal(pickTicket(PLAN.tickets, "gone"), "T2");
  assert.equal(pickTicket([PLAN.tickets[0]], null), "T1");
  assert.equal(pickTicket([], null), null);
});

test("S12 the header carries the plan, its state, one progress bar and the drift when the plan changed after approval", () => {
  const head = planHeader(PLAN);
  assert.equal(head.slug, "agent-workbench");
  assert.equal(head.state.word, "RUNNING");
  assert.deepEqual(head.progress, { done: 1, working: 1, blocked: 0, total: 3 });
  assert.equal(head.drift, null);
  const drifted = planHeader({ ...PLAN, revision: { ...PLAN.revision, drift: true, current_digest: "bbb222" } });
  assert.deepEqual(drifted.drift, { approved: "aaa111", current: "bbb222" });
});

test("S13 a command that fails, times out, prints no JSON or the wrong JSON is an error of its kind with a way out", () => {
  const failed = readPlans({ code: 1, stdout: "", stderr: "agent-orchestrator: boom\nTraceback" });
  assert.equal(failed.ok, false);
  assert.equal(failed.error.kind, "failed");
  assert.match(failed.error.message, /boom/);
  assert.ok(failed.error.wayOut);
  const slow = readPlans({ thrown: "exec timed out after 20000 ms" });
  assert.equal(slow.error.kind, "timeout");
  assert.ok(slow.error.wayOut);
  const missing = readPlans({ thrown: "No such file or directory: agent-orchestrator" });
  assert.equal(missing.error.kind, "not_started");
  assert.match(missing.error.wayOut, /agent-orchestrator/);
  assert.equal(readPlans({ code: 0, stdout: "hello", stderr: "" }).error.kind, "no_json");
  assert.equal(readPlans({ code: 0, stdout: JSON.stringify({ nothing: 1 }), stderr: "" }).error.kind, "no_json");
  assert.equal(readPlan({ code: 0, stdout: JSON.stringify(PLANS), stderr: "" }).error.kind, "no_json");
  for (const bad of [failed, slow, missing]) assert.equal(bad.value, undefined, "no placeholder data is drawn");
});

test("S13 a scoped plan error keeps the CLI's kind and way out, and names the plan", () => {
  const answer = readPlan(refusal("invalid_plan", "cycle: T1 -> T2", "fix the plan file and run plan check"));
  assert.equal(answer.ok, false);
  assert.equal(answer.error.kind, "invalid_plan");
  assert.equal(answer.error.wayOut, "fix the plan file and run plan check");
  const shown = notice(answer.error, { plan: "broken" });
  assert.deepEqual(shown.scope, { kind: "plan", name: "broken" });
  assert.match(shown.message, /cycle/);
  assert.match(shown.wayOut, /plan check/);
  assert.equal(shown.tone, "blocked");
});

test("S13 no plans is shown for the project with /start-planning, a plain refusal keeps its own way out", () => {
  const none = readPlans(refusal("no_plans", "no plan files in /r", "pass an explicit plan path or --repo R"));
  const shown = notice(none.error, { project: "agent-runtime" });
  assert.deepEqual(shown.scope, { kind: "project", name: "agent-runtime" });
  assert.match(shown.wayOut, /\/start-planning/);
  assert.equal(shown.tone, "muted", "an empty project is not a fault");
  const outside = readPlans(refusal("not_a_repo", "not a repository", "run the command inside a repository"));
  assert.match(notice(outside.error, { project: "x" }).wayOut, /inside a repository/);
});

test("S13 a plan that does not load is a row with its error; the other plans stay selectable", () => {
  const list = rows();
  const broken = list.find((r) => r.slug === "broken");
  assert.equal(broken.error.kind, "invalid_plan");
  assert.equal(broken.progress, null, "null counts are not zero progress");
  assert.equal(broken.selectable, true, "its error is shown in place when it is selected");
  assert.equal(list.filter((r) => r.selectable && !r.error).length, 2);
  assert.equal(pickPlan(list, BROKEN_PATH), BROKEN_PATH);
});

test("S14 below the threshold the mod is narrow, at it and above (or before it is measured) it is wide", () => {
  assert.equal(layoutFor(NARROW_BELOW - 1), "narrow");
  assert.equal(layoutFor(NARROW_BELOW), "wide");
  assert.equal(layoutFor(1600), "wide");
  assert.equal(layoutFor(null), "wide");
  assert.equal(layoutFor(undefined), "wide");
});

test("S14 wide shows layers and the inspector side by side, narrow one column and the inspector as a drill-in", () => {
  assert.equal(screenFor("wide", false, true), "split");
  assert.equal(screenFor("wide", true, true), "split");
  assert.equal(screenFor("narrow", false, true), "list");
  assert.equal(screenFor("narrow", true, true), "inspector");
  assert.equal(screenFor("narrow", true, false), "list", "nothing selected: nothing to drill into");
});

test("S14 a compact row is one line: glyph, id, summary and state", () => {
  const [t1, t2] = ticketLayers(PLAN.tickets).flatMap((l) => l.tickets);
  assert.equal(compactRow(t1), "✓ T1 workbench plans and workbench plan, read-only · complete");
  assert.equal(compactRow(t2), "◉ T2 Attempts, gates and outcome · in progress");
});
