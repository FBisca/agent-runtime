import test from "node:test";
import assert from "node:assert/strict";

// The mod's pure model, imported lazily so the file loads before the mod has the functions. Answers are shaped as
// `workbench plans|plan|totals --json` print them; terminals as Herdr's terminals() lists them.
const model = () => import("../../model.js");

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
  // Herdr's own name for a terminal need not be the ledger's: the row shows the ledger's.
  { address: "t9a", state: "live", name: "Claude Code", status: "working", program: { agent: "claude" } },
  { address: "t9b", state: "live", name: "Claude Code", status: "done", program: { agent: "claude" } },
  { address: "t77", state: "live", name: "alpha-t1", status: "working", program: { agent: "claude" } },
];

const CARD_IDS = ["merged", "first_pass", "escalations", "tokens", "ticket_time"];

test("baseline the model loads with the plan list it has today", async () => {
  const { planSections } = await model();
  assert.equal(typeof planSections, "function");
});

test("S7 home shows the cards, Needs me one row per plan, the active plans with their workers, the PRs to merge and the older line", async () => {
  const { homeView, liveTerminals, duration } = await model();
  const home = homeView(PLANS, TOTALS, liveTerminals(TERMINALS, null));
  assert.deepEqual(home.cards.map((c) => c.id), CARD_IDS);
  const value = (id) => home.cards.find((c) => c.id === id).value;
  assert.deepEqual(
    [value("merged"), value("first_pass"), value("escalations"), value("ticket_time")],
    ["3", "50%", "2", duration(2400)],
  );
  assert.match(home.cards.find((c) => c.id === "tokens").note, /1 of 3/);
  assert.deepEqual(home.needs.map((n) => [n.plan, n.items.map((i) => [i.ticket, i.kind])]), [
    ["beta", [[null, "driver_gone"]]],
    ["alpha", [["T5", "lead_pending"], ["T2", "lead_pending"]]],
  ]);
  assert.deepEqual(home.active.map((p) => p.slug), ["alpha", "beta"]);
  const workers = Object.fromEntries(home.active[0].workers.map((w) => [w.name, w.state]));
  assert.deepEqual(workers, { "alpha-t2": "live", "alpha-t2-review": "live", "alpha-t1": "not live" });
  assert.deepEqual(home.awaitingMerge, [{ plan: "alpha", ticket: "T3", pr: PR }]);
  assert.equal(home.older.count, 1);
});

test("S7 a card the window did not measure says unknown, and no totals answer means no cards", async () => {
  const { homeView, liveTerminals } = await model();
  const unmeasured = {
    ...TOTALS,
    cards: {
      ...TOTALS.cards, first_pass_rate: null, median_ticket_seconds: null,
      tokens: { input: null, output: null, measured: 0, tickets: 3 },
    },
  };
  const cards = homeView(PLANS, unmeasured, liveTerminals(TERMINALS, null)).cards;
  assert.equal(cards.find((c) => c.id === "tokens").value, "unknown");
  assert.equal(cards.find((c) => c.id === "first_pass").value, "unknown");
  assert.equal(cards.find((c) => c.id === "ticket_time").value, "unknown");
  assert.equal(homeView(PLANS, null, liveTerminals(TERMINALS, null)).cards, null);
});

test("S8 the mod opens on home, a plan or a card opens over it, and back returns home", async () => {
  const { initialNav, navigate } = await model();
  const home = initialNav();
  assert.deepEqual(home, { screen: "home" });
  const plan = navigate(home, { type: "plan", path: "/r/docs/plans/alpha/plan.yaml" });
  assert.deepEqual(plan, { screen: "plan", path: "/r/docs/plans/alpha/plan.yaml" });
  assert.deepEqual(navigate(plan, { type: "back" }), { screen: "home" });
  const card = navigate(home, { type: "card", id: "ticket_time" });
  assert.deepEqual(card, { screen: "dashboard", card: "ticket_time" });
  assert.deepEqual(navigate(card, { type: "back" }), { screen: "home" });
});

test("S9 the plan header is the plan's name with its whole goal and a way back, without the repository", async () => {
  const { planHeader } = await model();
  const goal = "A goal long enough that a single truncated line at twelve pixels would cut it well before its end.";
  const plan = { slug: "alpha", goal, state: "RUNNING", repo: "/repos/secret-repo", counts: null, metrics: null };
  const wide = planHeader(plan, "wide");
  assert.equal(wide.title, "alpha");
  assert.equal(wide.goal, goal);
  assert.equal(wide.goalLines, null);
  assert.equal(wide.back, true);
  assert.equal(JSON.stringify(wide).includes("secret-repo"), false);
  assert.equal(planHeader(plan, "narrow").goalLines, 3);
});

test("S10 a ticket names its worker and reviewer, live only at their exact address, with Open when live", async () => {
  const { ticketWorkers, liveTerminals } = await model();
  const live = liveTerminals(TERMINALS, null);
  const ticket = {
    id: "T2",
    agents: [
      { name: "alpha-t2", role: "worker", address: "t9a" },
      { name: "alpha-t2-review", role: "reviewer", address: "t9b" },
    ],
  };
  const [worker, reviewer] = ticketWorkers(ticket, live);
  assert.deepEqual(
    { name: worker.name, role: worker.role, state: worker.state, status: worker.status },
    { name: "alpha-t2", role: "worker", state: "live", status: "working" },
  );
  assert.deepEqual(worker.open, { command: "agent.focus", args: { terminal: "t9a" } });
  assert.deepEqual(
    { name: reviewer.name, role: reviewer.role, state: reviewer.state, status: reviewer.status },
    { name: "alpha-t2-review", role: "reviewer", state: "live", status: "done" },
  );
  assert.deepEqual(reviewer.open, { command: "agent.focus", args: { terminal: "t9b" } });
  const byName = ticketWorkers({ id: "T1", agents: [{ name: "alpha-t1", role: "worker", address: "t9c" }] }, live);
  assert.equal(byName[0].state, "not live");
  assert.equal(byName[0].open, null);
  const unbound = ticketWorkers({ id: "T4", agents: [{ name: "alpha-t4", role: "worker", address: null }] }, live);
  assert.equal(unbound[0].state, "unknown");
});

test("S11 the Runtime section is gone from the mod", async () => {
  const m = await model();
  assert.equal(typeof m.runtimeAgents, "undefined");
  assert.equal(typeof m.runtimeNote, "undefined");
});
