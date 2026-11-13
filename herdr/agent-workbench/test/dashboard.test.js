import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  dashboardView, cardsPerRow, plansPerRow, duration, navigate, initialNav, prLink, reasonLook, stepLook, orderedReasons,
  niceTop, relativeTime,
} from "../model.js";
import { RICH_TOTALS as TOTALS, OLD_TOTALS, GH } from "./fixtures.js";

const CARD_IDS = ["merged", "first_pass", "escalations", "tokens", "ticket_time"];
const SCALED = new Set(["columns", "stacked-columns", "bars", "stack", "swimlanes", "spread", "coverage"]);
const isRound = (n) => n > 0 && [1, 2, 5, 10].some((f) => Math.abs(n / 10 ** Math.floor(Math.log10(n)) - f) < 1e-9);
const chart = (id, kind, layout = "wide", totals = TOTALS) => dashboardView(id, totals, layout).charts.find((c) => c.kind === kind);
const labels = (rows) => rows.map((r) => r.label);

test("S7 dashboard opens on the value, the change against last week and the week's sparkline", () => {
  const merged = dashboardView("merged", TOTALS, "wide");
  assert.equal(merged.headline.value, "4");
  assert.equal(merged.headline.change, "▲ 300% vs last week");
  assert.deepEqual(merged.headline.spark, [0, 0, 0, 0, 0, 3, 1]);
  assert.equal(dashboardView("first_pass", TOTALS, "wide").headline.change, "▼ 25% vs last week");
  assert.deepEqual(dashboardView("first_pass", TOTALS, "wide").headline.spark, [null, null, null, null, null, 0.6667, 1]);
  assert.deepEqual(dashboardView("tokens", TOTALS, "wide").headline.spark, [null, null, null, null, null, 40, null]);
  assert.equal(dashboardView("tokens", TOTALS, "wide").headline.note, "1 of 4 tickets measured");
  // Nothing to compare (last week null or 0, or a CLI before it carried the change): said so, never 0%.
  assert.equal(dashboardView("tokens", TOTALS, "wide").headline.change, "first week measured");
  assert.equal(dashboardView("merged", OLD_TOTALS, "wide").headline.change, "first week measured");
  assert.deepEqual(dashboardView("merged", OLD_TOTALS, "wide").headline.spark, []);
  const flat = { ...TOTALS, change: { ...TOTALS.change, merged: 0 } };
  assert.equal(dashboardView("merged", flat, "wide").headline.change, "no change vs last week");
  assert.equal(dashboardView("ticket_time", TOTALS, "wide").headline.change, "▲ 33% vs last week");
});

test("S7 dashboard is two columns wide, one narrow, and every scaled chart carries an axis of 0, half and a round top", () => {
  for (const id of CARD_IDS) {
    assert.equal(dashboardView(id, TOTALS, "wide").columns, 2, id);
    assert.equal(dashboardView(id, TOTALS, "narrow").columns, 1, id);
    for (const layout of ["wide", "narrow"]) {
      const charts = dashboardView(id, TOTALS, layout).charts;
      assert.ok(charts.length > 0, `${id} ${layout} draws something`);
      for (const c of charts.filter((x) => SCALED.has(x.kind))) {
        assert.ok(c.axis, `${id} ${layout} ${c.kind} has an axis`);
        const { max, ticks, labels: words } = c.axis;
        assert.ok(isRound(max), `${id} ${c.kind} max ${max} is 1, 2 or 5 times a power of ten`);
        assert.deepEqual(ticks, [0, max / 2, max], `${id} ${c.kind}`);
        assert.equal(words.length, 3, `${id} ${c.kind} words its ticks`);
      }
      for (const c of charts.filter((x) => !SCALED.has(x.kind))) assert.equal(c.axis, undefined, `${id} ${c.kind}`);
    }
  }
  assert.ok(chart("merged", "stacked-columns").axis.max >= 3);
  assert.ok(chart("ticket_time", "spread").axis.max >= 3000);
  assert.ok(chart("ticket_time", "swimlanes").axis.max >= 3660);
  assert.ok(chart("tokens", "coverage").axis.max >= 2);
});

