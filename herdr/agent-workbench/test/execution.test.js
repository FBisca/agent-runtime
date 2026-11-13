import test from "node:test";
import assert from "node:assert/strict";
import { inspector, liveTerminals } from "../model.js";
import { PLAN } from "./fixtures.js";

// A terminal as Herdr's terminals() describes it: address, state, parent, status, program. Local addresses are bare.
const term = (address, extra = {}) => ({
  address, state: "live", name: "agent " + address, parent: null, status: "working", cwd: "/work/agent-runtime",
  program: { key: "claude", name: "Claude Code", agent: true, shell: false }, ...extra,
});

const attempt = (address, extra = {}) => ({
  task_id: "t1", kind: "worker", status: "RUNNING", started_at: null, finished_at: null, assignment_id: "a1",
  address, claimed: false, latest: true, ...extra,
});

const ticketWith = (attempts) => ({ ...PLAN.tickets[0], attempts });
const view = (attempts, live) => inspector(ticketWith(attempts), PLAN.tickets, live).attempts;

const local = (terminals) => liveTerminals(terminals, null);

test("S16 an attempt whose address is a live terminal offers Open, which focuses that terminal", () => {
  const [a] = view([attempt("t7")], local([term("t7"), term("t8")]));
  assert.equal(a.terminal.state, "live");
  assert.deepEqual(a.terminal.open, { command: "agent.focus", args: { terminal: "t7" } });
});

test("S16 a null address is unknown and offers no Open, whatever terminals are live", () => {
  const [a] = view([attempt(null)], local([term("t7")]));
  assert.equal(a.terminal.state, "unknown");
  assert.equal(a.terminal.open, null);
  assert.match(a.terminal.word, /unknown/);
});

test("S16 an address that is no longer live, or no longer there, is not live and offers no Open", () => {
  const [ended, gone] = view([attempt("t7"), attempt("t9", { task_id: "t1r1" })], local([term("t7", { state: "ended" })]));
  assert.equal(ended.terminal.state, "not live");
  assert.equal(ended.terminal.open, null);
  assert.equal(gone.terminal.state, "not live");
  assert.equal(gone.terminal.open, null);
});

test("S16 a remote space never joins an attempt to a terminal: unknown, no Open, even for a bare address that is live here", () => {
  const live = liveTerminals([term("t7"), term("box/t7")], "box");
  const [a] = view([attempt("t7")], live);
  assert.equal(a.terminal.state, "unknown");
  assert.equal(a.terminal.open, null);
});

test("S16 an answer read while terminals() is not available shows unknown, never not live", () => {
  const [a] = view([attempt("t7")], undefined);
  assert.equal(a.terminal.state, "unknown");
  assert.equal(a.terminal.open, null);
});

test("S16 an address is matched exactly: t7 is not t70, and a name is never an address", () => {
  const [a] = view([attempt("t7")], local([term("t70"), term("x", { name: "t7" })]));
  assert.equal(a.terminal.state, "not live");
});
