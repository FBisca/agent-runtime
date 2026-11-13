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

// The glyph and the button of a Needs me item, by its kind; a kind the mod does not know is opened, not guessed at.
const NEEDS_ACT = {
  lead_pending: { glyph: "⚑", action: "Answer" },
  needs_you: { glyph: "?", action: "Answer" },
  fix_plan: { glyph: "⚠", action: "Fix plan" },
  ready_to_drive: { glyph: "▶", action: "Drive" },
};
const NEEDS_OPEN = { glyph: "•", action: "Open" };

const needsItem = (item, rows) => {
  const make = NEEDS[item.kind];
  const text = make ? make(item.ticket ?? "a ticket") : { title: item.kind, wayOut: "Open the plan to see why" };
  return {
    plan: item.plan,
    ticket: item.ticket ?? null,
    kind: item.kind,
    path: rows.find((r) => r.slug === item.plan && r.selectable)?.path ?? null,
    ...(NEEDS_ACT[item.kind] ?? NEEDS_OPEN),
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
    row.items.push({
      ticket: item.ticket, kind: item.kind, glyph: item.glyph, action: item.action, title: item.title, wayOut: item.wayOut,
    });
  }
  return rows;
}

// ---- home's pictures: each card's dashboard in small, from the same answer; no data is no picture ----------------------

/** The palette slot of the nth part of a ranked list; the parts beyond the palette fold into one neutral `other`. */
function rankedParts(pairs) {
  const ranked = pairs.filter(([, n]) => num(n) > 0).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
  if (!ranked.length) return null;
  const parts = ranked.slice(0, DATA_SLOTS).map(([key, value], slot) => ({ key, value, slot }));
  const rest = sum(ranked.slice(DATA_SLOTS).map(([, n]) => n));
  if (rest > 0) parts.push({ key: "other", value: rest, slot: null });
  return { kind: "stack", parts };
}

const PICTURE = {
  merged: (totals) => {
    const values = sparkOf("merged", totals).map((v) => v ?? 0);
    return hasCount(values) ? { kind: "columns", values } : null;
  },
  first_pass: (totals) => (num(totals.cards?.first_pass_rate) === null ? null : { kind: "ring", value: totals.cards.first_pass_rate }),
  escalations: (totals) => rankedParts(entries(totals.escalations?.by_cause)),
  tokens: (totals) => {
    const measured = num(totals.cards?.tokens?.measured), tickets = num(totals.cards?.tokens?.tickets);
    return measured === null || tickets === null || tickets <= 0 ? null : { kind: "coverage", measured, unmeasured: Math.max(0, tickets - measured) };
  },
  ticket_time: (totals) => {
    const parts = STEP_ORDER.filter((step) => num(totals.time?.steps?.[step]?.median) !== null)
      .map((step) => ({ key: step, value: totals.time.steps[step].median, slot: stepLook(step).slot }));
    return parts.length ? { kind: "stack", parts } : null;
  },
};

const pictureOf = (card, totals) => PICTURE[card](totals);

/** How recently a plan moved: `active 3m ago`, `active 3h ago`, then `last active <day>`; null when it has no time. */
function recencyOf(iso, now) {
  const when = relativeTime(iso, new Date(now).toISOString());
  if (when === null) return null;
  return when.endsWith(" ago") ? `active ${when}` : `last active ${when}`;
}

/** What the mod opens on. `plans` and `totals` are the commands' answers (`totals` null when there is none: no cards),
 *  `live` is `liveTerminals(...)` and `now` the clock (ms) a plan's recency reads against. Workers are joined to Herdr's
 *  terminals only at their exact address; a plan lists its live ones and counts the others. */
