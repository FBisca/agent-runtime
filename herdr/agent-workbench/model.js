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
export const readTotals = (result) => read(result, (v) => Boolean(v.cards) && typeof v.cards === "object");

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

/** The plans as rows, in the order the command printed them (active before older, newest activity first: the
 *  orchestrator's order, not recomputed here). A Markdown plan is `legacy` and has no graph to open; a plan that did
 *  not load keeps its error and no progress. `group` is null when the answer does not say. */
export function planRows(plans) {
  return (plans ?? []).map((p) => ({
    path: p.path,
    slug: p.slug,
    format: p.format,
    legacy: Boolean(p.legacy),
    state: p.state ?? null,
    group: p.group ?? null,
    look: planState(p.state),
    progress: progressOf(p.tickets),
    error: p.error ?? null,
    selectable: p.format === "yaml" && !p.legacy,
  }));
}

/** The older line's words: collapsed until the person opens it. */
export const olderLabel = (count, open) => `${count} older ${open ? "▾" : "▸"}`;

// What each `needs_me` kind says, and the way to act on it. Only kinds, plans and tickets come from the answer; the
// words are the mod's. The ways name commands the person runs or the place to look, never text from a run.
const NEEDS = {
  needs_you: (t) => ({ title: `${t} needs you`, wayOut: `Open ${t}'s attempt below and answer in its terminal` }),
  unverified: (t) => ({ title: `${t} merged unverified`, wayOut: `Open ${t}: its gates say what was not proven` }),
  driver_gone: () => ({ title: "the driver is gone", wayOut: "Run /orchestrate on this plan to drive it again" }),
  error: () => ({ title: "the driver hit an error", wayOut: "Open the plan: the run's last error is in its header" }),
  ready_to_approve: () => ({ title: "draft ready to approve", wayOut: "Review it, then approve it with /start-planning" }),
  ready_to_drive: () => ({ title: "approved, not driven", wayOut: "Run /orchestrate to build it" }),
  fix_plan: () => ({ title: "the plan does not load", wayOut: "Open the plan file and fix what the error says" }),
  lead_pending: (t) => ({ title: `${t} waits on its Lead`, wayOut: `Answer ${t}'s Lead escalation in its terminal` }),
};

const needsItem = (item, rows) => {
  const make = NEEDS[item.kind];
  const text = make ? make(item.ticket ?? "a ticket") : { title: item.kind, wayOut: "Open the plan to see why" };
  return {
    plan: item.plan,
    ticket: item.ticket ?? null,
    kind: item.kind,
    path: rows.find((r) => r.slug === item.plan && r.selectable)?.path ?? null,
    ...text,
  };
};

/** Needs me, the active plans and the older ones as rows, from a plans answer. `olderLine(open)` is null when no plan
 *  is older, else its label and the rows it lists (none while collapsed). An answer that carries no `group` has nothing
 *  to fold away, so every plan is listed active. */
