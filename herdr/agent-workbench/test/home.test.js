import test from "node:test";
import assert from "node:assert/strict";
import { homeView, initialNav, navigate, liveTerminals, duration, readTotals, olderLabel } from "../model.js";
import { RICH_TOTALS, OLD_TOTALS, HOME_PLANS, HOME_TERMINALS, HOME_NOW } from "./fixtures.js";

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
  assert.deepEqual(home.awaitingMerge, [{ plan: "alpha", ticket: "T3", pr: PR }]);
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

test("S12 home's cards each carry a picture of their dashboard, and a card with no data has none", () => {
  const home = homeView(HOME_PLANS, RICH_TOTALS, liveTerminals(HOME_TERMINALS, null), HOME_NOW);
  const pic = Object.fromEntries(home.cards.map((c) => [c.id, c.picture]));
  assert.deepEqual(pic.merged, { kind: "columns", values: [0, 0, 0, 0, 0, 3, 1] });
  assert.deepEqual(pic.first_pass, { kind: "ring", value: 0.75 });
  assert.equal(pic.escalations.kind, "stack");
  assert.deepEqual(new Set(pic.escalations.parts.map((p) => p.key)), new Set(["gate", "blocked", "ci"]));
  assert.deepEqual(pic.tokens, { kind: "coverage", measured: 1, unmeasured: 3 });
  assert.deepEqual(pic.ticket_time.parts.map((p) => [p.key, p.value]), [["work", 600], ["review", 120], ["ci", 60], ["merge", 900]]);
  // The model carries a slot of the palette, never a colour; a step keeps its own.
  assert.deepEqual(pic.ticket_time.parts.map((p) => p.slot), [0, 1, 2, 3]);
  assert.ok(pic.escalations.parts.every((p) => !("color" in p)));
});

test("S12 home draws no picture for a card with no data: a calm week, an unmeasured rate, no week of days, no tickets", () => {
  const picture = (totals, id) => homeView(HOME_PLANS, totals, null, HOME_NOW).cards.find((c) => c.id === id).picture;
  assert.equal(picture({ ...RICH_TOTALS, escalations: { ...RICH_TOTALS.escalations, by_cause: {} } }, "escalations"), null);
  assert.equal(picture({ ...RICH_TOTALS, cards: { ...RICH_TOTALS.cards, first_pass_rate: null } }, "first_pass"), null);
  assert.equal(picture({ ...RICH_TOTALS, cards: { ...RICH_TOTALS.cards, tokens: { input: null, output: null, measured: 0, tickets: 0 } } }, "tokens"), null);
  assert.equal(picture({ ...RICH_TOTALS, time: { ...RICH_TOTALS.time, steps: {} } }, "ticket_time"), null);
  const quiet = { ...RICH_TOTALS, merged: { ...RICH_TOTALS.merged, by_day: RICH_TOTALS.merged.by_day.map((d) => ({ ...d, count: 0, by_plan: {} })) } };
  assert.equal(picture(quiet, "merged"), null, "a week of zeros draws no columns");
  // A CLI before the series: days with something are not a week, so no columns; the step medians still picture the time.
  const old = homeView(HOME_PLANS, OLD_TOTALS, null, HOME_NOW).cards;
  assert.equal(old.find((c) => c.id === "merged").picture, null);
  assert.equal(old.find((c) => c.id === "ticket_time").picture.kind, "stack");
  assert.equal(homeView(HOME_PLANS, null, null, HOME_NOW).cards, null);
});

test("S12 home folds the causes beyond the palette into one neutral part", () => {
  const by_cause = { a: 5, b: 4, c: 3, d: 2, e: 1 };
  const parts = homeView(HOME_PLANS, { ...RICH_TOTALS, escalations: { ...RICH_TOTALS.escalations, by_cause } }, null, HOME_NOW)
    .cards.find((c) => c.id === "escalations").picture.parts;
  assert.deepEqual(parts.map((p) => [p.key, p.value, p.slot]), [["a", 5, 0], ["b", 4, 1], ["c", 3, 2], ["d", 2, 3], ["other", 1, null]]);
});

test("S14 home's Needs me items carry a glyph and an action for their kind", () => {
  const home = homeView(HOME_PLANS, RICH_TOTALS, null, HOME_NOW);
  const kinds = Object.fromEntries(home.needs.flatMap((n) => n.items).map((i) => [i.kind, [i.glyph, i.action]]));
  assert.deepEqual(kinds, {
    lead_pending: ["⚑", "Answer"], needs_you: ["?", "Answer"], fix_plan: ["⚠", "Fix plan"],
    ready_to_drive: ["▶", "Drive"], something_new: ["•", "Open"],
  });
  const driver = homeView({ ...HOME_PLANS, needs_me: [{ plan: "alpha", ticket: null, kind: "driver_gone" }] }, null, null, HOME_NOW);
  assert.deepEqual([driver.needs[0].items[0].glyph, driver.needs[0].items[0].action], ["•", "Open"]);
});

test("S14 home's plans carry how recently they moved, their live agents, the count of the others and their PRs to merge", () => {
  const home = homeView(HOME_PLANS, RICH_TOTALS, liveTerminals(HOME_TERMINALS, null), HOME_NOW);
  const alpha = home.active[0];
  assert.equal(alpha.recency, "active 3m ago");
  assert.deepEqual(alpha.live.map((w) => w.name), ["alpha-t2"]);
  assert.equal(alpha.idle, 2);
  assert.deepEqual(alpha.prs, [
    { ticket: "T3", link: "https://github.com/acme/repo/pull/3", linkText: "#3" },
    { ticket: "T4", link: null, linkText: "#4" },
  ], "a PR opens only when it is a GitHub pull request");
  // Without Herdr's terminals none is known live: all of them are the others.
  const blind = homeView(HOME_PLANS, null, null, HOME_NOW).active[0];
  assert.deepEqual([blind.live, blind.idle], [[], 3]);
});

test("S14 home's plan with no activity time, no agents and no PR has no recency, none live and nothing to merge", () => {
  const bare = { ...HOME_PLANS, plans: [{ ...HOME_PLANS.plans[0], last_activity: null, agents: [], awaiting_merge: [] }] };
  const plan = homeView(bare, null, null, HOME_NOW).active[0];
  assert.deepEqual([plan.recency, plan.live, plan.idle, plan.prs], [null, [], 0, []]);
  const day = { ...HOME_PLANS, plans: [{ ...HOME_PLANS.plans[0], last_activity: "2026-11-10T09:00:00+00:00" }] };
  assert.equal(homeView(day, null, null, HOME_NOW).active[0].recency, "last active Tue 10 Nov");
});