export function homeView(plans, totals, live = null, now = Date.now()) {
  const sections = planSections(plans);
  const byPlan = new Map((Array.isArray(plans?.plans) ? plans.plans : []).map((p) => [p.slug, p]));
  const awaitingMerge = [];
  for (const row of planRows(plans?.plans)) {
    for (const m of byPlan.get(row.slug)?.awaiting_merge ?? []) {
      awaitingMerge.push({ plan: row.slug, ticket: m.ticket, pr: m.pr ?? null });
    }
  }
  const plan = (row) => {
    const raw = byPlan.get(row.slug);
    const workers = workersOf(raw?.agents, live);
    return {
      ...row,
      workers,
      live: workers.filter((w) => w.state === "live"),
      idle: workers.filter((w) => w.state !== "live").length,
      recency: recencyOf(raw?.last_activity, now),
      prs: list(raw?.awaiting_merge).map((m) => ({ ticket: m.ticket, ...prOf(m.pr) })),
    };
  };
  const cards = cardsOf(totals);
  return {
    cards: cards && cards.map((c) => ({ ...c, picture: pictureOf(c.id, totals) })),
    needs: needsRows(sections.needsMe),
    active: sections.active.map(plan),
    awaitingMerge,
    older: { count: sections.older.length, rows: sections.older.map(plan) },
  };
}

/** How many cards fit a row: two when narrow, so a card keeps room for its number. */
export const cardsPerRow = (layout) => (layout === "narrow" ? 2 : 5);

/** Plans are one to a row at every width: a plan card has more to say than a card's number. */
export const plansPerRow = () => 1;

// ---- a card's dashboard --------------------------------------------------------------------------------------------

const list = (v) => (Array.isArray(v) ? v : []);
const entries = (v) => (v && typeof v === "object" && !Array.isArray(v) ? Object.entries(v) : []);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const percent = (r) => (num(r) === null ? UNKNOWN : `${Math.round(r * 100)}%`);
const tokenSum = (r) => (num(r?.input) === null && num(r?.output) === null ? null : (r.input ?? 0) + (r.output ?? 0));
const tokenText = (r) => { const n = tokenSum(r); return n === null ? UNKNOWN : tokenCount(n); };
const sum = (values) => values.reduce((total, v) => total + v, 0);

// The mod's data palette has four slots (aqua, violet, magenta, green: `ui.js` holds the colours). The model hands out
// slots by what a thing is, never by where it arrives, so a reason or a step keeps its colour whatever the order.
export const DATA_SLOTS = 4;

export const STEP_ORDER = ["work", "review", "ci", "merge"];

/** A step's slot in the data palette; a step the mod does not know has none (a neutral). */
export const stepLook = (step) => {
  const at = STEP_ORDER.indexOf(step);
  return { slot: at < 0 ? null : at, shade: 1 };
};

// A finding's reason is `"<source>/<finding_kind>"`. Fixed order: the gate, the review's kinds from blocking to deferred, CI,
// then a merge conflict; a reason this mod does not know follows them, by name.
export const REASON_ORDER = [
  "verify/blocking", "review/blocking", "review/concern", "review/question", "review/nit", "review/deferred",
  "ci/blocking", "conflict/blocking",
];
const REASON_SLOT = { verify: 0, review: 1, ci: 2, conflict: 3 };
// A review's kinds are shades of one colour, strongest first, so the source reads at a glance and the kind in its label.
const REVIEW_SHADE = { blocking: 1, concern: 0.8, question: 0.6, nit: 0.45, deferred: 0.3 };
const REASON_WORD = { "verify/blocking": "gate check", "ci/blocking": "CI failed", "conflict/blocking": "merge conflict" };

export function orderedReasons(keys) {
  const rank = (k) => { const at = REASON_ORDER.indexOf(k); return at < 0 ? REASON_ORDER.length : at; };
  return [...keys].sort((a, b) => rank(a) - rank(b) || (a < b ? -1 : a > b ? 1 : 0));
}

/** How a reason reads and which slot and shade of the palette it takes. An unknown reason reads as itself, in a neutral. */
export function reasonLook(key) {
  const [source, kind] = String(key).split("/");
  const slot = REASON_SLOT[source] ?? null;
  const word = REASON_WORD[key] ?? (source === "review" && kind ? `review · ${kind}` : String(key));
  return { slot, shade: source === "review" ? (REVIEW_SHADE[kind] ?? 0.5) : 1, label: word };
}

/** A round top for an axis: 1, 2 or 5 times a power of ten, covering `value`. */
export function niceTop(value) {
  if (!(value > 0)) return 1;
  const base = 10 ** Math.floor(Math.log10(value));
  const factor = value / base;
  return ([1, 2, 5, 10].find((f) => factor <= f + 1e-9) ?? 10) * base;
}

