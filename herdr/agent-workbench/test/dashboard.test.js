import test from "node:test";
import assert from "node:assert/strict";
import { dashboardView, cardsPerRow, duration } from "../model.js";

// The detail `workbench totals --json` carries beside its cards.
const TOTALS = {
  schema_version: 1,
  window: { days: 7 },
  cards: { merged: 3 },
  time: {
    steps: {
      work: { median: 600, max: 900 }, review: { median: 120, max: 300 },
      ci: { median: null, max: null }, merge: { median: 60, max: 60 },
    },
    slowest: [{ plan: "alpha", ticket: "T2", pr: "https://x/pull/2", seconds: 3000 }],
  },
  merged: {
    by_day: [{ day: "2026-11-12", count: 1 }, { day: "2026-11-13", count: 2 }],
    tickets: [{ plan: "alpha", ticket: "T2", pr: "https://x/pull/2", merged_at: "2026-11-13T10:00:00+00:00" }],
  },
  first_pass: {
    by_plan: { alpha: 0.5, beta: 1 },
    fixed: [{ plan: "alpha", ticket: "T1", fix_rounds: 2, findings: 5 }],
  },
  escalations: {
    by_cause: { review_loop: 2 }, by_plan: { alpha: 2 },
    latest: [{ plan: "alpha", ticket: "T1", cause: "review_loop", at: "2026-11-13T09:00:00+00:00" }],
  },
  tokens: {
    by_plan: { alpha: { input: 100, output: 40, measured: 1, tickets: 2 } },
    by_model: { opus: { input: 100, output: 40, tickets: 1 } },
    top: [{ plan: "alpha", ticket: "T1", input: 100, output: 40, model: "opus" }],
  },
};

const labels = (rows) => rows.map((r) => r.label);

test("S12 a card's dashboard draws that card's detail from the totals answer", () => {
  const time = dashboardView("ticket_time", TOTALS, "wide");
  assert.deepEqual(labels(time.bars), ["work", "review", "ci", "merge"]);
  assert.equal(time.bars[0].text, `${duration(600)} · max ${duration(900)}`);
  assert.equal(time.bars[2].text, "unknown");
  assert.equal(time.lists[0].title, "Slowest tickets");
  assert.deepEqual(time.lists[0].rows[0], { label: "alpha T2", text: duration(3000) });

  const merged = dashboardView("merged", TOTALS, "wide");
  assert.deepEqual(merged.bars.map((b) => [b.label, b.value]), [["2026-11-12", 1], ["2026-11-13", 2]]);
  assert.equal(merged.lists[0].rows[0].label, "alpha T2");

  const first = dashboardView("first_pass", TOTALS, "wide");
  assert.deepEqual(first.bars.map((b) => [b.label, b.text]), [["alpha", "50%"], ["beta", "100%"]]);
  assert.deepEqual(first.lists[0].rows[0], { label: "alpha T1", text: "2 fix rounds · 5 findings" });

  const esc = dashboardView("escalations", TOTALS, "wide");
  assert.deepEqual(esc.bars.map((b) => [b.label, b.value]), [["review_loop", 2], ["alpha", 2]]);
  assert.equal(esc.lists[0].rows[0].label, "alpha T1");
  assert.match(esc.lists[0].rows[0].text, /review_loop/);

  const tok = dashboardView("tokens", TOTALS, "wide");
  assert.deepEqual(tok.bars.map((b) => b.label), ["alpha", "opus"]);
  assert.deepEqual(tok.lists[0].rows[0], { label: "alpha T1", text: "140 · opus" });
});

test("S12 a narrow panel shows the lists only, bars only when wide", () => {
  for (const id of ["ticket_time", "merged", "first_pass", "escalations", "tokens"]) {
    assert.deepEqual(dashboardView(id, TOTALS, "narrow").bars, [], id);
    assert.ok(dashboardView(id, TOTALS, "wide").bars.length > 0, id);
    assert.ok(dashboardView(id, TOTALS, "narrow").lists.length > 0, id);
  }
});

test("S12 home's cards wrap two per row when narrow", () => {
  assert.equal(cardsPerRow("narrow"), 2);
  assert.equal(cardsPerRow("wide"), 5);
});

test("S12 a totals answer without the card's detail draws nothing, never a guess", () => {
  const bare = dashboardView("merged", { cards: {} }, "wide");
  assert.deepEqual(bare, { bars: [], lists: [] });
});