test("S7 dashboard rounds a top up to 1, 2 or 5 times a power of ten and words the ticks in the axis's unit", () => {
  assert.deepEqual([0, 0.3, 1, 1.5, 3, 5, 6, 7000].map(niceTop), [1, 0.5, 1, 2, 5, 5, 10, 10000]);
  assert.deepEqual(chart("merged", "stacked-columns").axis.labels, ["0", "2.5", "5"]);
  assert.deepEqual(chart("first_pass", "stack").axis.labels, ["0%", "50%", "100%"]);
  assert.deepEqual(chart("ticket_time", "spread").axis.labels, ["0", duration(2500), duration(5000)]);
});

test("S8 dashboard draws merged as day columns split by plan", () => {
  const cols = chart("merged", "stacked-columns");
  assert.deepEqual(cols.days.map((d) => d.total), [0, 0, 0, 0, 0, 3, 1]);
  assert.deepEqual(cols.days[5].parts.map((p) => [p.key, p.value]), [["alpha", 2], ["beta", 1]]);
  assert.equal(cols.days[5].day, "2026-11-12");
  assert.equal(cols.days[5].label, "Thu");
  assert.deepEqual(cols.days[5].filter, { kind: "day", day: "2026-11-12" });
  assert.deepEqual(cols.series.map((s) => s.key), ["alpha", "beta", "gamma"], "the week's biggest plan first, ties by name");
});

test("S8 dashboard folds plans past the fourth into other", () => {
  const plans = ["a", "b", "c", "d", "e", "f"];
  const days = [{ day: "2026-11-13", count: 6, by_plan: Object.fromEntries(plans.map((p, i) => [p, i + 1])) }];
  const cols = chart("merged", "stacked-columns", "wide", { ...TOTALS, merged: { ...TOTALS.merged, by_day: days } });
  assert.deepEqual(cols.series.map((s) => s.key), ["f", "e", "d", "c", "other"]);
  assert.deepEqual(cols.days[0].parts.map((p) => [p.key, p.value]), [["f", 6], ["e", 5], ["d", 4], ["c", 3], ["other", 3]]);
  assert.equal(cols.days[0].total, 6);
});

test("S8 dashboard draws first pass as a ring, the fix-round histogram and the findings by reason in a fixed order", () => {
  assert.equal(chart("first_pass", "ring").value, 0.75);
  const histogram = dashboardView("first_pass", TOTALS, "wide").charts.find((c) => c.kind === "bars" && c.id === "rounds");
  assert.deepEqual(histogram.rows.map((r) => r.value), [3, 1, 0, 0]);
  assert.deepEqual(histogram.rows.map((r) => r.label), ["first pass", "1 round", "2 rounds", "3+ rounds"]);
  const reasons = chart("first_pass", "stack");
  assert.equal(reasons.id, "reasons");
  assert.deepEqual(reasons.parts.map((p) => [p.key, p.value]), [["verify/blocking", 1], ["review/concern", 2]]);
  assert.deepEqual(reasons.parts.map((p) => p.label), ["gate check", "review · concern"]);
  // However the CLI orders them, they draw in the fixed order: gate, review blocking to deferred, CI, conflict.
  const shuffled = { "conflict/blocking": 1, "review/deferred": 1, "ci/blocking": 1, "review/blocking": 1, "verify/blocking": 1, "review/nit": 1, "review/question": 1, "review/concern": 1, "other/odd": 1 };
  assert.deepEqual(orderedReasons(Object.keys(shuffled)), [
    "verify/blocking", "review/blocking", "review/concern", "review/question", "review/nit", "review/deferred",
    "ci/blocking", "conflict/blocking", "other/odd",
  ]);
});

