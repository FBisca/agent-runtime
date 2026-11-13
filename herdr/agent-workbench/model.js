// The mod's pure logic: reads an answer of `agent-orchestrator workbench ... --json` and turns it into what the
// views draw. Nothing here touches Herdr, the clock or the machine, so `node --test` runs it. The layers, order,
// edges and nulls are the orchestrator's (the mod renders, it does not recompute them); what is decided here is
// how an answer, or the lack of one, reads.

// The mod is narrower than this many logical pixels: one column, the inspector as a drill-in.
export const NARROW_BELOW = 720;

// ---- reading an answer --------------------------------------------------------------------------------------

const WAY_OUT = {
  failed: "Run the command in this folder to see the error: agent-orchestrator workbench plans --json",
  timeout: "The command took too long. Run it in this folder to see why, then refresh with ↻",
  not_started:
    "agent-orchestrator did not start: install it (sh install.sh) and make sure it is on the PATH Herdr runs with",
  no_json: "The command printed something that is not a workbench answer: update agent-orchestrator, then refresh with ↻",
};

const failure = (kind, message, wayOut) => ({ ok: false, error: { kind, message, wayOut: wayOut || WAY_OUT[kind] } });

const firstLine = (text) => (String(text ?? "").split("\n").map((l) => l.trim()).find(Boolean) ?? "").slice(0, 240);

/** What a run of the workbench command amounts to. `result` is exec's `{code, stdout, stderr}`, or `{thrown}` with the
 *  message of the rejection (it could not start, timed out, the machine was unreachable). `shaped` says whether
 *  the JSON is the answer of this command. */
function read(result, shaped) {
  if (result.thrown !== undefined) {
    const kind = /time(d)?[ -]?out/i.test(result.thrown) ? "timeout" : "not_started";
    return failure(kind, firstLine(result.thrown) || kind);
  }
  let value;
  try {
    value = JSON.parse(String(result.stdout ?? "").trim());
  } catch {
    value = undefined;
  }
  if (value && typeof value === "object" && value.error && typeof value.error === "object") {
    const { kind, message, way_out: wayOut } = value.error;
    return failure(kind || "failed", String(message ?? kind ?? "the command refused"), wayOut);
  }
  if (value === undefined) {
    if (result.code !== 0) return failure("failed", firstLine(result.stderr) || "exit status " + result.code);
    return failure("no_json", "the command printed no JSON");
  }
  if (result.code !== 0) return failure("failed", firstLine(result.stderr) || "exit status " + result.code);
  if (!value || typeof value !== "object" || !shaped(value)) {
    return failure("no_json", "the JSON is not a workbench answer");
  }
  return { ok: true, value };
}

export const readPlans = (result) => read(result, (v) => Array.isArray(v.plans));
export const readPlan = (result) => read(result, (v) => Array.isArray(v.tickets));

const TITLE = {
  no_plans: "No plans",
  not_a_repo: "Not a repository",
  ambiguous: "Several plan directories",
  invalid_plan: "This plan does not load",
  no_snapshot: "The approved plan is missing",
  no_such_plan: "No such plan",
  legacy_plan: "A legacy plan has no ticket graph",
  failed: "The workbench command failed",
  timeout: "The workbench command timed out",
  not_started: "The workbench command did not start",
  no_json: "The workbench command printed no answer",
};

/** An error as the mod shows it, in the place it belongs to: `{plan: name}` or `{project: name}`. */
export function notice(error, scope) {
  const plan = scope.plan !== undefined;
  const empty = error.kind === "no_plans";
  return {
    scope: { kind: plan ? "plan" : "project", name: plan ? scope.plan : scope.project },
    kind: error.kind,
    title: TITLE[error.kind] ?? "The workbench cannot show this",
    message: error.message,
    wayOut: empty ? "Write one with /start-planning, and it shows here." : error.wayOut,
    tone: empty ? "muted" : "blocked",
  };
}

// ---- states -------------------------------------------------------------------------------------------------

const norm = (state) => String(state ?? "").toUpperCase().replace(/[-_\s]+/g, " ").trim();

const LOOKS = {
  COMPLETE: { key: "done", word: "complete", glyph: "✓", tone: "done" },
  RELEASED: { key: "done", word: "released", glyph: "✓", tone: "done" },
  "IN PROGRESS": { key: "working", word: "in progress", glyph: "◉", tone: "working" },
  BLOCKED: { key: "blocked", word: "blocked", glyph: "✕", tone: "blocked" },
  "TO DO": { key: "todo", word: "to do", glyph: "○", tone: "muted" },
};

/** How a ticket's state reads. A state the mod does not know is shown as its own text, never mapped to a known one. */
export function stateLook(state) {
  const known = LOOKS[norm(state)];
  if (known) return known;
  return { key: "unknown", word: state ? String(state).toLowerCase() : "unknown", glyph: "·", tone: "muted" };
}

