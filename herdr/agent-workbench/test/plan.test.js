import test from "node:test";
import assert from "node:assert/strict";
import { planCards, ticketCard, planTimeline, cardLayers, planColumns, liveTerminals } from "../model.js";
import { RICH_PLAN as PLAN, HOME_TERMINALS, GH } from "./fixtures.js";

// An open plan as `workbench plan --json` prints it; terminals as Herdr's terminals() lists them.
const live = () => liveTerminals(HOME_TERMINALS, null);

test("S16 plan has five cards from its own metrics: progress, first pass, wall time, escalations and tokens with coverage", () => {
  const cards = planCards(PLAN);
  assert.deepEqual(cards.map((c) => c.id), ["progress", "first_pass", "wall", "escalations", "tokens"]);
  const by = Object.fromEntries(cards.map((c) => [c.id, c]));
  assert.equal(by.progress.value, "1/3");
  assert.equal(by.first_pass.value, "50%");
  assert.equal(by.wall.value, "2h 00m");
  assert.equal(by.escalations.value, "3");
  assert.equal(by.tokens.value, "52.0k");
  assert.equal(by.tokens.note, "1 of 3 tickets measured", "coverage is part of the number");
});

test("S16 plan reads a value its metrics do not carry as unknown, never 0", () => {
  const bare = planCards({ ...PLAN, metrics: null });
  assert.deepEqual(bare.map((c) => [c.id, c.value]), [
    ["progress", "1/3"], ["first_pass", "unknown"], ["wall", "unknown"], ["escalations", "unknown"], ["tokens", "unknown"],
  ]);
  assert.ok(bare.every((c) => c.picture === null || c.id === "progress"), "a card with no data has no picture");
  const partial = planCards({ ...PLAN, metrics: { ...PLAN.metrics, wall_seconds: null, first_pass_rate: null, tickets: {} } });
  assert.deepEqual(partial.map((c) => c.value), ["1/3", "unknown", "unknown", "unknown", "unknown"]);
});

test("S16 plan's cards picture themselves from the same metrics", () => {
  const by = Object.fromEntries(planCards(PLAN).map((c) => [c.id, c.picture]));
  assert.deepEqual(by.progress, { kind: "ring", value: 1 / 3, tone: "done" });
  assert.deepEqual(by.first_pass, { kind: "ring", value: 0.5, tone: null });
  assert.deepEqual(by.wall.parts.map((p) => [p.key, p.value]), [["work", 2400], ["review", 600], ["ci", 60], ["merge", 1200]]);
  assert.deepEqual(by.escalations.parts.map((p) => [p.key, p.value]), [["gate", 2], ["idle", 1]]);
  assert.deepEqual(by.tokens, { kind: "coverage", measured: 1, unmeasured: 2 });
});

test("S17 plan's ticket is a card of its measures, phase split, dependencies, agents and PR", () => {
  const t1 = ticketCard(PLAN.tickets[0], PLAN.tickets, PLAN.metrics, live());
  assert.deepEqual(t1.measures, ["1 round", "4 findings", "1h 00m", "52.0k tok", "sonnet"]);
  assert.deepEqual(t1.phases.map((p) => p.step), ["work", "review", "ci", "merge"]);
  assert.equal(t1.idle, 2);
  assert.deepEqual([t1.linkText, t1.link], ["#1", GH(1)]);
  const t2 = ticketCard(PLAN.tickets[1], PLAN.tickets, PLAN.metrics, live());
  assert.equal(t2.measures[0], "first pass");
  assert.deepEqual(t2.live.map((w) => w.name), ["alpha-t2"]);
  assert.deepEqual(t2.after.map((c) => c.id), ["T1"]);
  assert.deepEqual(t2.phases.map((p) => p.step), ["work"], "an unmeasured phase is no segment");
});

test("S17 plan's ticket with no metrics shows none, and a PR that is not GitHub's reads #n without a link", () => {
  const t3 = ticketCard(PLAN.tickets[2], PLAN.tickets, PLAN.metrics, live());
  assert.deepEqual([t3.measures, t3.phases], [[], null]);
  assert.deepEqual([t3.linkText, t3.link], ["#3", null]);
  const none = ticketCard(PLAN.tickets[1], PLAN.tickets, null, live());
  assert.deepEqual([none.measures, none.phases], [[], null]);
  assert.equal(ticketCard(PLAN.tickets[1], PLAN.tickets, PLAN.metrics, null).live.length, 0, "no terminals, no live agent");
});

test("S17 plan's ticket cards come by layer with each layer's done count", () => {
  const layers = cardLayers(PLAN.tickets, PLAN.metrics, live());
  assert.deepEqual(layers.map((l) => [l.layer, l.done, l.tickets.map((t) => t.id)]), [[0, 1, ["T1"]], [1, 0, ["T2", "T3"]]]);
});

test("S17 plan's timeline runs on the plan's clock and marks each merge, and a narrow panel gets the phase split", () => {
  const wide = planTimeline(PLAN.metrics, PLAN.tickets, "wide");
  assert.equal(wide.kind, "timeline");
  const lane = wide.lanes.find((l) => l.id === "T1");
  assert.equal(lane.merged, 0.5);
  assert.deepEqual([lane.segments[0].step, lane.segments[0].left], ["work", 0]);
  assert.ok(Math.abs(lane.segments[0].width - 20 / 240) < 1e-9);
  assert.ok(Math.abs(wide.lanes.find((l) => l.id === "T2").segments[0].left - 0.5) < 1e-9);
  assert.equal(wide.lanes.find((l) => l.id === "T2").merged, null);
  assert.deepEqual(wide.lanes.find((l) => l.id === "T3").segments, []);
  assert.deepEqual(wide.ticks.length, 5, "the clock is read off five ticks");
  const narrow = planTimeline(PLAN.metrics, PLAN.tickets, "narrow");
  assert.equal(narrow.kind, "split");
  assert.deepEqual(narrow.lanes.find((l) => l.id === "T1").phases.map((p) => p.step), ["work", "review", "ci", "merge"]);
  assert.equal(narrow.lanes.find((l) => l.id === "T3").phases, null);
});

test("S17 plan draws no timeline for a plan with no metrics or no span", () => {
  assert.equal(planTimeline(null, PLAN.tickets, "wide"), null);
  assert.equal(planTimeline(null, PLAN.tickets, "narrow"), null);
  assert.equal(planTimeline({ ...PLAN.metrics, span: { start: null, end: null } }, PLAN.tickets, "wide"), null);
});

test("S17 plan lays its cards and tickets out by the width the inspector leaves", () => {
  assert.deepEqual(planColumns("narrow", 400), { cards: 2, tickets: 1, inspector: false });
  assert.deepEqual(planColumns("wide", 800), { cards: 2, tickets: 1, inspector: true });
  assert.deepEqual(planColumns("wide", 1200), { cards: 5, tickets: 2, inspector: true });
  assert.deepEqual(planColumns("wide", null), { cards: 5, tickets: 2, inspector: true }, "unmeasured is wide");
});