test("S8 dashboard draws escalations as a plan x cause heatmap when wide and bars by cause when narrow", () => {
  const heat = chart("escalations", "heatmap");
  assert.equal(heat.cells.find((c) => c.plan === "alpha" && c.cause === "ci").value, 1);
  assert.equal(heat.cells.find((c) => c.plan === "beta" && c.cause === "ci").value, 0);
  assert.deepEqual(heat.cells.find((c) => c.plan === "alpha" && c.cause === "ci").filter, { kind: "cell", plan: "alpha", cause: "ci" });
  assert.deepEqual(heat.plans, ["alpha", "beta"]);
  assert.deepEqual(heat.causes, ["gate", "blocked", "ci"]);
  const narrow = dashboardView("escalations", TOTALS, "narrow").charts;
  assert.ok(!narrow.some((c) => c.kind === "heatmap"));
  assert.deepEqual(narrow.find((c) => c.kind === "bars").rows.map((r) => [r.key, r.value]), [["gate", 1], ["blocked", 1], ["ci", 1]]);
});

test("S8 dashboard draws tokens as each plan's measured and unmeasured tickets", () => {
  const coverage = chart("tokens", "coverage");
  assert.deepEqual(coverage.rows.find((r) => r.label === "beta"), { label: "beta", measured: 0, unmeasured: 1, text: "0/1 · unknown" });
  assert.deepEqual(coverage.rows.find((r) => r.label === "alpha"), { label: "alpha", measured: 1, unmeasured: 1, text: "1/2 · 140" });
});

test("S8 dashboard draws ticket time as a swimlane per slow ticket, each step's median and p90", () => {
  const lanes = chart("ticket_time", "swimlanes");
  assert.deepEqual(labels(lanes.lanes), ["alpha T2", "beta T1", "gamma T1", "alpha T1"]);
  // An unmeasured phase is no segment, not a zero one.
  assert.deepEqual(lanes.lanes[1].segments.map((s) => s.step), ["work", "review", "ci"]);
  const first = lanes.lanes[0];
  assert.deepEqual(first.segments.map((s) => s.step), ["work", "review", "ci", "merge"]);
  assert.equal(first.segments[0].left, 0);
  assert.ok(Math.abs(first.segments[1].left - first.segments[0].width) < 1e-9, "segments follow one another");
  assert.ok(first.segments.at(-1).left + first.segments.at(-1).width <= 1 + 1e-9, "the axis covers every lane");
  assert.equal(first.text, duration(3660));
  const spread = chart("ticket_time", "spread");
  assert.deepEqual(spread.rows.find((r) => r.step === "merge"), { step: "merge", median: 900, p90: 3000 });
  assert.deepEqual(spread.rows.map((r) => r.step), ["work", "review", "ci", "merge"]);
});

test("S8 dashboard draws no chart for a section the answer lacks, never a zero one", () => {
  const old = (id, layout = "wide") => dashboardView(id, OLD_TOTALS, layout).charts.map((c) => c.kind);
  assert.ok(!old("escalations").includes("heatmap"), "no by_plan_cause, no heatmap");
  assert.ok(old("escalations").includes("bars"), "the causes it does carry still draw");
  assert.ok(!old("ticket_time").includes("swimlanes"));
  assert.ok(!old("merged").includes("stacked-columns"), "no by_plan, no columns");
  assert.ok(!old("first_pass").includes("stack") && !old("first_pass").some((k) => k === "bars"));
  assert.ok(old("first_pass").includes("ring"), "the rate is in the cards");
  assert.ok(old("tokens").includes("coverage"), "coverage reads by_plan, which old answers carry");
  for (const id of CARD_IDS) assert.ok(Array.isArray(dashboardView(id, OLD_TOTALS, "wide").rows), `${id} still lists its rows`);
  const quiet = {
    ...TOTALS,
    merged: { ...TOTALS.merged, by_day: TOTALS.merged.by_day.map((d) => ({ ...d, count: 0, by_plan: {} })), tickets: [] },
    escalations: { ...TOTALS.escalations, by_cause: {}, by_plan_cause: {}, latest: [] },
    first_pass: { ...TOTALS.first_pass, by_reason: {}, rounds: { 0: 0, 1: 0, 2: 0, "3+": 0 } },
    tokens: { ...TOTALS.tokens, by_plan: {} },
    time: { ...TOTALS.time, tickets: [], steps: {} },
  };
  assert.deepEqual(dashboardView("merged", quiet, "wide").charts, []);
  assert.deepEqual(dashboardView("escalations", quiet, "wide").charts, []);
  assert.ok(!dashboardView("first_pass", quiet, "wide").charts.some((c) => c.kind === "stack" || c.kind === "bars"));
  assert.deepEqual(dashboardView("tokens", quiet, "wide").charts, []);
  assert.deepEqual(dashboardView("ticket_time", quiet, "wide").charts, []);
  assert.deepEqual(dashboardView("merged", null, "wide"), { headline: null, columns: 2, charts: [], rows: [], rowsTitle: null, filtered: null, empty: null });
});

