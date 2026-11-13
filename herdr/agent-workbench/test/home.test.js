import test from "node:test";
import assert from "node:assert/strict";
import { homeView, initialNav, navigate, liveTerminals, duration, readTotals, olderLabel } from "../model.js";

// Answers as `workbench plans|totals --json` print them; terminals as Herdr's terminals() lists them.
const PR = "https://example.invalid/pull/3";

const PLANS = {
  schema_version: 1,
  repo: "/r",
  plans: [
    {
      slug: "alpha", format: "yaml", path: "/r/docs/plans/alpha/plan.yaml", legacy: false, state: "RUNNING",
      tickets: { total: 3, by_state: { COMPLETE: 1, "IN PROGRESS": 2 } }, group: "active",
      last_activity: "2026-11-13T10:00:00+00:00",
      agents: [
        { name: "alpha-t2", role: "worker", ticket: "T2", address: "t9a" },
        { name: "alpha-t2-review", role: "reviewer", ticket: "T2", address: "t9b" },
        { name: "alpha-t1", role: "worker", ticket: "T1", address: "t9c" },
        { name: "alpha-t4", role: "worker", ticket: "T4", address: null },
      ],
      awaiting_merge: [{ ticket: "T3", pr: PR }],
    },
    {
      slug: "beta", format: "yaml", path: "/r/docs/plans/beta/plan.yaml", legacy: false, state: "RUNNING",
      tickets: { total: 1, by_state: { "IN PROGRESS": 1 } }, group: "active",
      last_activity: "2026-11-13T09:00:00+00:00", agents: [], awaiting_merge: [],
    },
    {
      slug: "old", format: "yaml", path: "/r/docs/plans/old/plan.yaml", legacy: false, state: "ARCHIVED",
      tickets: { total: 1, by_state: { COMPLETE: 1 } }, group: "older",
      last_activity: "2026-10-01T09:00:00+00:00", agents: [], awaiting_merge: [],
    },
  ],
  needs_me: [
    { plan: "beta", ticket: null, kind: "driver_gone" },
    { plan: "alpha", ticket: "T5", kind: "lead_pending" },
    { plan: "alpha", ticket: "T2", kind: "lead_pending" },
  ],
};

const TOTALS = {
  schema_version: 1,
  window: { days: 7 },
  cards: {
    merged: 3, first_pass_rate: 0.5, escalations: 2,
    tokens: { input: 100, output: 40, measured: 1, tickets: 3 }, median_ticket_seconds: 2400,
  },
};

// alpha-t1's ledger address t9c is not live; a terminal that merely carries its name lives at another address.
const TERMINALS = [
  { address: "t9a", state: "live", name: "Claude Code", status: "working", program: { agent: "claude" } },
  { address: "t9b", state: "live", name: "Claude Code", status: "done", program: { agent: "claude" } },
  { address: "t77", state: "live", name: "alpha-t1", status: "working", program: { agent: "claude" } },
];

const live = () => liveTerminals(TERMINALS, null);
const CARD_IDS = ["merged", "first_pass", "escalations", "tokens", "ticket_time"];

test("S7 home gives the five cards, Needs me one row per plan, the active plans with their workers, the PRs to merge and the older count", () => {
  const home = homeView(PLANS, TOTALS, live());
  assert.deepEqual(home.cards.map((c) => c.id), CARD_IDS);
  const card = (id) => home.cards.find((c) => c.id === id);
  assert.deepEqual(
    [card("merged").value, card("first_pass").value, card("escalations").value, card("ticket_time").value],
    ["3", "50%", "2", duration(2400)],
  );
  assert.equal(card("tokens").value, "140");
  assert.match(card("tokens").note, /1 of 3/);
  assert.deepEqual(home.needs.map((n) => [n.plan, n.items.map((i) => [i.ticket, i.kind])]), [
    ["beta", [[null, "driver_gone"]]],
    ["alpha", [["T5", "lead_pending"], ["T2", "lead_pending"]]],
  ]);
  assert.equal(home.needs[1].path, "/r/docs/plans/alpha/plan.yaml", "a row opens the plan it names");
  assert.ok(home.needs[1].items.every((i) => i.title && i.wayOut));
  assert.deepEqual(home.active.map((p) => p.slug), ["alpha", "beta"]);
  assert.deepEqual(home.active[1].workers, []);
  assert.deepEqual(
    home.active[0].workers.map((w) => [w.name, w.role, w.ticket, w.state, w.status, w.open?.args.terminal ?? null]),
    [
      ["alpha-t2", "worker", "T2", "live", "working", "t9a"],
      ["alpha-t2-review", "reviewer", "T2", "live", "done", "t9b"],
      ["alpha-t1", "worker", "T1", "not live", null, null],
      ["alpha-t4", "worker", "T4", "unknown", null, null],
    ],
  );
  assert.deepEqual(home.awaitingMerge, [{ plan: "alpha", ticket: "T3", pr: PR, path: "/r/docs/plans/alpha/plan.yaml" }]);
  assert.equal(home.older.count, 1);
  assert.deepEqual(home.older.rows.map((r) => r.slug), ["old"]);
  assert.equal(olderLabel(1, false), "1 older ▸");
  assert.equal(olderLabel(1, true), "1 older ▾");
});