const AXIS_WORD = {
  count: (v) => String(v),
  percent: (v) => `${Math.round(v * 100)}%`,
  seconds: (v) => duration(v),
};

/** The axis of a scaled chart: 0, half and a round top that covers `value`, worded in `unit`. */
export function axisFor(value, unit = "count", top = null) {
  const max = top ?? niceTop(value);
  const ticks = [0, max / 2, max];
  return { max, ticks, unit, labels: ticks.map((v) => (v === 0 && unit !== "percent" ? "0" : AXIS_WORD[unit](v))) };
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// This runtime has no Intl: a day is worded by hand, in UTC like every day the CLI returns.
const dayName = (ms) => { const d = new Date(ms); return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };

/** When a thing happened, read against the end of the window the answer covers: `5m ago`, `3h ago`, then the day. */
export function relativeTime(iso, end) {
  const at = Date.parse(iso ?? "");
  if (!Number.isFinite(at)) return null;
  const to = Date.parse(end ?? "");
  if (!Number.isFinite(to)) return dayName(at);
  const seconds = (to - at) / 1000;
  if (seconds < 3600) return `${Math.max(1, Math.floor(seconds / 60))}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return dayName(at);
}

/** The URL of a GitHub pull request, only when it is exactly `https://github.com/<owner>/<repo>/pull/<n>`: the one thing
 *  a chip opens. Anything else is shown and never opened. */
export function prLink(url) {
  if (typeof url !== "string") return null;
  const match = /^https:\/\/github\.com\/([A-Za-z0-9][A-Za-z0-9-]*)\/([A-Za-z0-9._-]+)\/pull\/[0-9]+$/.exec(url);
  return match && match[2] !== "." && match[2] !== ".." ? url : null;
}

/** How a PR reads: `#n` from the URL's number, and the link only when `prLink` accepts it. */
function prOf(pr) {
  if (typeof pr !== "string" || !pr) return { link: null, linkText: null };
  const number = /\/pull\/([0-9]+)\/?$/.exec(pr)?.[1];
  return { link: prLink(pr), linkText: number ? `#${number}` : pr.slice(0, 24) };
}

const dayOf = (iso) => (typeof iso === "string" && /^\d{4}-\d\d-\d\d/.test(iso) ? iso.slice(0, 10) : null);

// What a dashboard's click filters select, by the kind of filter. A filter of another kind is not the card's: it selects all.
const SAME = {
  day: (f, r) => r.day === f.day,
  reason: (f, r) => Object.hasOwn(r.reasons ?? {}, f.key),
  cell: (f, r) => r.plan === f.plan && r.cause === f.cause,
};
const FILTER_OF = { merged: "day", first_pass: "reason", escalations: "cell" };
const EMPTY = {
  day: (f) => `Nothing merged on ${dayName(Date.parse(f.day))}.`,
  reason: (f) => `No fixed ticket had ${reasonLook(f.key).label} findings.`,
  cell: (f) => `No ${f.cause} escalation among ${f.plan}'s latest.`,
};

// What an applied filter reads as, in the line above the rows.
const FILTER_TEXT = {
  day: (f) => dayName(Date.parse(f.day)),
  reason: (f) => reasonLook(f.key).label,
  cell: (f) => `${f.plan} · ${f.cause}`,
};

const row = (label, text, extra = {}) => ({
  label, text, link: null, linkText: null, plan: null, day: null, reasons: null, cause: null, ...extra,
});

// One builder per card of what it lists, from the answer's own sections.
const LIST = {
  merged: (totals) => ({
    title: "Merged",
    rows: list(totals.merged?.tickets).map((r) => row(`${r.plan} ${r.ticket}`, relativeTime(r.merged_at, totals.window?.end) ?? "", {
      ...prOf(r.pr), plan: r.plan, day: dayOf(r.merged_at),
    })),
  }),
  first_pass: (totals) => ({
    title: "Fixed tickets",
    rows: list(totals.first_pass?.fixed).map((r) => row(
      `${r.plan} ${r.ticket}`, `${plural(r.fix_rounds, "fix round")} · ${plural(r.findings, "finding")}`,
      { plan: r.plan, reasons: r.reasons && typeof r.reasons === "object" ? r.reasons : null },
    )),
  }),
  escalations: (totals) => ({
    title: "Latest",
    rows: list(totals.escalations?.latest).map((r) => row(
      // A unit of no ticket (the archive) names itself; an answer from before it did names the plan alone.
      [r.plan, r.unit ?? r.ticket].filter(Boolean).join(" "),
      [r.cause, relativeTime(r.at, totals.window?.end)].filter(Boolean).join(" · "),
      { plan: r.plan, cause: r.cause, day: dayOf(r.at) },
    )),
  }),
  tokens: (totals) => ({
    title: "Top tickets",
    rows: list(totals.tokens?.top).map((r) => row(`${r.plan} ${r.ticket}`, `${tokenText(r)} · ${r.model ?? UNKNOWN}`, { plan: r.plan })),
  }),
  ticket_time: (totals) => ({
    title: "Slowest tickets",
    rows: list(totals.time?.slowest).map((r) => row(`${r.plan} ${r.ticket}`, duration(r.seconds), { ...prOf(r.pr), plan: r.plan })),
  }),
};

// Which card, of `cardsOf`, each part of a totals answer belongs to: its change, and its week as a series.
const CHANGE_OF = { merged: "merged", first_pass: "first_pass_rate", escalations: "escalations", tokens: "tokens", ticket_time: "median_ticket_seconds" };
const SPARK = {
  merged: (t) => list(t.merged?.by_day).map((d) => num(d.count)),
  first_pass: (t) => list(t.first_pass?.by_day).map((d) => num(d.rate)),
  escalations: (t) => list(t.escalations?.by_day).map((d) => num(d.count)),
  tokens: (t) => list(t.tokens?.by_day).map((d) => num(d.output)),
  ticket_time: (t) => list(t.time?.by_day).map((d) => num(d.median)),
};

// A sparkline is the whole week: an answer from before the series (days with something, not every date) draws none.
const sparkOf = (card, totals) => {
  const values = SPARK[card](totals);
  return values.length >= (num(totals.window?.days) ?? 7) ? values : [];
};

/** The change against last week as the headline words it: `▲ 300% vs last week`, and when there is none (the CLI says
 *  null: last week was unmeasured or 0; or it does not say) `first week measured`, never 0%. */
export function changeText(change) {
  if (num(change) === null) return "first week measured";
  const n = Math.round(Math.abs(change) * 100);
  if (n === 0) return "no change vs last week";
  return `${change > 0 ? "▲" : "▼"} ${n}% vs last week`;
}

const chartHeight = (layout) => (layout === "narrow" ? 120 : 200);

const hasCount = (values) => values.some((v) => v > 0);

const FILTER_DIM = (filter, kind, matches) => Boolean(filter) && filter.kind === kind && !matches;

function mergedCharts(totals, filter) {
  const days = list(totals.merged?.by_day);
  // Plans arrive per day; the columns need them across the week, so the colour of a plan is its rank in the week.
  if (!days.length || days.some((d) => !d.by_plan || typeof d.by_plan !== "object")) return [];
  if (!hasCount(days.map((d) => d.count))) return [];
  const week = new Map();
  for (const d of days) for (const [plan, n] of entries(d.by_plan)) week.set(plan, (week.get(plan) ?? 0) + n);
  const ranked = [...week.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).map(([plan]) => plan);
  const shown = ranked.slice(0, DATA_SLOTS);
  const series = shown.map((key, slot) => ({ key, slot }));
  if (ranked.length > DATA_SLOTS) series.push({ key: "other", slot: null });
  const columns = days.map((d) => {
    const folded = new Map();
    for (const [plan, n] of entries(d.by_plan)) {
      const key = shown.includes(plan) ? plan : "other";
      folded.set(key, (folded.get(key) ?? 0) + n);
    }
    const parts = series.filter((s) => folded.has(s.key)).map((s) => ({ key: s.key, value: folded.get(s.key), slot: s.slot }));
    const ms = Date.parse(d.day);
    return {
      day: d.day, label: Number.isFinite(ms) ? WEEKDAYS[new Date(ms).getUTCDay()] : d.day, total: d.count, parts,
      filter: { kind: "day", day: d.day }, dim: FILTER_DIM(filter, "day", filter?.day === d.day),
    };
  });
  return [{
    kind: "stacked-columns", id: "days", title: "Merged per day, by plan", series, days: columns,
    axis: axisFor(Math.max(...columns.map((c) => c.total))),
  }];
}

const ROUND_ROWS = [["0", "first pass"], ["1", "1 round"], ["2", "2 rounds"], ["3+", "3+ rounds"]];

function firstPassCharts(totals, filter) {
  const charts = [];
  const rate = num(totals.cards?.first_pass_rate);
  if (rate !== null) charts.push({ kind: "ring", id: "ring", title: "First pass", value: rate, text: percent(rate) });
  const rounds = totals.first_pass?.rounds;
  if (rounds && typeof rounds === "object") {
    const rows = ROUND_ROWS.map(([key, label]) => ({ key, label, value: num(rounds[key]) ?? 0, text: String(num(rounds[key]) ?? 0) }));
    if (hasCount(rows.map((r) => r.value))) {
      charts.push({ kind: "bars", id: "rounds", title: "Tickets by fix rounds", rows, axis: axisFor(Math.max(...rows.map((r) => r.value))) });
    }
  }
  const reasons = entries(totals.first_pass?.by_reason).filter(([, n]) => num(n) > 0);
  if (reasons.length) {
    const total = sum(reasons.map(([, n]) => n));
    const byKey = new Map(reasons);
    const parts = orderedReasons(byKey.keys()).map((key) => ({
      key, value: byKey.get(key), share: byKey.get(key) / total, ...reasonLook(key),
      filter: { kind: "reason", key }, dim: FILTER_DIM(filter, "reason", filter?.key === key),
    }));
    charts.push({ kind: "stack", id: "reasons", title: "Findings by reason", parts, axis: axisFor(1, "percent", 1) });
  }
  return charts;
}

function escalationCharts(totals, filter, layout) {
  const causes = entries(totals.escalations?.by_cause).filter(([, n]) => num(n) > 0);
  const matrix = entries(totals.escalations?.by_plan_cause);
  if (layout !== "narrow" && matrix.length) {
    const names = [...new Set([...causes.map(([c]) => c), ...matrix.flatMap(([, by]) => Object.keys(by ?? {}))])];
    const cells = matrix.flatMap(([plan, by]) => names.map((cause) => ({
      plan, cause, value: num(by?.[cause]) ?? 0, filter: { kind: "cell", plan, cause },
      dim: FILTER_DIM(filter, "cell", filter?.plan === plan && filter?.cause === cause),
    })));
    const max = Math.max(...cells.map((c) => c.value));
    if (max > 0) {
      return [{
        kind: "heatmap", id: "plan-cause", title: "Escalations · plan × cause", plans: matrix.map(([p]) => p), causes: names,
        cells: cells.map((c) => ({ ...c, intensity: c.value / max })), max,
      }];
    }
  }
  if (!causes.length) return [];
  const rows = causes.map(([key, value]) => ({ key, label: key, value, text: String(value) }));
  return [{ kind: "bars", id: "causes", title: "Escalations by cause", rows, axis: axisFor(Math.max(...rows.map((r) => r.value))) }];
}

function tokenCharts(totals) {
  const rows = entries(totals.tokens?.by_plan).filter(([, v]) => num(v?.tickets) > 0).map(([label, v]) => {
    const measured = num(v.measured) ?? 0;
    return { label, measured, unmeasured: v.tickets - measured, text: `${measured}/${v.tickets} · ${tokenText(v)}` };
  });
  if (!rows.length) return [];
  return [{
    kind: "coverage", id: "coverage", title: "Tickets with tokens measured", rows,
    axis: axisFor(Math.max(...rows.map((r) => r.measured + r.unmeasured))),
  }];
}

function timeCharts(totals) {
  const charts = [];
  const tickets = list(totals.time?.tickets).filter((t) => STEP_ORDER.some((s) => num(t.phases?.[s]) !== null));
  if (tickets.length) {
    const seconds = (t) => Math.max(num(t.seconds) ?? 0, sum(STEP_ORDER.map((s) => num(t.phases?.[s]) ?? 0)));
    const axis = axisFor(Math.max(...tickets.map(seconds)), "seconds");
    const lanes = tickets.map((t) => {
      let at = 0;
      const segments = [];
      for (const step of STEP_ORDER) {
        const value = num(t.phases?.[step]);
        if (value === null) continue; // an unmeasured phase is no segment
        segments.push({ step, seconds: value, left: at / axis.max, width: value / axis.max, slot: stepLook(step).slot });
        at += value;
      }
      return { label: `${t.plan} ${t.ticket}`, seconds: t.seconds, text: duration(t.seconds), segments };
    });
    charts.push({ kind: "swimlanes", id: "slow", title: "Slowest tickets · where the time went", lanes, axis });
  }
  const steps = STEP_ORDER.map((step) => [step, totals.time?.steps?.[step]]).filter(([, s]) => num(s?.median) !== null);
  if (steps.length) {
    const rows = steps.map(([step, s]) => ({ step, median: s.median, p90: num(s.p90) }));
    charts.push({
      kind: "spread", id: "steps", title: "Per step · median and p90", rows,
      axis: axisFor(Math.max(...rows.map((r) => r.p90 ?? r.median)), "seconds"),
    });
  }
  return charts;
}

const CHARTS = {
  merged: (totals, filter) => mergedCharts(totals, filter),
  first_pass: (totals, filter) => firstPassCharts(totals, filter),
  escalations: (totals, filter, layout) => escalationCharts(totals, filter, layout),
  tokens: (totals) => tokenCharts(totals),
  ticket_time: (totals) => timeCharts(totals),
};

/** A card's dashboard from the totals answer: the headline (value, change against last week, the week's series), the
 *  number of columns, the charts the answer carries a section for (a section it lacks draws none), and the rows behind
 *  them. `filter` is the click filter of the navigation: it keeps only the rows it selects and dims what the charts show
 *  of the rest; `empty` says so when none is left. */
export function dashboardView(card, totals, layout, filter = null) {
  const columns = layout === "narrow" ? 1 : 2;
  const cards = cardsOf(totals);
  const headline = cards?.find((c) => c.id === card);
  if (!headline || !CHARTS[card]) return { headline: null, columns, charts: [], rows: [], rowsTitle: null, filtered: null, empty: null };
  const kind = FILTER_OF[card];
  const applied = filter && filter.kind === kind ? filter : null;
  const { title, rows: all } = LIST[card](totals);
  const rows = applied ? all.filter((r) => SAME[kind](applied, r)) : all;
  return {
    headline: {
      label: headline.label, value: headline.value, note: headline.note,
      change: changeText(totals.change?.[CHANGE_OF[card]]), spark: sparkOf(card, totals),
    },
    columns,
    chartHeight: chartHeight(layout),
    charts: CHARTS[card](totals, applied, layout),
    rowsTitle: title,
    filtered: applied ? FILTER_TEXT[kind](applied) : null,
    rows,
    empty: applied && !rows.length ? EMPTY[kind](applied) : null,
  };
}

// ---- navigation ---------------------------------------------------------------------------------------------

/** Where the mod opens: on home. There is no plan picker. */
export const initialNav = () => ({ screen: "home" });

const sameFilter = (a, b) => {
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((k) => a[k] === b[k]);
};

/** A plan or a card opens over home; back from either returns to it. A click filter lives on the open dashboard: the
 *  same filter again clears it, another replaces it, and a card opened afresh has none. An action the mod does not know
 *  changes nothing. */
export function navigate(nav, action) {
  switch (action?.type) {
    case "plan": return { screen: "plan", path: action.path };
    case "card": return { screen: "dashboard", card: action.id };
    case "filter": {
      if (nav.screen !== "dashboard" || !action.filter) return nav;
      const same = nav.filter && sameFilter(nav.filter, action.filter);
      return { ...nav, filter: same ? null : { ...action.filter } };
    }
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