const PLAN_TONE = { RUNNING: "working", APPROVED: "working", INTEGRATING: "working", ACCEPTED: "done", BLOCKED: "blocked" };

/** A plan's state: its own word (unknown ones pass through) and a tone. */
export const planState = (state) => ({ word: state ?? "unknown", tone: PLAN_TONE[norm(state)] ?? "muted" });

/** Ticket counts by state as a bar's numbers, or null when nothing measured them. */
export function progressOf(counts) {
  if (!counts || typeof counts.total !== "number") return null;
  const out = { done: 0, working: 0, blocked: 0, total: counts.total };
  for (const [state, n] of Object.entries(counts.by_state ?? {})) {
    const key = stateLook(state).key;
    if (key in out) out[key] += n;
  }
  return out;
}

// ---- the plan list ------------------------------------------------------------------------------------------

const base = (path) => String(path).split("/").pop();

/** The plans as rows: active before archived, newest (by dated file name) first. A Markdown plan is `legacy` and has
 *  no graph to open; a plan that did not load keeps its error and no progress. */
export function planRows(plans) {
  const rows = (plans ?? []).map((p) => ({
    path: p.path,
    slug: p.slug,
    format: p.format,
    legacy: Boolean(p.legacy),
    state: p.state ?? null,
    look: planState(p.state),
    progress: progressOf(p.tickets),
    error: p.error ?? null,
    selectable: p.format === "yaml" && !p.legacy,
  }));
  const archived = (r) => (r.state === "ARCHIVED" ? 1 : 0);
  return rows.sort((a, b) => archived(a) - archived(b) || base(b.path).localeCompare(base(a.path)));
}

/** The plan shown: the one the person picked if it is still listed, else the first that loads. */
export function pickPlan(rows, selected) {
  if (selected && rows.some((r) => r.path === selected && r.selectable)) return selected;
  const open = rows.filter((r) => r.selectable);
  return (open.find((r) => !r.error) ?? open[0])?.path ?? null;
}

// ---- tickets ------------------------------------------------------------------------------------------------