test("S9 dashboard keeps only a clicked day's, reason's or cell's rows and says so when none is left", () => {
  const day = { kind: "day", day: "2026-11-12" };
  assert.deepEqual(labels(dashboardView("merged", TOTALS, "wide", day).rows), ["alpha T2", "alpha T1", "beta T1"]);
  assert.equal(dashboardView("merged", TOTALS, "wide", day).empty, null);
  assert.equal(dashboardView("merged", TOTALS, "wide", day).filtered, "Thu 12 Nov");
  assert.equal(dashboardView("merged", TOTALS, "wide").filtered, null);
  assert.equal(dashboardView("first_pass", TOTALS, "wide", { kind: "reason", key: "ci/blocking" }).filtered, "CI failed");
  assert.equal(dashboardView("escalations", TOTALS, "wide", { kind: "cell", plan: "alpha", cause: "ci" }).filtered, "alpha · ci");
  const quiet = dashboardView("merged", TOTALS, "wide", { kind: "day", day: "2026-11-09" });
  assert.deepEqual(quiet.rows, []);
  assert.match(quiet.empty, /2026-11-09|Mon 9 Nov/);
  const reason = dashboardView("first_pass", TOTALS, "wide", { kind: "reason", key: "verify/blocking" });
  assert.deepEqual(labels(reason.rows), ["alpha T2"]);
  assert.deepEqual(dashboardView("first_pass", TOTALS, "wide", { kind: "reason", key: "ci/blocking" }).rows, []);
  const cell = dashboardView("escalations", TOTALS, "wide", { kind: "cell", plan: "alpha", cause: "ci" });
  assert.deepEqual(labels(cell.rows), ["alpha archive"]);
  assert.deepEqual(dashboardView("escalations", TOTALS, "wide", { kind: "cell", plan: "beta", cause: "ci" }).rows, []);
  // A filter of another card's kind is not this card's: nothing is filtered.
  assert.equal(dashboardView("tokens", TOTALS, "wide", day).rows.length, 1);
  assert.equal(dashboardView("merged", TOTALS, "wide", { kind: "cell", plan: "alpha", cause: "ci" }).rows.length, 4);
});

test("S9 dashboard dims what a filter leaves out", () => {
  const day = { kind: "day", day: "2026-11-12" };
  const cols = dashboardView("merged", TOTALS, "wide", day).charts.find((c) => c.kind === "stacked-columns");
  assert.deepEqual(cols.days.map((d) => d.dim), [true, true, true, true, true, false, true]);
  assert.ok(chart("merged", "stacked-columns").days.every((d) => d.dim === false), "no filter, nothing dimmed");
  const reasons = dashboardView("first_pass", TOTALS, "wide", { kind: "reason", key: "verify/blocking" }).charts.find((c) => c.kind === "stack");
  assert.deepEqual(reasons.parts.map((p) => [p.key, p.dim]), [["verify/blocking", false], ["review/concern", true]]);
  assert.deepEqual(reasons.parts[0].filter, { kind: "reason", key: "verify/blocking" });
  const heat = dashboardView("escalations", TOTALS, "wide", { kind: "cell", plan: "alpha", cause: "ci" }).charts.find((c) => c.kind === "heatmap");
  assert.deepEqual(heat.cells.filter((c) => !c.dim).map((c) => [c.plan, c.cause]), [["alpha", "ci"]]);
});

