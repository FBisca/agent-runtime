// spec: Mod focus section
// The focus report as the workbench reads it: `rastro focus --json` in the shown space's folder. The ranking, the
// citations and the gaps are rastro's; the model only decides how the answer reads on home.
import test from "node:test";
import assert from "node:assert/strict";
import { readFocus, focusView, homeView, initialNav, navigate } from "../model.js";
import { FOCUS, FOCUS_EMPTY, FOCUS_NOW, ok, refusal } from "./fixtures.js";

test("S35 a focus answer is the schema-versioned envelope of the focus operation", () => {
  const read = readFocus(ok(FOCUS));
  assert.equal(read.ok, true);
  assert.equal(read.value.operation, "focus");
  assert.equal(read.value.result.focus.id, "H-8102");
});

test("S35 a refusal keeps rastro's own way out, and a missing rastro names rastro", () => {
  const refused = readFocus(refusal("failed", "no store in this folder", "run rastro init"));
  assert.equal(refused.ok, false);
  assert.equal(refused.error.kind, "failed");
  assert.equal(refused.error.message, "no store in this folder");
  assert.equal(refused.error.wayOut, "run rastro init");
  const absent = readFocus({ thrown: "spawn rastro ENOENT" });
  assert.equal(absent.error.kind, "not_started");
  assert.match(absent.error.wayOut, /rastro/);
});

test("S35 JSON that is not a focus answer is no answer, never a half-read report", () => {
  const read = readFocus(ok({ schema_version: 2, operation: "queue", result: {} }));
  assert.equal(read.ok, false);
  assert.equal(read.error.kind, "no_json");
});

test("S35 the recommended focus reads with its reason, its id and its citation", () => {
  const view = focusView(FOCUS.result, FOCUS_NOW);
  assert.deepEqual(view.hero, {
    title: "Ship rate-limiter config", id: "H-8102", why: "overdue 1d", tone: "late",
    cite: "linear:H-8102 · observed 07:35",
  });
  assert.equal(view.asOf, "as of 07:35");
  assert.equal(view.overdue, "1 overdue");
});

test("S35 each needs-you row reads with its glyph, its sub-line and its due tag", () => {
  const view = focusView(FOCUS.result, FOCUS_NOW);
  assert.equal(view.rows.length, 2);
  assert.deepEqual(view.rows[0], {
    tone: "wait", glyph: "✋", title: "RBAC design doc for Ana", id: null, sub: "you promised", tag: "due Fri",
  });
  assert.deepEqual(view.rows[1], {
    tone: "move", glyph: "▸", title: "RBAC extraction", id: "H-8095", sub: "in progress", tag: "moving",
  });
});

test("S35 the queue reads as a count with no content, and each gap with its typed reason and last good read", () => {
  const view = focusView(FOCUS.result, FOCUS_NOW);
  assert.equal(view.queue, '2 waiting in the queue — say "work the queue"');
  assert.deepEqual(view.gaps, [{ text: "⚠ linear couldn't look (sync failed) · last good 07:35" }]);
  assert.equal(view.notice, null);
});

test("S35 an empty report reads as its notice, not an error and not a zero card", () => {
  const view = focusView(FOCUS_EMPTY.result, FOCUS_NOW);
  assert.equal(view.hero, null);
  assert.deepEqual(view.rows, []);
  assert.equal(view.queue, null);
  assert.deepEqual(view.gaps, []);
  assert.equal(view.overdue, null);
  assert.equal(view.notice, "Nothing needs you.");
  assert.equal(view.asOf, "as of 07:35");
});

test("S35 a due-today item reads due today, and a reason the mod does not know reads as its own text", () => {
  const due = { ...FOCUS.result.focus, reason: "due_today", due: "2026-11-18T17:00:00+00:00" };
  const report = { ...FOCUS.result, focus: due, needs_you: [{ ...due, id: "H-1", reason: "someday" }] };
  const view = focusView(report, FOCUS_NOW);
  assert.equal(view.hero.why, "due today");
  assert.equal(view.hero.tone, "late");
  assert.equal(view.rows[0].tag, "someday");
  assert.equal(view.rows[0].glyph, "•");
  assert.equal(view.rows[0].tone, null);
});

test("S35 home carries the focus model: an answer, a failure's one line, and nothing while loading or rastro is absent", () => {
  const plans = { plans: [] };
  const okState = { status: "ok", value: FOCUS };
  assert.equal(homeView(plans, null, null, FOCUS_NOW, okState).focus.hero.title, "Ship rate-limiter config");
  assert.equal(homeView(plans, null, null, FOCUS_NOW, { status: "loading" }).focus, null);
  assert.equal(homeView(plans, null, null, FOCUS_NOW, null).focus, null);
  const absent = { status: "error", error: { kind: "not_started", message: "spawn rastro ENOENT", wayOut: "install rastro" } };
  assert.equal(homeView(plans, null, null, FOCUS_NOW, absent).focus, null, "a space without rastro shows no section");
  const failed = { status: "error", error: { kind: "failed", message: "no store in this folder", wayOut: "run rastro init" } };
  assert.deepEqual(homeView(plans, null, null, FOCUS_NOW, failed).focus, {
    error: { message: "no store in this folder", wayOut: "run rastro init" },
  });
});

// spec: Mod focus section
test("S37 home opens on the Focus tab, and the selector switches to Plans and back", () => {
  const nav = initialNav();
  assert.equal(nav.screen, "home");
  assert.equal(nav.tab, "focus");
  const plans = navigate(nav, { type: "tab", tab: "plans" });
  assert.deepEqual(plans, { screen: "home", tab: "plans" });
  assert.deepEqual(navigate(plans, { type: "tab", tab: "focus" }), { screen: "home", tab: "focus" });
  assert.equal(navigate(nav, { type: "tab", tab: "someday" }), nav, "a tab the mod does not know changes nothing");
  assert.equal(navigate({ screen: "plan", path: "/p" }, { type: "tab", tab: "plans" }).screen, "plan");
});

test("S37 opening a plan and returning keeps the chosen tab", () => {
  const plans = navigate(initialNav(), { type: "tab", tab: "plans" });
  const open = navigate(plans, { type: "plan", path: "/p" });
  assert.equal(open.screen, "plan");
  assert.deepEqual(navigate(open, { type: "back" }), { screen: "home", tab: "plans" });
});

test("S37 home carries the focus status so the Focus tab can say it is looking", () => {
  const plans = { plans: [] };
  assert.equal(homeView(plans, null, null, FOCUS_NOW, { status: "loading" }).focusStatus, "loading");
  assert.equal(homeView(plans, null, null, FOCUS_NOW, { status: "ok", value: FOCUS }).focusStatus, "ok");
});
