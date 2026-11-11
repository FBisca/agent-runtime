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

/** Everything the one inspector shows for a ticket. Attempts and gates are null when the answer does not carry them. */
export function inspector(ticket, tickets) {
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
