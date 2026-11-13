import test from "node:test";
import assert from "node:assert/strict";
import { createData, watchLoop, COMMAND, FALLBACK_MS, TIMEOUT_MS } from "../data.js";
import { PLANS, PLAN, TOTALS, WORKBENCH_PATH, SECOND_PATH, BROKEN_PATH, ok, refusal } from "./fixtures.js";

const tick = () => new Promise((resolve) => setImmediate(resolve));

// An exec whose answers the test settles by hand, recording how many run at once.
function harness() {
  const calls = [];
  const state = { active: 0, max: 0 };
  const exec = (command, args, options) => new Promise((resolve, reject) => {
    state.active += 1;
    state.max = Math.max(state.max, state.active);
    calls.push({
      command, args, options,
      resolve: (v) => { state.active -= 1; resolve(v); },
      reject: (e) => { state.active -= 1; reject(e); },
    });
  });
  return { calls, state, exec };
}

const SCOPE = { folder: "/work/agent-runtime", machine: null };
const open = (path) => ({ screen: "plan", path });
const HOME = { screen: "home" };

test("S15 a refresh runs the workbench command with a fixed argv in the space folder, no shell", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope({ folder: "/work/agent-runtime", machine: "box" });
  data.show(open(WORKBENCH_PATH));
  const done = data.refresh();
  await tick();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].command, COMMAND);
  assert.deepEqual(h.calls[0].args, ["workbench", "plans", "--json"]);
  assert.deepEqual(h.calls[0].options, { cwd: "/work/agent-runtime", on: "box", timeout: TIMEOUT_MS });
  h.calls[0].resolve(ok(PLANS));
  await tick();
  assert.deepEqual(h.calls[1].args, ["workbench", "plan", WORKBENCH_PATH, "--json"]);
  h.calls[1].resolve(ok(PLAN));
  await done;
  assert.equal(data.state().plan.value.slug, "agent-workbench");
});

test("S15 a path with spaces or shell syntax is one argv element, never parsed by a shell", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  const awkward = "/work/my plans/$(touch pwned); rm -rf x.plan.yaml";
  data.setScope(SCOPE);
  data.show(open(awkward));
  const done = data.refresh();
  await tick();
  h.calls[0].resolve(ok({ ...PLANS, plans: [{ ...PLANS.plans[1], path: awkward }] }));
  await tick();
  assert.deepEqual(h.calls[1].args, ["workbench", "plan", awkward, "--json"]);
  h.calls[1].resolve(ok(PLAN));
  await done;
  for (const call of h.calls) {
    assert.equal(call.command, "agent-orchestrator");
    assert.ok(!call.args.includes("-c"));
  }
});

// Answers every call as it appears until the run ends.
async function settleAll(h, run) {
  let finished = false;
  run.then(() => { finished = true; });
  while (!finished) {
    await tick();
    for (const call of h.calls) {
      if (call.settled) continue;
      call.settled = true;
      call.resolve(ok(call.args[1] === "plans" ? PLANS : call.args[1] === "totals" ? TOTALS : PLAN));
    }
  }
}

test("S15 only the read-only workbench verbs ever run, so a refresh writes nothing", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope(SCOPE);
  await settleAll(h, data.refresh());
  data.show(open(SECOND_PATH));
  await settleAll(h, data.refresh());
  assert.ok(h.calls.length >= 4);
  for (const call of h.calls) {
    assert.equal(call.args[0], "workbench");
    assert.ok(["plans", "plan", "totals"].includes(call.args[1]));
  }
});

test("S15 refreshes asked for while a fetch is in flight share one follow-up: never more than one fetch in flight", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope(SCOPE);
  const first = data.refresh();
  data.refresh();
  data.refresh();
  data.refresh();
  await tick();
  assert.equal(h.calls.length, 1, "three more requests start nothing");
  h.calls[0].resolve(ok({ ...PLANS, plans: [] }));
  await tick();
  assert.equal(h.calls.length, 2, "one follow-up for the requests that arrived meanwhile");
  assert.equal(h.calls[1].args[1], "plans");
  h.calls[1].resolve(ok({ ...PLANS, plans: [] }));
  await first;
  assert.equal(h.calls.length, 2);
  assert.equal(h.state.max, 1);
});