export const summaryOf = (delivers) => String(delivers ?? "").replace(/`/g, "").replace(/\s+/g, " ").trim();

export function verifiedOf(ticket) {
  if (ticket.verified === true) return { value: true, word: "verified", tone: "done" };
  if (ticket.verified === false) {
    return { value: false, word: "not verified", tone: stateLook(ticket.state).key === "done" ? "blocked" : "muted" };
  }
  return { value: null, word: "unknown", tone: "muted" };
}

const byId = (tickets) => new Map(tickets.map((t) => [t.id, t]));

/** One ticket row: its summary, state, blocked-by chips (each with the blocker's own state) and verified. */
export function ticketRow(ticket, tickets) {
  const known = byId(tickets);
  return {
    id: ticket.id,
    summary: summaryOf(ticket.delivers),
    layer: ticket.layer,
    look: stateLook(ticket.state),
    verified: verifiedOf(ticket),
    chips: (ticket.blocked_by ?? []).map((id) => ({ id, look: stateLook(known.get(id)?.state) })),
  };
}

/** The tickets grouped by layer, in the answer's order (the orchestrator orders them by layer, then plan order). */
export function ticketLayers(tickets) {
  const layers = [];
  for (const ticket of tickets) {
    let group = layers.at(-1);
    if (!group || group.layer !== ticket.layer) layers.push((group = { layer: ticket.layer, tickets: [] }));
    group.tickets.push(ticketRow(ticket, tickets));
  }
  return layers;
}

/** The ticket the inspector opens on: the one the person picked if it is still there, else the one in progress, else the
 *  first not yet done, else the first. */
export function pickTicket(tickets, selected) {
  if (tickets.some((t) => t.id === selected)) return selected;
  const key = (t) => stateLook(t.state).key;
  return (tickets.find((t) => key(t) === "working") ?? tickets.find((t) => key(t) !== "done") ?? tickets[0])?.id ?? null;
}

/** The ticket ids a selected ticket waits on and the ones waiting on it. */
export function relatives(tickets, id) {
  const ticket = tickets.find((t) => t.id === id);
  return {
    blockers: ticket ? [...(ticket.blocked_by ?? [])] : [],
    dependents: ticket ? tickets.filter((t) => (t.blocked_by ?? []).includes(id)).map((t) => t.id) : [],
  };
}

const GATE_TONE = { PASS: "done", FAIL: "blocked" };

// ---- execution: attempts and live terminals --------------------------------------------------------------------

/** The command that takes a person to a terminal. It is data so a test can read it; the view runs it. */
export const openTerminal = (address) => ({ command: "agent.focus", args: { terminal: address } });

/** What `terminals()` says is live in the shown space's machine, or null when it was not asked. An attempt's address
 *  is the one the runtime returned and it records no machine, so a space on another machine joins nothing. */
export function liveTerminals(terminals, machine) {
  if (!Array.isArray(terminals)) return null;
  const live = new Map();
  for (const t of terminals) if (t && t.state === "live" && typeof t.address === "string") live.set(t.address, t);
  return { machine: machine ?? null, live };
}

/** Where an attempt's terminal stands. `live` is a terminal in `terminals()` at exactly its address; a null address, a
 *  remote space or no terminals() at all is `unknown` (we cannot tell), and only a local address that is not live is
 *  `not live`. Only `live` offers Open. */
export function attemptTerminal(address, live) {
  const unknown = (why) => ({ state: "unknown", word: "unknown", why, open: null });
  if (address === null || address === undefined) return unknown("the runtime returned no address for this attempt");
  if (!live) return unknown("Herdr's terminals are not available");
  if (live.machine !== null) return unknown("this space is on another machine");
  if (live.live.has(address)) return { state: "live", word: "live", why: null, open: openTerminal(address) };
  return { state: "not live", word: "not live", why: "no live terminal has this address", open: null };
}

const isAgent = (t) => Boolean(t.program?.agent) || (t.status !== null && t.status !== undefined && t.status !== "");

/** What the Runtime section says instead of a list when it cannot tell: an attempt's address records no machine, so
 *  in a space on another machine nothing says which live agents an attempt names. Null when it can tell. */
export const runtimeNote = (machine) =>
  machine ? "Cannot tell which live agents a plan names in a space on another machine." : null;

/** The live agent terminals no attempt of the given plans names, as rows in tree order (a terminal under the one that
 *  started it, depth 0 for the first of each tree). Nothing is made up for them: no plan, no ticket. Only on this
 *  computer (bare addresses): in a space on another machine it lists none, see `runtimeNote`. */
export function runtimeAgents(terminals, plans, machine) {
  if (!Array.isArray(terminals) || machine) return [];
  const named = new Set();
  for (const plan of plans ?? []) {
    for (const ticket of plan.tickets ?? []) {
      for (const a of ticket.attempts ?? []) if (a.address) named.add(a.address);
    }
  }
  const here = (address) => !address.includes("/");
  const agents = terminals.filter((t) =>
    t && t.state === "live" && typeof t.address === "string" && here(t.address) && isAgent(t) && !named.has(t.address));
  const listed = new Set(agents.map((t) => t.address));
  const rows = [];
  const seen = new Set();
  const add = (t, depth) => {
    if (seen.has(t.address)) return;
    seen.add(t.address);
    rows.push({
      address: t.address,
      name: t.name || t.program?.name || t.address,
      status: t.status ?? null,
      parent: t.parent ?? null,
      depth,
      open: openTerminal(t.address),
    });
    for (const kid of agents) if (kid.parent === t.address) add(kid, depth + 1);
  };
  for (const t of agents) if (!t.parent || !listed.has(t.parent)) add(t, 0);
  for (const t of agents) add(t, 0); // a parent cycle has no root: every agent is still listed, once
  return rows;
}

/** Everything the one inspector shows for a ticket. Attempts and gates are null when the answer does not carry them.
 *  `live` (from `liveTerminals`) joins each attempt to its terminal by address; without it every terminal is unknown. */
export function inspector(ticket, tickets, live = null) {
  const known = byId(tickets);
  const attempts = Array.isArray(ticket.attempts) ? ticket.attempts : null;
  const gates = Array.isArray(ticket.gates) ? ticket.gates : null;
  return {
    id: ticket.id,
    delivers: summaryOf(ticket.delivers),
    state: stateLook(ticket.state),
    reason: ticket.reason ?? null,
    pr: ticket.pr ?? null,
    verified: verifiedOf(ticket),
    blockers: (ticket.blocked_by ?? []).map((id) => ({ id, look: stateLook(known.get(id)?.state) })),
    dependents: relatives(tickets, ticket.id).dependents,
    attempts: attempts && attempts.map((a) => ({
      taskId: a.task_id,
      kind: a.kind ?? null,
      status: a.status ?? null,
      startedAt: a.started_at ?? null,
      finishedAt: a.finished_at ?? null,
      assignmentId: a.assignment_id ?? null,
      address: a.address ?? null,
      terminal: attemptTerminal(a.address ?? null, live),
      claimed: Boolean(a.claimed),
      latest: Boolean(a.latest),
    })),
    gates: gates && gates.map((g) => ({
      scenario: g.scenario,
      phase: g.phase ?? null,
      result: g.result ?? null,
      reason: g.reason ?? null,
      tone: GATE_TONE[g.result] ?? "muted",
    })),
  };
}

/** The plan's header: its name, state, one progress bar, and the digests when it changed after approval. */
export function planHeader(plan) {
  const revision = plan.revision ?? {};
  return {
    slug: plan.slug,
    goal: plan.goal ?? null,
    state: planState(plan.state),
    progress: progressOf(plan.counts),
    drift: revision.drift ? { approved: revision.approved_digest, current: revision.current_digest } : null,
  };
}

// ---- layout -------------------------------------------------------------------------------------------------

export const layoutFor = (width) => (typeof width === "number" && width < NARROW_BELOW ? "narrow" : "wide");

/** What fills the mod: both panes at full width; at narrow widths the list, or the inspector once a ticket is drilled into. */
export function screenFor(layout, drilled, hasSelection) {
  if (layout === "wide") return "split";
  return drilled && hasSelection ? "inspector" : "list";
}

/** A narrow list row: one line. */
export const compactRow = (row) => `${row.look.glyph} ${row.id} ${row.summary} · ${row.look.word}`;

// ---- the tab bar's Workbench button -------------------------------------------------------------------------

// Herdr's own tabs mod draws a button for Files and Changes only, so the workbench layout puts this mod beside it
// (props `{ toggle: true }`) to open and close the side panel on the workbench the same way theirs do.
export const WORKBENCH_PANEL = "workbench";
const HERDR_PANELS = ["files", "changes"];

function findNode(node, id) {
  if (node?.id === id) return node;
  for (const child of node?.children ?? []) {
    const found = findNode(child, id);
    if (found) return found;
  }
  return null;
}

// The terminals' node, and the width the panel takes back when the workbench collapses (Herdr's own default).
const MAIN = "main";
export const PANEL_PX = 520;

/** Whether the panel shows the workbench, and whether the terminals are expanded over (the panel took the work area). */
function showingWorkbench(root) {
  const panel = findNode(root, "panel"), body = findNode(root, "panel-body");
  if (!panel || !(body?.children ?? []).some((c) => c.id === WORKBENCH_PANEL)) return null;
  return { panel, on: !panel.hidden && body.active === WORKBENCH_PANEL, expanded: findNode(root, MAIN)?.hidden === true };
}

/** The button's state and the Herdr commands a click runs, or null when the layout has no panel holding the workbench.
 *  Closing while expanded first gives the terminals back and the width the panel had (`restorePx`, 520 when unknown). */
export function panelToggle(root, restorePx = null) {
  const shown = showingWorkbench(root);
  if (!shown) return null;
  const { on, expanded } = shown;
  const commands = on
    ? [
      ...(expanded ? [["layout.show", { id: MAIN }], ["layout.resize", { id: "panel", px: restorePx ?? PANEL_PX }]] : []),
      ["layout.hide", { id: "panel" }],
    ]
    : [["layout.select", { stack: "panel-body", child: WORKBENCH_PANEL }], ["layout.show", { id: "panel" }]];
  return { on, commands };
}

/** The expand over the terminals: while the panel shows the workbench beside the terminals, `expand` hides them and
 *  gives the panel the whole work area (weight 1); once expanded, `commands` collapse back to `restorePx` (520 when
 *  unknown). `width` is the panel's px now, the one to remember before expanding; null when Herdr sizes it by weight.
 *  Null when no expand is offered: the panel does not show the workbench, or no terminals sit beside it. */
export function panelExpand(root, restorePx = null) {
  const shown = showingWorkbench(root);
  if (!shown) return null;
  const { panel, on, expanded } = shown;
  if (expanded) {
    return {
      expanded: true, offer: false,
      commands: [["layout.show", { id: MAIN }], ["layout.resize", { id: "panel", px: restorePx ?? PANEL_PX }]],
      width: null,
    };
  }
  const beside = (findNode(root, "work-body")?.children ?? []).some((c) => c.id === MAIN);
  if (!on || !beside) return null;
  const px = panel.size?.px;
  return {
    expanded: false,
    offer: true,
    commands: [["layout.hide", { id: MAIN }], ["layout.resize", { id: "panel", weight: 1 }]],
    width: typeof px === "number" ? px : null,
  };
}

// Herdr's tabs mod restores each space's panel only when it is in `workspace.panels` and hides it otherwise, so a
// workbench missing from that list closes on every space switch and on launch.
/** The panel list with the workbench added after the person's own, or null when it is already there. */
export function panelsWith(panels) {
  const list = Array.isArray(panels) ? panels : HERDR_PANELS;
  return list.includes(WORKBENCH_PANEL) ? null : [...list, WORKBENCH_PANEL];
}
