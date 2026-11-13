import test from "node:test";
import assert from "node:assert/strict";
import * as model from "../model.js";
import { planHeader, ticketWorkers, ticketLayers, liveTerminals } from "../model.js";

// Terminals as Herdr's terminals() lists them. Herdr's own name for a terminal need not be the ledger's.
const TERMINALS = [
  { address: "t9a", state: "live", name: "Claude Code", status: "working", program: { agent: "claude" } },
  { address: "t9b", state: "live", name: "Claude Code", status: "done", program: { agent: "claude" } },
  // a terminal that merely carries the ledger's name for T1, at another address
  { address: "t77", state: "live", name: "alpha-t1", status: "working", program: { agent: "claude" } },
];

const GOAL = "A goal long enough that a single truncated line at twelve pixels would cut it well before its end.";
const plan = { slug: "alpha", goal: GOAL, state: "RUNNING", repo: "/repos/secret-repo", counts: null, metrics: null };

test("S9 the plan header is the plan's name with its whole goal and a way back, without the repository", () => {
  const wide = planHeader(plan, "wide");
  assert.equal(wide.title, "alpha");
  assert.equal(wide.goal, GOAL);
  assert.equal(wide.goalLines, null, "wide wraps the whole goal");
  assert.equal(wide.back, true);
  assert.equal(JSON.stringify(wide).includes("secret-repo"), false);
  const narrow = planHeader(plan, "narrow");
  assert.equal(narrow.goal, GOAL, "narrow keeps the whole goal and the view clamps it");
  assert.equal(narrow.goalLines, 3);
});

test("S10 a ticket names its worker and reviewer by the ledger's name, live only at their exact address, Open when live", () => {
  const live = liveTerminals(TERMINALS, null);
  const ticket = {
    id: "T2",
    agents: [
      { name: "alpha-t2", role: "worker", address: "t9a" },
      { name: "alpha-t2-review", role: "reviewer", address: "t9b" },
    ],
  };
  const [worker, reviewer] = ticketWorkers(ticket, live);
  assert.deepEqual({ name: worker.name, role: worker.role, state: worker.state, status: worker.status },
    { name: "alpha-t2", role: "worker", state: "live", status: "working" });
  assert.deepEqual(worker.open, { command: "agent.focus", args: { terminal: "t9a" } });
  assert.deepEqual({ name: reviewer.name, role: reviewer.role, state: reviewer.state, status: reviewer.status },
    { name: "alpha-t2-review", role: "reviewer", state: "live", status: "done" });
  assert.deepEqual(reviewer.open, { command: "agent.focus", args: { terminal: "t9b" } });
});

test("S10 an agent whose address is gone is not live even when a terminal elsewhere has its name, and one with no address is unknown", () => {
  const live = liveTerminals(TERMINALS, null);
  const [gone] = ticketWorkers({ id: "T1", agents: [{ name: "alpha-t1", role: "worker", address: "t9c" }] }, live);
  assert.equal(gone.state, "not live");
  assert.equal(gone.word, "not live");
  assert.equal(gone.status, null);
  assert.equal(gone.open, null);
  const [unbound] = ticketWorkers({ id: "T4", agents: [{ name: "alpha-t4", role: "worker", address: null }] }, live);
  assert.equal(unbound.state, "unknown");
  assert.equal(unbound.open, null);
});

test("S10 a ticket with no agents, or an answer that carries none, has no worker rows, and a row carries its ticket's workers", () => {
  const live = liveTerminals(TERMINALS, null);
  assert.deepEqual(ticketWorkers({ id: "T3", agents: [] }, live), []);
  assert.deepEqual(ticketWorkers({ id: "T3" }, live), []);
  const tickets = [{ id: "T2", layer: 0, state: "IN PROGRESS", delivers: "x", agents: [{ name: "alpha-t2", role: "worker", address: "t9a" }] }];
  const [row] = ticketLayers(tickets, live)[0].tickets;
  assert.deepEqual(row.workers.map((w) => [w.name, w.state]), [["alpha-t2", "live"]]);
});

test("S10 without Herdr's terminals every agent is unknown, never not live", () => {
  const [w] = ticketWorkers({ id: "T2", agents: [{ name: "alpha-t2", role: "worker", address: "t9a" }] }, null);
  assert.equal(w.state, "unknown");
  assert.equal(w.open, null);
});

test("S11 the Runtime section is gone from the mod", () => {
  assert.equal(typeof model.runtimeAgents, "undefined");
  assert.equal(typeof model.runtimeNote, "undefined");
});
