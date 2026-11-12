import test from "node:test";
import assert from "node:assert/strict";
import { inspector, runtimeAgents, liveTerminals } from "../model.js";
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

const plans = (...addresses) => [{
  ...PLAN,
  tickets: [{ ...PLAN.tickets[0], attempts: addresses.map((address, i) => attempt(address, { task_id: "t" + i })) }],
}];

test("S17 live agent terminals no attempt names are listed with their parent tree, each offering Open", () => {
  const all = [
    term("t1"), // named by an attempt
    term("t2"),
    term("t3", { parent: "t2" }),
    term("t4", { parent: "t3" }),
    term("t5", { parent: "t1" }), // a sub-agent of a planned worker is itself unplanned
  ];
  const section = runtimeAgents(all, plans("t1"), null);
  assert.deepEqual(section.map((r) => [r.address, r.depth, r.parent]), [["t2", 0, null], ["t3", 1, "t2"], ["t4", 2, "t3"], ["t5", 0, "t1"]]);
  for (const r of section) assert.deepEqual(r.open, { command: "agent.focus", args: { terminal: r.address } });
  assert.deepEqual(Object.keys(section[0]).filter((k) => /plan|ticket/i.test(k)), [], "no plan or ticket is made up for them");
});

test("S17 only live agents count: an ended terminal, a plain shell and a null-address attempt do not hide or add rows", () => {
  const all = [
    term("t1", { state: "ended" }),
    term("t2", { status: null, program: { key: "shell", name: "zsh", agent: false, shell: true } }),
    term("t3"),
  ];
  assert.deepEqual(runtimeAgents(all, plans(null), null).map((r) => r.address), ["t3"]);
});

test("S17 an attempt of any shown plan names its terminal, and none are listed when every live agent is planned", () => {
  const all = [term("t1"), term("t2")];
  assert.deepEqual(runtimeAgents(all, [...plans("t1"), ...plans("t2")], null), []);
});

test("S17 in a space on a machine, only that machine's terminals are listed, and in a local one only the bare ones", () => {
  const all = [term("t1"), term("box/t2"), term("other/t3")];
  assert.deepEqual(runtimeAgents(all, [], null).map((r) => r.address), ["t1"]);
  assert.deepEqual(runtimeAgents(all, [], "box").map((r) => r.address), ["box/t2"]);
});

test("S17 a parent cycle or a parent that is not listed still lists every agent once", () => {
  const all = [term("t1", { parent: "t2" }), term("t2", { parent: "t1" }), term("t3", { parent: "t9" })];
  const section = runtimeAgents(all, [], null);
  assert.deepEqual(section.map((r) => r.address).sort(), ["t1", "t2", "t3"]);
});