test("S9 dashboard's click filter lives in the navigation: the same click clears it and back clears it", () => {
  const day = { kind: "day", day: "2026-11-12" };
  let nav = navigate(initialNav(), { type: "card", id: "merged" });
  assert.deepEqual(nav, { screen: "dashboard", card: "merged" });
  nav = navigate(nav, { type: "filter", filter: day });
  assert.deepEqual(nav, { screen: "dashboard", card: "merged", filter: day });
  assert.equal(navigate(nav, { type: "filter", filter: { ...day } }).filter, null, "the same filter again clears it");
  const other = { kind: "day", day: "2026-11-13" };
  assert.deepEqual(navigate(nav, { type: "filter", filter: other }).filter, other, "another filter replaces it");
  assert.deepEqual(navigate(nav, { type: "back" }), initialNav());
  assert.deepEqual(navigate(navigate(nav, { type: "back" }), { type: "card", id: "merged" }), { screen: "dashboard", card: "merged" });
  assert.deepEqual(navigate(initialNav(), { type: "filter", filter: day }), initialNav(), "home has nothing to filter");
  const plan = navigate(initialNav(), { type: "plan", path: "/p" });
  assert.deepEqual(navigate(plan, { type: "filter", filter: day }), plan);
});

test("S10 dashboard rows name a plan-level unit, never null, and read times relative to the window's end", () => {
  const rows = dashboardView("escalations", TOTALS, "wide").rows;
  assert.deepEqual(labels(rows), ["alpha archive", "beta T1", "alpha T2"]);
  assert.ok(rows.every((r) => !/null|undefined|\d{4}-\d\d-\d\dT/.test(r.label + r.text)), JSON.stringify(rows));
  assert.equal(rows[0].text, "ci · 5m ago");
  assert.equal(rows[1].text, "blocked · 3h ago");
  assert.equal(rows[2].text, "gate · Wed 11 Nov");
  // An answer from before the unit: the plan alone, never a null.
  const old = dashboardView("escalations", OLD_TOTALS, "wide").rows;
  assert.equal(old[0].label, "alpha");
  assert.equal(old[1].label, "beta T1");
});

test("S10 dashboard reads time as minutes, hours, then the day", () => {
  const end = "2026-11-13T12:00:00+00:00";
  assert.equal(relativeTime("2026-11-13T11:59:50+00:00", end), "1m ago");
  assert.equal(relativeTime("2026-11-13T11:55:00+00:00", end), "5m ago");
  assert.equal(relativeTime("2026-11-13T11:00:01+00:00", end), "59m ago");
  assert.equal(relativeTime("2026-11-13T11:00:00+00:00", end), "1h ago");
  assert.equal(relativeTime("2026-11-12T12:00:01+00:00", end), "23h ago");
  assert.equal(relativeTime("2026-11-12T12:00:00+00:00", end), "Thu 12 Nov");
  assert.equal(relativeTime("2026-11-11T10:00:00+00:00", null), "Wed 11 Nov");
  assert.equal(relativeTime(null, end), null);
  assert.equal(relativeTime("not a time", end), null);
});

test("S10 dashboard shows a PR as #n and opens it only when it is an https GitHub pull request", () => {
  const merged = dashboardView("merged", TOTALS, "wide").rows;
  const alpha = merged.find((r) => r.label === "alpha T2");
  assert.deepEqual([alpha.linkText, alpha.link], ["#2", GH(2)]);
  assert.equal(alpha.text, "Thu 12 Nov");
  const gamma = merged.find((r) => r.label === "gamma T1");
  assert.deepEqual([gamma.linkText, gamma.link], ["#9", null], "not a GitHub PR: shown, never opened");
  assert.equal(gamma.text, "5m ago");
  const beta = merged.find((r) => r.label === "beta T1");
  assert.deepEqual([beta.linkText, beta.link], [null, null]);
  assert.equal(prLink(GH(7)), GH(7));
  for (const bad of [
    "http://github.com/a/b/pull/1", "https://github.com.evil/a/b/pull/1", "https://github.com/a/b/issues/1",
    "https://github.com/a/b/pull/1?x=1", "https://github.com/../b/pull/1", "https://user@github.com/a/b/pull/1",
    "https://github.com/a/b/pull/1/files", "https://github.com/a/b/pull/1#x", "https://github.com/a/b/pull/1\n", "javascript:alert(1)", "file:///etc", null, undefined, 7,
  ]) {
    assert.equal(prLink(bad), null, String(bad));
  }
  assert.deepEqual(dashboardView("ticket_time", TOTALS, "wide").rows[0], {
    label: "alpha T2", text: duration(3660), link: GH(2), linkText: "#2", plan: "alpha", day: null, reasons: null, cause: null,
  });
});