test("S15 an answer older than the latest request is dropped: a space change during a fetch", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope({ folder: "/a", machine: null });
  const run = data.refresh();
  await tick();
  data.setScope({ folder: "/b", machine: null });
  data.refresh();
  h.calls[0].resolve(ok(PLANS));
  await tick();
  assert.equal(data.state().plans.status, "loading", "the answer for /a is not shown for /b");
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].options.cwd, "/b");
  h.calls[1].resolve(ok({ ...PLANS, plans: [PLANS.plans[2]] }));
  await tick();
  assert.deepEqual(h.calls[2].args, ["workbench", "totals", "--json"]);
  h.calls[2].resolve(ok(TOTALS));
  await run;
  assert.deepEqual(data.state().plans.value.plans.map((p) => p.slug), ["second"]);
});

test("S15 an answer older than the latest request is dropped: another plan chosen during a fetch", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope(SCOPE);
  data.show(open(WORKBENCH_PATH));
  const run = data.refresh();
  await tick();
  h.calls[0].resolve(ok(PLANS));
  await tick();
  data.show(open(SECOND_PATH));
  data.refresh();
  h.calls[1].resolve(ok(PLAN));
  await tick();
  assert.equal(data.state().selected, SECOND_PATH);
  assert.equal(data.state().plan.status, "loading", "the slow answer for the first plan is not shown for the second");
  const next = h.calls.at(-1);
  assert.equal(next.args[1], "plans");
  next.resolve(ok(PLANS));
  await tick();
  const second = h.calls.at(-1);
  assert.deepEqual(second.args, ["workbench", "plan", SECOND_PATH, "--json"]);
  second.resolve(ok({ ...PLAN, slug: "second", path: SECOND_PATH }));
  await run;
  assert.equal(data.state().plan.value.slug, "second");
});

test("S15 changes seen by the watcher are flushed as one fetch, and nothing runs when nothing changed", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope(SCOPE);
  data.touch(); data.touch(); data.touch();
  assert.equal(h.calls.length, 0, "a change only marks the view dirty");
  const run = data.flush();
  await tick();
  assert.equal(h.calls.length, 1);
  h.calls[0].resolve(ok({ ...PLANS, plans: [] }));
  await run;
  const idle = data.flush();
  await tick();
  assert.equal(h.calls.length, 1, "nothing changed: no fetch");
  await idle;
});

test("S15 the watch loop reports every change of the folder until the watch ends, and the fallback is 30 seconds", async () => {
  assert.equal(FALLBACK_MS, 30000);
  let changes = 0;
  let watches = 0;
  const watch = async () => {
    watches += 1;
    if (watches > 2) throw new Error("watch ended");
    return ["/state/x"];
  };
  await watchLoop(watch, "/state", () => { changes += 1; });
  assert.equal(changes, 2);
});

test("S13 a failing plan fetch is scoped to that plan: the plan list and the other plans stay usable", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope(SCOPE);
  data.show(open(BROKEN_PATH));
  const run = data.refresh();
  await tick();
  h.calls[0].resolve(ok(PLANS));
  await tick();
  h.calls[1].resolve(refusal("invalid_plan", "cycle: T1 -> T2", "fix the plan file"));
  await run;
  assert.equal(data.state().plans.status, "ok");
  assert.equal(data.state().plan.status, "error");
  assert.equal(data.state().plan.error.kind, "invalid_plan");
  assert.equal(data.state().plan.value, undefined);
  data.show(open(WORKBENCH_PATH));
  const next = data.refresh();
  await tick();
  h.calls.at(-1).resolve(ok(PLANS));
  await tick();
  h.calls.at(-1).resolve(ok(PLAN));
  await next;
  assert.equal(data.state().plan.status, "ok");
  data.show(open(BROKEN_PATH));
  assert.equal(data.state().plan.error.kind, "invalid_plan", "its error is still the broken plan's own");
});

