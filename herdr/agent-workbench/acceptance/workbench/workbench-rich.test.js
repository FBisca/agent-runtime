import test from "node:test";
import assert from "node:assert/strict";

// The mod's pure model, imported lazily so the file loads before the mod has the functions. Answers are shaped as
// `workbench plans|plan|totals --json` print them; the rich totals fields are this plan's contract (brief D1–D6).
const model = () => import("../../model.js");

const GH = (n) => `https://github.com/acme/repo/pull/${n}`;
const END = "2026-11-13T12:00:00+00:00";
const DAYS = ["2026-11-07", "2026-11-08", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"];
const series = (field, values) => DAYS.map((day, i) => ({ day, [field]: values[i] }));

const TOTALS = {
  schema_version: 1,
  window: {
    days: 7, start: "2026-11-06T12:00:00+00:00", end: END,
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

// An answer as the CLI prints it before this plan: the cards and the old detail, none of the new sections.
const OLD_TOTALS = {
  schema_version: 1, window: { days: 7, start: TOTALS.window.start, end: END }, cards: TOTALS.cards,
  merged: { by_day: [{ day: "2026-11-12", count: 3 }, { day: "2026-11-13", count: 1 }], tickets: TOTALS.merged.tickets },
  first_pass: { by_plan: TOTALS.first_pass.by_plan, fixed: [{ plan: "alpha", ticket: "T2", fix_rounds: 1, findings: 3 }] },
  escalations: {
    by_cause: TOTALS.escalations.by_cause, by_plan: TOTALS.escalations.by_plan,
    latest: TOTALS.escalations.latest.map(({ unit, ...rest }) => rest),
  },
  tokens: { by_plan: TOTALS.tokens.by_plan, by_model: TOTALS.tokens.by_model, top: TOTALS.tokens.top },
  time: {
    steps: Object.fromEntries(Object.entries(TOTALS.time.steps).map(([k, { median, max }]) => [k, { median, max }])),
    slowest: TOTALS.time.slowest,
  },
};

const NOW = Date.parse(END);

const PLANS = {
  schema_version: 1,
  repo: "/r",
  plans: [
    {
      slug: "alpha", format: "yaml", path: "/r/docs/plans/alpha/plan.yaml", legacy: false, state: "RUNNING",
      tickets: { total: 3, by_state: { COMPLETE: 1, "IN PROGRESS": 2 } }, group: "active",
      last_activity: "2026-11-13T11:57:00+00:00",
      agents: [
        { name: "alpha-t2", role: "worker", ticket: "T2", address: "t9a" },
        { name: "alpha-t1", role: "worker", ticket: "T1", address: "t9c" },
        { name: "alpha-t1-review", role: "reviewer", ticket: "T1", address: null },
      ],
      awaiting_merge: [{ ticket: "T3", pr: GH(3) }, { ticket: "T4", pr: "https://example.invalid/pull/4" }],
    },
  ],
  needs_me: [
    { plan: "alpha", ticket: "T2", kind: "lead_pending" },
    { plan: "alpha", ticket: "T1", kind: "needs_you" },
    { plan: "alpha", ticket: null, kind: "fix_plan" },
    { plan: "alpha", ticket: null, kind: "ready_to_drive" },
    { plan: "alpha", ticket: null, kind: "something_new" },
  ],
};

const TERMINALS = [{ address: "t9a", state: "live", name: "Claude Code", status: "working", program: { agent: "claude" } }];

const PLAN = {
  schema_version: 1, slug: "alpha", state: "RUNNING", goal: "g", path: "/r/docs/plans/alpha/plan.yaml",
  counts: { total: 3, by_state: { COMPLETE: 1, "IN PROGRESS": 1, "TO-DO": 1 } },
  tickets: [
    { id: "T1", state: "COMPLETE", layer: 0, blocked_by: [], delivers: "one", pr: GH(1), size: "small", difficulty: "simple",
      agents: [{ name: "alpha-t1", role: "worker", address: "t9c" }, { name: "alpha-t1-review", role: "reviewer", address: null }] },
    { id: "T2", state: "IN PROGRESS", layer: 1, blocked_by: ["T1"], delivers: "two", pr: null, size: "regular", difficulty: "complex",
      agents: [{ name: "alpha-t2", role: "worker", address: "t9a" }] },
    { id: "T3", state: "TO-DO", layer: 1, blocked_by: ["T1"], delivers: "three", pr: null, agents: [] },
  ],
  metrics: {
    wall_seconds: 7200, first_pass_rate: 0.5,
    span: { start: "2026-11-13T08:00:00+00:00", end: "2026-11-13T12:00:00+00:00" },
    errors: { by_gate: {}, last: null },
    tickets: {
      T1: { elapsed_seconds: 3600, merged_at: "2026-11-13T10:00:00+00:00", phases: { work: 1200, review: 600, ci: 60, merge: 1200 },
        fix_rounds: 1, review_rounds: 2, findings: 4, ci_failures: 0, escalations: { gate: 2, idle: 1 }, model: "sonnet",
        tokens: { input: 10, output: 52000 } },
      T2: { elapsed_seconds: 1200, merged_at: null, phases: { work: 1200, review: null, ci: null, merge: null },
        fix_rounds: 0, review_rounds: 0, findings: 0, ci_failures: 0, escalations: {}, model: null, tokens: null },
    },
    timeline: {
      T1: [{ phase: "work", start: "2026-11-13T08:00:00+00:00", end: "2026-11-13T08:20:00+00:00" },
        { phase: "merge", start: "2026-11-13T09:40:00+00:00", end: "2026-11-13T10:00:00+00:00" }],
      T2: [{ phase: "work", start: "2026-11-13T10:00:00+00:00", end: null }],
    },
  },
};

const CARD_IDS = ["merged", "first_pass", "escalations", "tokens", "ticket_time"];
const SCALED = new Set(["columns", "stacked-columns", "bars", "stack", "swimlanes", "spread", "coverage"]);
const isRound = (n) => n > 0 && [1, 2, 5, 10].some((f) => Math.abs(n / 10 ** Math.floor(Math.log10(n)) - f) < 1e-9);

test("baseline the model loads with the dashboards it has today", async () => {
  const { dashboardView, homeView } = await model();
  assert.equal(typeof dashboardView, "function");
  assert.equal(typeof homeView, "function");
});

test("S7 every dashboard opens on its headline, the change against last week and the week's sparkline", async () => {
  const { dashboardView } = await model();
  const merged = dashboardView("merged", TOTALS, "wide");
  assert.equal(merged.headline.value, "4");
  assert.equal(merged.headline.change, "▲ 300% vs last week");
  assert.deepEqual(merged.headline.spark, [0, 0, 0, 0, 0, 3, 1]);
  assert.equal(dashboardView("first_pass", TOTALS, "wide").headline.change, "▼ 25% vs last week");
  // No change to show (last week unmeasured, or a CLI before this plan): said so, never 0%.
  assert.equal(dashboardView("tokens", TOTALS, "wide").headline.change, "first week measured");
  assert.equal(dashboardView("merged", OLD_TOTALS, "wide").headline.change, "first week measured");
  assert.deepEqual(dashboardView("first_pass", TOTALS, "wide").headline.spark, [null, null, null, null, null, 0.6667, 1]);
});

test("S7 a dashboard is two columns when wide and one when narrow, and every scaled chart carries its axis", async () => {
  const { dashboardView } = await model();
  for (const id of CARD_IDS) {
    assert.equal(dashboardView(id, TOTALS, "wide").columns, 2, id);
    assert.equal(dashboardView(id, TOTALS, "narrow").columns, 1, id);
    for (const layout of ["wide", "narrow"]) {
      for (const chart of dashboardView(id, TOTALS, layout).charts) {
        if (!SCALED.has(chart.kind)) continue;
        assert.ok(chart.axis, `${id} ${layout} ${chart.kind} has an axis`);
        const { max, ticks } = chart.axis;
        assert.ok(isRound(max), `${id} ${chart.kind} max ${max} is 1, 2 or 5 times a power of ten`);
        assert.deepEqual(ticks, [0, max / 2, max], `${id} ${chart.kind}`);
      }
    }
  }
  // The top covers the data: three merged on the busiest day, a p90 of 3000s.
  assert.ok(dashboardView("merged", TOTALS, "wide").charts.find((c) => c.kind === "stacked-columns").axis.max >= 3);
  assert.ok(dashboardView("ticket_time", TOTALS, "wide").charts.find((c) => c.kind === "spread").axis.max >= 3000);
});

test("S8 each card's dashboard draws its own charts from the answer", async () => {
  const { dashboardView } = await model();
  const kinds = (id, layout = "wide") => dashboardView(id, TOTALS, layout).charts.map((c) => c.kind);
  assert.ok(kinds("merged").includes("stacked-columns"));
  const cols = dashboardView("merged", TOTALS, "wide").charts.find((c) => c.kind === "stacked-columns");
  assert.deepEqual(cols.days.map((d) => d.total), [0, 0, 0, 0, 0, 3, 1]);
  assert.deepEqual(cols.days[5].parts.map((p) => [p.key, p.value]), [["alpha", 2], ["beta", 1]]);
  assert.ok(kinds("first_pass").includes("ring"));
  const histogram = dashboardView("first_pass", TOTALS, "wide").charts.find((c) => c.kind === "bars" && c.id === "rounds");
  assert.deepEqual(histogram.rows.map((r) => r.value), [3, 1, 0, 0]);
  const reasons = dashboardView("first_pass", TOTALS, "wide").charts.find((c) => c.kind === "stack" && c.id === "reasons");
  assert.deepEqual(reasons.parts.map((p) => [p.key, p.value]), [["verify/blocking", 1], ["review/concern", 2]]);
  assert.ok(kinds("escalations").includes("heatmap"));
  const heat = dashboardView("escalations", TOTALS, "wide").charts.find((c) => c.kind === "heatmap");
  assert.equal(heat.cells.find((c) => c.plan === "alpha" && c.cause === "ci").value, 1);
  assert.ok(kinds("tokens").includes("coverage"));
  const coverage = dashboardView("tokens", TOTALS, "wide").charts.find((c) => c.kind === "coverage");
  assert.deepEqual(coverage.rows.find((r) => r.label === "beta"), { label: "beta", measured: 0, unmeasured: 1, text: "0/1 · unknown" });
  const lanes = dashboardView("ticket_time", TOTALS, "wide").charts.find((c) => c.kind === "swimlanes");
  assert.deepEqual(lanes.lanes.map((l) => l.label), ["alpha T2", "beta T1", "gamma T1", "alpha T1"]);
  // An unmeasured phase is no segment, not a zero one.
  assert.deepEqual(lanes.lanes[1].segments.map((s) => s.step), ["work", "review", "ci"]);
  const spread = dashboardView("ticket_time", TOTALS, "wide").charts.find((c) => c.kind === "spread");
  assert.deepEqual(spread.rows.find((r) => r.step === "merge"), { step: "merge", median: 900, p90: 3000 });
});

test("S8 a narrow panel trades the heatmap for bars, and an answer without the new sections draws no new chart", async () => {
  const { dashboardView } = await model();
  const narrow = dashboardView("escalations", TOTALS, "narrow").charts.map((c) => c.kind);
  assert.ok(!narrow.includes("heatmap"));
  assert.ok(narrow.includes("bars"));
  const old = dashboardView("escalations", OLD_TOTALS, "wide").charts.map((c) => c.kind);
  assert.ok(!old.includes("heatmap"), "no by_plan_cause, no heatmap");
  assert.ok(!dashboardView("ticket_time", OLD_TOTALS, "wide").charts.some((c) => c.kind === "swimlanes"));
  for (const id of CARD_IDS) {
    assert.ok(Array.isArray(dashboardView(id, OLD_TOTALS, "wide").rows), `${id} still lists its rows`);
  }
});

test("S9 a day, a reason or a heatmap cell filters the list, and going back clears it", async () => {
  const { dashboardView, navigate, initialNav } = await model();
  const day = { kind: "day", day: "2026-11-12" };
  const merged = dashboardView("merged", TOTALS, "wide", day);
  assert.deepEqual(merged.rows.map((r) => r.label), ["alpha T2", "alpha T1", "beta T1"]);
  const quiet = dashboardView("merged", TOTALS, "wide", { kind: "day", day: "2026-11-09" });
  assert.deepEqual(quiet.rows, []);
  assert.equal(typeof quiet.empty, "string");
  assert.ok(quiet.empty.length > 0);
  const reason = dashboardView("first_pass", TOTALS, "wide", { kind: "reason", key: "verify/blocking" });
  assert.deepEqual(reason.rows.map((r) => r.label), ["alpha T2"]);
  const cell = dashboardView("escalations", TOTALS, "wide", { kind: "cell", plan: "alpha", cause: "ci" });
  assert.deepEqual(cell.rows.map((r) => r.label), ["alpha archive"]);

  let nav = navigate(initialNav(), { type: "card", id: "merged" });
  nav = navigate(nav, { type: "filter", filter: day });
  assert.deepEqual(nav.filter, day);
  assert.equal(navigate(nav, { type: "filter", filter: { ...day } }).filter, null, "the same filter again clears it");
  const other = { kind: "day", day: "2026-11-13" };
  assert.deepEqual(navigate(nav, { type: "filter", filter: other }).filter, other, "another filter replaces it");
  assert.deepEqual(navigate(nav, { type: "back" }), initialNav());
});

test("S10 rows name a plan-level unit, say when relative to the window and link only GitHub PRs", async () => {
  const { dashboardView, prLink } = await model();
  const rows = dashboardView("escalations", TOTALS, "wide").rows;
  assert.deepEqual(rows.map((r) => r.label), ["alpha archive", "beta T1", "alpha T2"]);
  assert.ok(rows.every((r) => !/null|undefined|\d{4}-\d\d-\d\dT/.test(r.label + r.text)), JSON.stringify(rows));
  assert.equal(rows[0].text, "ci · 5m ago");
  assert.equal(rows[1].text, "blocked · 3h ago");
  const merged = dashboardView("merged", TOTALS, "wide").rows;
  const alpha = merged.find((r) => r.label === "alpha T2");
  assert.deepEqual([alpha.linkText, alpha.link], ["#2", GH(2)]);
  const gamma = merged.find((r) => r.label === "gamma T1");
  assert.deepEqual([gamma.linkText, gamma.link], ["#9", null], "not a GitHub PR: shown, never opened");
  assert.equal(prLink(GH(7)), GH(7));
  for (const bad of [
    "http://github.com/a/b/pull/1", "https://github.com.evil/a/b/pull/1", "https://github.com/a/b/issues/1",
    "https://github.com/a/b/pull/1?x=1", "https://github.com/../b/pull/1", "https://user@github.com/a/b/pull/1",
    "https://github.com/a/b/pull/1/files", "javascript:alert(1)", "file:///etc", null,
  ]) {
    assert.equal(prLink(bad), null, String(bad));
  }
});

test("S12 home's cards each carry a picture of their dashboard", async () => {
  const { homeView, liveTerminals } = await model();
  const home = homeView(PLANS, TOTALS, liveTerminals(TERMINALS, null), NOW);
  const pic = Object.fromEntries(home.cards.map((c) => [c.id, c.picture]));
  assert.deepEqual(home.cards.map((c) => c.id), CARD_IDS);
  assert.deepEqual(pic.merged, { kind: "columns", values: [0, 0, 0, 0, 0, 3, 1] });
  assert.deepEqual(pic.first_pass, { kind: "ring", value: 0.75 });
  assert.equal(pic.escalations.kind, "stack");
  assert.deepEqual(new Set(pic.escalations.parts.map((p) => p.key)), new Set(["gate", "blocked", "ci"]));
  assert.deepEqual(pic.tokens, { kind: "coverage", measured: 1, unmeasured: 3 });
  assert.deepEqual(pic.ticket_time.parts.map((p) => [p.key, p.value]), [["work", 600], ["review", 120], ["ci", 60], ["merge", 900]]);
  // A CLI before this plan still has the step medians; a card with no data has no picture.
  const old = homeView(PLANS, OLD_TOTALS, null, NOW);
  assert.equal(old.cards.find((c) => c.id === "ticket_time").picture.kind, "stack");
  const calm = { ...TOTALS, escalations: { ...TOTALS.escalations, by_cause: {} } };
  assert.equal(homeView(PLANS, calm, null, NOW).cards.find((c) => c.id === "escalations").picture, null);
});

test("S14 home's plans carry how recently they moved, live agents, idle agents folded and their PRs to merge", async () => {
  const { homeView, liveTerminals } = await model();
  const home = homeView(PLANS, TOTALS, liveTerminals(TERMINALS, null), NOW);
  const alpha = home.active.find((p) => p.slug === "alpha");
  assert.equal(alpha.recency, "active 3m ago");
  assert.deepEqual(alpha.live.map((w) => w.name), ["alpha-t2"]);
  assert.equal(alpha.idle, 2);
  assert.deepEqual(alpha.prs, [
    { ticket: "T3", link: GH(3), linkText: "#3" },
    { ticket: "T4", link: null, linkText: "#4" },
  ]);
  const kinds = Object.fromEntries(home.needs.flatMap((n) => n.items).map((i) => [i.kind, [i.glyph, i.action]]));
  assert.deepEqual(kinds.lead_pending, ["⚑", "Answer"]);
  assert.deepEqual(kinds.needs_you, ["?", "Answer"]);
  assert.deepEqual(kinds.fix_plan, ["⚠", "Fix plan"]);
  assert.deepEqual(kinds.ready_to_drive, ["▶", "Drive"]);
  assert.deepEqual(kinds.something_new, ["•", "Open"]);
});

test("S16 an open plan has five cards from its own metrics", async () => {
  const { planCards } = await model();
  const cards = planCards(PLAN);
  assert.deepEqual(cards.map((c) => c.id), ["progress", "first_pass", "wall", "escalations", "tokens"]);
  const by = Object.fromEntries(cards.map((c) => [c.id, c]));
  assert.equal(by.progress.value, "1/3");
  assert.equal(by.first_pass.value, "50%");
  assert.equal(by.wall.value, "2h 00m");
  assert.equal(by.escalations.value, "3");
  assert.equal(by.tokens.value, "52.0k");
  assert.equal(by.tokens.note, "1 of 3 tickets measured");
  const bare = planCards({ ...PLAN, metrics: null });
  assert.deepEqual(bare.filter((c) => c.id !== "progress").map((c) => c.value), ["unknown", "unknown", "unknown", "unknown"]);
});

test("S17 each ticket is a card of its measures, phases, agents and PR, and the timeline marks each merge", async () => {
  const { ticketCard, planTimeline, liveTerminals } = await model();
  const live = liveTerminals(TERMINALS, null);
  const t1 = ticketCard(PLAN.tickets[0], PLAN.tickets, PLAN.metrics, live);
  assert.deepEqual(t1.measures, ["1 round", "4 findings", "1h 00m", "52.0k tok", "sonnet"]);
  assert.deepEqual(t1.phases.map((p) => p.step), ["work", "review", "ci", "merge"]);
  assert.equal(t1.idle, 2);
  assert.deepEqual([t1.linkText, t1.link], ["#1", GH(1)]);
  const t2 = ticketCard(PLAN.tickets[1], PLAN.tickets, PLAN.metrics, live);
  assert.equal(t2.measures[0], "first pass");
  assert.deepEqual(t2.live.map((w) => w.name), ["alpha-t2"]);
  assert.deepEqual(t2.after.map((c) => c.id), ["T1"]);
  const t3 = ticketCard(PLAN.tickets[2], PLAN.tickets, PLAN.metrics, live);
  assert.deepEqual([t3.measures, t3.phases], [[], null], "no metrics: nothing measured is shown");

  const wide = planTimeline(PLAN.metrics, PLAN.tickets, "wide");
  assert.equal(wide.kind, "timeline");
  const lane = wide.lanes.find((l) => l.id === "T1");
  assert.equal(lane.merged, 0.5);
  // On the plan's clock: T1 worked from the span's start for 20 of its 240 minutes.
  assert.deepEqual([lane.segments[0].step, lane.segments[0].left], ["work", 0]);
  assert.ok(Math.abs(lane.segments[0].width - 20 / 240) < 1e-9);
  assert.ok(Math.abs(wide.lanes.find((l) => l.id === "T2").segments[0].left - 0.5) < 1e-9);
  assert.equal(wide.lanes.find((l) => l.id === "T2").merged, null);
  assert.equal(planTimeline(PLAN.metrics, PLAN.tickets, "narrow").kind, "split");
  assert.equal(planTimeline(null, PLAN.tickets, "wide"), null);
});