export function planSections(answer) {
  const rows = planRows(answer?.plans);
  const older = rows.filter((r) => r.group === "older");
  const active = rows.filter((r) => r.group !== "older");
  const needsMe = (Array.isArray(answer?.needs_me) ? answer.needs_me : []).map((i) => needsItem(i, rows));
  const olderLine = (open) => (older.length ? { label: olderLabel(older.length, open), rows: open ? older : [] } : null);
  return { needsMe, active, older, olderLine };
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
export function ticketRow(ticket, tickets, live = null) {
  const known = byId(tickets);
  return {
    id: ticket.id,
    summary: summaryOf(ticket.delivers),
    layer: ticket.layer,
    look: stateLook(ticket.state),
    verified: verifiedOf(ticket),
    chips: (ticket.blocked_by ?? []).map((id) => ({ id, look: stateLook(known.get(id)?.state) })),
    workers: ticketWorkers(ticket, live),
  };
}

/** The tickets grouped by layer, in the answer's order (the orchestrator orders them by layer, then plan order). */
export function ticketLayers(tickets, live = null) {
  const layers = [];
  for (const ticket of tickets) {
    let group = layers.at(-1);
    if (!group || group.layer !== ticket.layer) layers.push((group = { layer: ticket.layer, tickets: [] }));
    group.tickets.push(ticketRow(ticket, tickets, live));
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

/** An agent as a row names it: the ledger's name and role, and where its terminal stands. The terminal is joined by the
 *  address the ledger recorded and nothing else, never by a name (a terminal with the same name elsewhere is another
 *  agent). `status` is Herdr's, only for a live one; Open only for a live one. */
function workersOf(agents, live) {
  return (Array.isArray(agents) ? agents : []).map((a) => {
    const terminal = attemptTerminal(a.address ?? null, live);
    return {
      name: a.name,
      role: a.role ?? null,
      ticket: a.ticket ?? null,
      address: a.address ?? null,
      state: terminal.state,
      word: terminal.word,
      status: terminal.state === "live" ? (live.live.get(a.address)?.status ?? null) : null,
      open: terminal.open,
    };
  });
}

/** The agents of one ticket, from the ticket's own `agents`. */
export const ticketWorkers = (ticket, live = null) => workersOf(ticket.agents, live);

// ---- metrics: measured by the orchestrator, only worded here; a value it did not measure reads unknown, never 0 ----

const UNKNOWN = "unknown";
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** A duration in seconds: `42s`, `9m 00s`, `1h 02m`. */
export function duration(seconds) {
  const n = num(seconds);
  if (n === null) return UNKNOWN;
  const s = Math.round(n);
  const two = (v) => String(v).padStart(2, "0");
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${two(Math.floor((s % 3600) / 60))}m`;
  if (s >= 60) return `${Math.floor(s / 60)}m ${two(s % 60)}s`;
  return `${s}s`;
}

const count = (v) => (num(v) === null ? UNKNOWN : String(v));

function errorsText(errors) {
  if (!errors || typeof errors.by_gate !== "object" || errors.by_gate === null) return UNKNOWN;
  const gates = Object.entries(errors.by_gate);
  if (!gates.length) return "none";
  return `${gates.reduce((sum, [, n]) => sum + n, 0)} (${gates.map(([gate, n]) => `${gate} ${n}`).join(", ")})`;
}

/** The plan header's measures, or null when the plan carries no metrics (never driven: nothing is drawn). */
export function metricsHeader(metrics) {
  if (!metrics) return null;
  const rate = num(metrics.first_pass_rate);
  return {
    wall: duration(metrics.wall_seconds),
    firstPass: rate === null ? UNKNOWN : `${Math.round(rate * 100)}%`,
    errors: errorsText(metrics.errors),
  };
}

/** The inspector's measures for one ticket as label/text rows; a ticket the run never measured is unknown throughout. */
function measuresOf(m) {
  const phases = m?.phases ?? {};
  const escalations = m?.escalations ? Object.entries(m.escalations) : null;
  return [
    ["Elapsed", duration(m?.elapsed_seconds)],
    ["Work", duration(phases.work)],
    ["Review", duration(phases.review)],
    ["CI", duration(phases.ci)],
    ["Fix rounds", count(m?.fix_rounds)],
    ["Review rounds", count(m?.review_rounds)],
    ["Findings", count(m?.findings)],
    ["CI failures", count(m?.ci_failures)],
    ["Escalations", escalations ? (escalations.length ? escalations.map(([c, n]) => `${c} ${n}`).join(", ") : "none") : UNKNOWN],
    ["Model", m?.model ?? UNKNOWN],
  ].map(([label, text]) => ({ label, text }));
}

/** The timeline's lanes: one per ticket in the plan's order, a segment per phase placed as fractions of the run's span
 *  (an open segment runs to the span's end). Null unless wide and the plan has metrics with a span. */
export function timelineOf(metrics, tickets, layout) {
  if (layout !== "wide" || !metrics) return null;
  const start = Date.parse(metrics.span?.start), end = Date.parse(metrics.span?.end);
  if (!(end > start)) return null;
  const span = end - start;
  const at = (iso) => Math.min(Math.max((Date.parse(iso) - start) / span, 0), 1);
  return {
    lanes: tickets.map((ticket) => ({
      id: ticket.id,
      segments: (metrics.timeline?.[ticket.id] ?? []).map((seg) => {
        const left = at(seg.start);
        return { phase: seg.phase, left, width: (seg.end ? at(seg.end) : 1) - left };
      }),
    })),
  };
}

/** Everything the one inspector shows for a ticket. Attempts and gates are null when the answer does not carry them.
 *  `live` (from `liveTerminals`) joins each attempt to its terminal by address; without it every terminal is unknown. */
export function inspector(ticket, tickets, live = null, metrics = null) {
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
    measures: metrics ? measuresOf(metrics.tickets?.[ticket.id]) : null,
    gates: gates && gates.map((g) => ({
      scenario: g.scenario,
      phase: g.phase ?? null,
      result: g.result ?? null,
      reason: g.reason ?? null,
      tone: GATE_TONE[g.result] ?? "muted",
    })),
  };
}

/** The plan's header: a way back, the plan's name as the title and its whole goal (wrapping fully when wide, in at most
 *  three lines when narrow: `goalLines`), its state, one progress bar, and the digests when it changed after approval.
 *  The repository is not part of it. */
export function planHeader(plan, layout = "wide") {
  const revision = plan.revision ?? {};
  return {
    title: plan.slug,
    slug: plan.slug,
    back: true,
    goal: plan.goal ?? null,
    goalLines: layout === "narrow" ? GOAL_LINES_NARROW : null,
    state: planState(plan.state),
    progress: progressOf(plan.counts),
    metrics: metricsHeader(plan.metrics),
    drift: revision.drift ? { approved: revision.approved_digest, current: revision.current_digest } : null,
  };
}

export const GOAL_LINES_NARROW = 3;

// ---- home: the week's cards, Needs me, the active plans with their workers, the PRs to merge, the older line -----------

// The cards, in the order they are drawn. The orchestrator measured every value; an unmeasured one reads unknown, never 0.
const CARDS = [
  ["merged", "Merged"],
  ["first_pass", "First pass"],
  ["escalations", "Escalations"],
  ["tokens", "Tokens"],
  ["ticket_time", "Ticket time"],
];

/** A token count in words: `950`, `12.3k`, `1.2M`. */
export function tokenCount(n) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

function tokensCard(tokens) {
  const measured = num(tokens?.measured), tickets = num(tokens?.tickets);
  const total = num(tokens?.input) === null || num(tokens?.output) === null ? null : tokens.input + tokens.output;
  // Coverage is part of the number: a total over some of the tickets must not read as the whole week's.
  const note = measured === null || tickets === null || tickets === 0 ? null : `${measured} of ${tickets} tickets measured`;
  return { value: total === null || !measured ? UNKNOWN : tokenCount(total), note };
}

/** The five cards from a totals answer, or null when there is none (no card is drawn from a guess). */
export function cardsOf(totals) {
  const cards = totals?.cards;
  if (!cards || typeof cards !== "object") return null;
  const rate = num(cards.first_pass_rate);
  const values = {
    merged: { value: count(cards.merged), note: null },
    first_pass: { value: rate === null ? UNKNOWN : `${Math.round(rate * 100)}%`, note: null },
    escalations: { value: count(cards.escalations), note: null },
    tokens: tokensCard(cards.tokens),
    ticket_time: { value: duration(cards.median_ticket_seconds), note: null },
  };
  return CARDS.map(([id, label]) => ({ id, label, ...values[id] }));
}

/** Needs me as one row per plan, in the order each plan first appears, each holding that plan's items in order. */
function needsRows(items) {
  const rows = [];
  for (const item of items) {
    let row = rows.find((r) => r.plan === item.plan);
    if (!row) rows.push((row = { plan: item.plan, path: item.path, items: [] }));
    row.items.push({ ticket: item.ticket, kind: item.kind, title: item.title, wayOut: item.wayOut });
  }
  return rows;
}

/** What the mod opens on. `plans` and `totals` are the commands' answers (`totals` null when there is none: no cards),
 *  `live` is `liveTerminals(...)`. Workers are joined to Herdr's terminals only at their exact address. */
export function homeView(plans, totals, live = null) {
  const sections = planSections(plans);
  const byPlan = new Map((Array.isArray(plans?.plans) ? plans.plans : []).map((p) => [p.slug, p]));
  const awaitingMerge = [];
  for (const row of planRows(plans?.plans)) {
    for (const m of byPlan.get(row.slug)?.awaiting_merge ?? []) {
      awaitingMerge.push({ plan: row.slug, ticket: m.ticket, pr: m.pr ?? null });
    }
  }
  return {
    cards: cardsOf(totals),
    needs: needsRows(sections.needsMe),
    active: sections.active.map((row) => ({ ...row, workers: workersOf(byPlan.get(row.slug)?.agents, live) })),
    awaitingMerge,
    older: { count: sections.older.length, rows: sections.older },
  };
}

// ---- navigation ---------------------------------------------------------------------------------------------

/** Where the mod opens: on home. There is no plan picker. */
export const initialNav = () => ({ screen: "home" });

/** A plan or a card opens over home; back from either returns to it. An action the mod does not know changes nothing. */
export function navigate(nav, action) {
  switch (action?.type) {
    case "plan": return { screen: "plan", path: action.path };
    case "card": return { screen: "dashboard", card: action.id };
    case "back": return initialNav();
    default: return nav;
  }
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