test("S11 dashboard's colours are slots of the fixed data palette, and a reason or step keeps its slot whatever order it arrives in", () => {
  assert.deepEqual(stepLook("work"), { slot: 0, shade: 1 });
  assert.deepEqual(["work", "review", "ci", "merge"].map((s) => stepLook(s).slot), [0, 1, 2, 3]);
  assert.deepEqual(stepLook("odd"), { slot: null, shade: 1 });
  const keys = ["conflict/blocking", "ci/blocking", "review/deferred", "review/blocking", "verify/blocking", "other/odd"];
  const slots = (list) => Object.fromEntries(list.map((k) => [k, reasonLook(k).slot]));
  assert.deepEqual(slots(keys), slots([...keys].reverse()));
  assert.deepEqual(slots(keys), {
    "conflict/blocking": 3, "ci/blocking": 2, "review/deferred": 1, "review/blocking": 1, "verify/blocking": 0, "other/odd": null,
  });
  assert.ok(reasonLook("review/blocking").shade > reasonLook("review/deferred").shade);
  for (const [key, label] of [["verify/blocking", "gate check"], ["review/nit", "review · nit"], ["ci/blocking", "CI failed"],
    ["conflict/blocking", "merge conflict"], ["other/odd", "other/odd"]]) assert.equal(reasonLook(key).label, label);
  // A day's plans take the slots in the order of their week's total, so one plan keeps its colour across the days.
  const cols = chart("merged", "stacked-columns");
  assert.deepEqual(cols.series.map((s) => [s.key, s.slot]), [["alpha", 0], ["beta", 1], ["gamma", 2]]);
  const reasons = chart("first_pass", "stack");
  assert.deepEqual(reasons.parts.map((p) => [p.key, p.slot]), [["verify/blocking", 0], ["review/concern", 1]]);
  const lanes = chart("ticket_time", "swimlanes");
  assert.deepEqual(lanes.lanes[0].segments.map((s) => s.slot), [0, 1, 2, 3]);
});

test("S11 dashboard's data palette is aqua, violet, magenta, green in that order, and no chart is coloured with a state colour", () => {
  const ui = readFileSync(new URL("../ui.js", import.meta.url), "utf8");
  const palette = ["#199e70", "#9085e9", "#d55181", "#008300"];
  const at = palette.map((hex) => ui.indexOf(hex));
  assert.ok(at.every((i) => i >= 0), "ui.js holds the four data colours");
  assert.deepEqual([...at].sort((a, b) => a - b), at, "in that order");
  for (const name of ["charts.js", "dashboard.js"]) {
    const source = readFileSync(new URL(`../views/${name}`, import.meta.url), "utf8");
    assert.ok(!/\bt\.(done|working|blocked)\b/.test(source), `${name} colours no data with a state colour`);
    assert.ok(!/colorOf\(/.test(source), `${name} does not paint a tone`);
    assert.ok(!/#[0-9a-fA-F]{6}/.test(source), `${name} takes its colours from ui.js`);
  }
});

test("S15 dashboard keeps its charts shorter in the one column of a narrow panel, and home's cards two to a row", () => {
  const wide = dashboardView("merged", TOTALS, "wide");
  const narrow = dashboardView("merged", TOTALS, "narrow");
  assert.equal(narrow.columns, 1);
  assert.ok(narrow.chartHeight < wide.chartHeight, `${narrow.chartHeight} < ${wide.chartHeight}`);
  assert.equal(cardsPerRow("narrow"), 2);
  assert.equal(cardsPerRow("wide"), 5);
  assert.equal(plansPerRow("narrow"), 1);
  assert.equal(plansPerRow("wide"), 1);
});