test("S7 a card the window did not measure says unknown, and no totals answer means no cards", () => {
  const unmeasured = {
    ...TOTALS,
    cards: {
      ...TOTALS.cards, first_pass_rate: null, median_ticket_seconds: null,
      tokens: { input: null, output: null, measured: 0, tickets: 3 },
    },
  };
  const cards = homeView(PLANS, unmeasured, live()).cards;
  assert.equal(cards.find((c) => c.id === "tokens").value, "unknown");
  assert.match(cards.find((c) => c.id === "tokens").note, /0 of 3/);
  assert.equal(cards.find((c) => c.id === "first_pass").value, "unknown");
  assert.equal(cards.find((c) => c.id === "ticket_time").value, "unknown");
  assert.equal(homeView(PLANS, null, live()).cards, null);
});

test("S7 a week with nothing merged shows 0 merged and unknown for the rest, never a guess", () => {
  const none = {
    ...TOTALS,
    cards: { merged: 0, first_pass_rate: null, escalations: 0, tokens: { input: null, output: null, measured: 0, tickets: 0 }, median_ticket_seconds: null },
  };
  const cards = homeView(PLANS, none, live()).cards;
  assert.deepEqual(cards.map((c) => c.value), ["0", "unknown", "0", "unknown", "unknown"]);
});

test("S7 without Herdr's terminals every worker is unknown, never not live, and a plan with no needs or older plans has none to show", () => {
  const home = homeView({ ...PLANS, plans: PLANS.plans.slice(0, 2), needs_me: [] }, TOTALS, null);
  assert.deepEqual(home.active[0].workers.map((w) => w.state), ["unknown", "unknown", "unknown", "unknown"]);
  assert.deepEqual(home.needs, []);
  assert.equal(home.older.count, 0);
});

test("S7 the totals answer is read as the plans answer is: a refusal or a stray JSON is an error, never cards", () => {
  assert.equal(readTotals({ code: 0, stdout: JSON.stringify(TOTALS), stderr: "" }).ok, true);
  assert.equal(readTotals({ code: 0, stdout: JSON.stringify({ plans: [] }), stderr: "" }).ok, false);
  const refused = readTotals({ code: 2, stdout: JSON.stringify({ error: { kind: "no_plans", message: "none" } }), stderr: "" });
  assert.equal(refused.ok, false);
  assert.equal(refused.error.kind, "no_plans");
});

test("S8 the mod opens on home, a plan or a card opens over it, and back returns home", () => {
  const home = initialNav();
  assert.deepEqual(home, { screen: "home" });
  const plan = navigate(home, { type: "plan", path: "/r/docs/plans/alpha/plan.yaml" });
  assert.deepEqual(plan, { screen: "plan", path: "/r/docs/plans/alpha/plan.yaml" });
  assert.deepEqual(navigate(plan, { type: "back" }), { screen: "home" });
  const card = navigate(home, { type: "card", id: "ticket_time" });
  assert.deepEqual(card, { screen: "dashboard", card: "ticket_time" });
  assert.deepEqual(navigate(card, { type: "back" }), { screen: "home" });
});

test("S8 back on home stays home, and an action the mod does not know changes nothing", () => {
  assert.deepEqual(navigate({ screen: "home" }, { type: "back" }), { screen: "home" });
  const plan = { screen: "plan", path: "/p" };
  assert.deepEqual(navigate(plan, { type: "nonsense" }), plan);
});
