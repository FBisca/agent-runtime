import test from "node:test";
import assert from "node:assert/strict";
import { planHeader, inspector, timelineOf } from "../model.js";
import { PLAN, METRICS } from "./fixtures.js";

const measured = { ...PLAN, metrics: METRICS };
const rows = (view) => Object.fromEntries(view.measures.map((r) => [r.label, r.text]));

test("S31 the plan header shows wall time, first-pass rate and driver errors", () => {
  const head = planHeader(measured);
  assert.deepEqual(head.metrics, { wall: "1h 02m", firstPass: "50%", errors: "3 (ci 2, review 1)" });
});

test("S31 a plan without metrics, or with unmeasured values, reads unknown and never 0", () => {
  assert.equal(planHeader(PLAN).metrics, null, "nothing is drawn for a plan never driven");
  const bare = planHeader({ ...PLAN, metrics: { ...METRICS, wall_seconds: null, first_pass_rate: null, errors: null } });
  assert.deepEqual(bare.metrics, { wall: "unknown", firstPass: "unknown", errors: "unknown" });
  assert.equal(planHeader({ ...PLAN, metrics: { ...METRICS, errors: { by_gate: {}, last: null } } }).metrics.errors, "none");
});

test("S31 the inspector shows the ticket's phase times, rounds, findings, CI failures, escalations and model", () => {
  const view = inspector(PLAN.tickets[0], PLAN.tickets, null, METRICS);
  assert.deepEqual(rows(view), {
    Elapsed: "30m 00s", Work: "20m 00s", Review: "9m 00s", CI: "unknown",
    "Fix rounds": "1", "Review rounds": "2", Findings: "3", "CI failures": "0", Escalations: "review_loop 1", Model: "opus",
  });
});

test("S31 unmeasured ticket values read unknown, a measured zero reads 0, and a ticket that never ran is all unknown", () => {
  const t2 = rows(inspector(PLAN.tickets[1], PLAN.tickets, null, METRICS));
  assert.equal(t2.Elapsed, "unknown");
  assert.equal(t2.Work, "0s");
  assert.equal(t2.Review, "unknown");
  assert.equal(t2.Model, "unknown");
  assert.equal(t2.Escalations, "none");
  const t3 = rows(inspector(PLAN.tickets[2], PLAN.tickets, null, METRICS));
  assert.ok(Object.values(t3).every((text) => text === "unknown"));
  assert.equal(inspector(PLAN.tickets[0], PLAN.tickets, null, null).measures, null);
});

test("S32 the timeline has one lane per ticket in ticket order, a segment per phase scaled to the run's span", () => {
  const timeline = timelineOf(METRICS, PLAN.tickets, "wide");
  assert.deepEqual(timeline.lanes.map((l) => l.id), ["T1", "T2", "T3"]);
  const placed = (lane) => lane.segments.map((s) => [s.phase, +s.left.toFixed(4), +s.width.toFixed(4)]);
  assert.deepEqual(placed(timeline.lanes[0]), [["work", 0, 0.1667], ["review", 0.1667, 0.0833]]);
  assert.deepEqual(placed(timeline.lanes[1]), [["work", 0.5, 0.5]], "an open segment runs to the span's end");
  assert.deepEqual(timeline.lanes[2].segments, []);
});

test("S32 the timeline is drawn only wide and only for a plan with metrics and a span", () => {
  assert.equal(timelineOf(METRICS, PLAN.tickets, "narrow"), null);
  assert.equal(timelineOf(null, PLAN.tickets, "wide"), null);
  assert.equal(timelineOf({ ...METRICS, span: { start: null, end: null } }, PLAN.tickets, "wide"), null);
});