test("S13 a command that fails to start or times out is a project error with no plans and no placeholder", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope(SCOPE);
  const run = data.refresh();
  await tick();
  h.calls[0].reject(new Error("exec timed out after 20000 ms"));
  await run;
  const shown = data.state();
  assert.equal(shown.plans.status, "error");
  assert.equal(shown.plans.error.kind, "timeout");
  assert.equal(shown.plans.value, undefined);
  assert.equal(shown.plan, null);
  assert.equal(h.calls.length, 1, "no plan is fetched without a list");
});

test("S15 the plans whose answers are held are handed over to the view", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope(SCOPE);
  data.show(open(WORKBENCH_PATH));
  const done = data.refresh();
  await tick();
  assert.deepEqual(data.state().loaded, [], "nothing is loaded before an answer");
  h.calls[0].resolve(ok(PLANS));
  await tick();
  h.calls[1].resolve(ok(PLAN));
  await done;
  assert.deepEqual(data.state().loaded.map((p) => p.slug), ["agent-workbench"]);
  data.setScope({ folder: "/elsewhere", machine: null });
  assert.deepEqual(data.state().loaded, [], "another folder holds none of them");
});

test("S8 home fetches the plans and the week's totals, and no plan: the cards come from one fixed totals call", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope({ folder: "/work/agent-runtime", machine: "box" });
  assert.equal(data.state().totals.status, "loading");
  const done = data.refresh();
  await tick();
  assert.deepEqual(h.calls[0].args, ["workbench", "plans", "--json"]);
  h.calls[0].resolve(ok(PLANS));
  await tick();
  assert.equal(h.calls.length, 2);
  assert.deepEqual(h.calls[1].args, ["workbench", "totals", "--json"]);
  assert.deepEqual(h.calls[1].options, { cwd: "/work/agent-runtime", on: "box", timeout: TIMEOUT_MS });
  h.calls[1].resolve(ok(TOTALS));
  await done;
  assert.equal(data.state().totals.value.cards.merged, 3);
  assert.equal(data.state().plan, null, "no plan is open on home");
  assert.equal(h.calls.length, 2);
});

test("S8 an open plan fetches the plan and not the totals, and a card's dashboard fetches the totals again", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope(SCOPE);
  data.show(open(WORKBENCH_PATH));
  await settleAll(h, data.refresh());
  assert.deepEqual(h.calls.map((c) => c.args[1]), ["plans", "plan"]);
  data.show({ screen: "dashboard", card: "merged" });
  await settleAll(h, data.refresh());
  assert.deepEqual(h.calls.map((c) => c.args[1]), ["plans", "plan", "plans", "totals"]);
  assert.equal(data.state().plan, null, "back from the plan: it is not the shown one");
  data.show(HOME);
  assert.equal(data.state().selected, null);
});

test("S7 a failing totals fetch shows no cards and leaves the plans list and the plan usable", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope(SCOPE);
  const run = data.refresh();
  await tick();
  h.calls[0].resolve(ok(PLANS));
  await tick();
  h.calls[1].resolve(refusal("failed", "totals is not a verb", "update agent-orchestrator"));
  await run;
  assert.equal(data.state().plans.status, "ok");
  assert.equal(data.state().totals.status, "error");
  assert.equal(data.state().totals.value, undefined);
});

test("S7 a totals answer that arrives after the person left home is dropped", async () => {
  const h = harness();
  const data = createData({ exec: h.exec });
  data.setScope(SCOPE);
  const run = data.refresh();
  await tick();
  h.calls[0].resolve(ok(PLANS));
  await tick();
  data.show(open(WORKBENCH_PATH));
  data.refresh();
  h.calls[1].resolve(ok(TOTALS));
  await tick();
  assert.equal(data.state().totals.status, "loading", "the slow totals of home are not shown");
  await settleAll(h, run);
});
