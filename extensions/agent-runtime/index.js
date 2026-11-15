/**
 * The Pi rhythm extension: Pi has no Stop or PreToolUse hook of its own, so this adapter is where the
 * runtime's rhythm reaches a Pi session.
 *
 * It asks `agent-runtime-hook` for the Stop decision (the same entry Claude Code and Codex run) and
 * turns a block into one more provider request; it watches the lead's driver `wakes.jsonl` and pushes
 * a wake into an idle session instead of the lead sleeping for it; and it refuses a shell command that
 * sleeps a minute or longer, naming `wait(all)` as the way to wait. Every decision is the runtime's:
 * the hook decides the stop, the driver writes the wakes, and the server refuses a mutating verb while
 * the lead has undelivered reports. A Pi session without this extension keeps its own scheduling.
 */

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { toToolCall, toTurnEnd, toWake } from "./handlers.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WAKE_POLL_MS = 2000; // how often wakes.jsonl is read while the session sits idle
const PANE_TTL_MS = 15000; // how long one reading of the terminals stands before hc is asked again
const HOOK_TIMEOUT_MS = 5000;
const HC_TIMEOUT_MS = 2000;
const STOP_PAYLOAD = { hook_event_name: "Stop", stop_hook_active: false };
const NUDGE_TYPE = "agent-runtime-stop"; // the guard's reason, as a custom message
const WAKE_TYPE = "agent-runtime-wake"; // one driver wake, as a custom message
const PANE_PREFIX = "orchestrate-"; // the driver's pane, named after the plan it runs

/** The hook entry the runtime ships: an explicit override, this checkout's own, or the installed one. */
export function hookPath(env = process.env) {
  if (env.AGENT_RUNTIME_HOOK) return env.AGENT_RUNTIME_HOOK;
  const checkout = path.resolve(realpath(HERE), "..", "..", "bin", "agent-runtime-hook");
  if (fs.existsSync(checkout)) return checkout;
  return "agent-runtime-hook";
}

/**
 * What `agent-runtime-hook stop --cli pi --dialect json` answers for this session, or null. It fails
 * open the way the hooks do: a missing entry, an error or an unreadable answer allows the stop.
 */
export function stopDecision(options = {}) {
  const env = options.env || process.env;
  let result;
  try {
    result = spawnSync(hookPath(env), ["stop", "--cli", "pi", "--dialect", "json"], {
      input: JSON.stringify(STOP_PAYLOAD),
      env,
      encoding: "utf8",
      timeout: HOOK_TIMEOUT_MS,
    });
  } catch {
    return null;
  }
  if (!result || result.error || result.status !== 0) return null;
  try {
    return JSON.parse(result.stdout || "null");
  } catch {
    return null;
  }
}

/** A Pi turn-end proposal from the hook's answer: the reason stays the hook's, word for word. */
export function turnProposal(answer) {
  const proposal = toTurnEnd(answer);
  if (!proposal) return undefined;
  // Pi's session entry needs an owner and a display flag; the custom message itself is the reason.
  const entries = proposal.entries.map((entry) => ({
    ...entry,
    customType: NUDGE_TYPE,
    display: false,
  }));
  return { continue: proposal.continue, entries };
}

/** Where this extension remembers which wake lines it has pushed: one cursor per session and run. */
export function statePath(env = process.env) {
  return path.join(stateRoot(env), "agent-runtime", "pi-wakes.json");
}

/**
 * The wake files to watch: an explicit list, or every driven run under the orchestrator's state
 * directory (`<state>/orchestrate/<repo>/<slug>/wakes.jsonl`, where the driver books one wake per
 * escalation).
 */
export function wakeFiles(env = process.env) {
  if (env.AGENT_RUNTIME_WAKES) return env.AGENT_RUNTIME_WAKES.split(path.delimiter).filter(Boolean);
  const files = [];
  for (const repo of directories(path.join(stateRoot(env), "orchestrate"))) {
    for (const run of directories(repo)) {
      const file = path.join(run, "wakes.jsonl");
      if (fs.existsSync(file)) files.push(file);
    }
  }
  return files;
}

/**
 * The session a wake push belongs to: its hc terminal, which is what the runtime and the driver both
 * name a session by. Without one (a native session) the explicit wake list is all it has.
 */
export function sessionId(env = process.env) {
  return env.HC_TERMINAL_ID || "unattributed";
}

/**
 * Whether this session may take the wakes in `wakes.jsonl` (its run is `slug`). Two ways, and no
 * more: the run's driver pane names this terminal as its lead — the driver runs in an hc terminal
 * named `orchestrate-<slug>` whose parent is the terminal that launched it, and a run started inside
 * the lead's own terminal is that terminal — or the file was named explicitly by `AGENT_RUNTIME_WAKES`
 * and this terminal is itself a lead (it has no parent), because an explicitly named file has no run
 * pane to ask. A Pi worker, another session on the same machine, can show neither: it neither
 * receives the lead's wakes nor spends its cursor, so the wake stays for the lead and `await` and the
 * driver's own `hc send` still carry it.
 */
export function ownsRun(slug, rows, terminal, explicit = false) {
  if (!terminal) return explicit;
  const named = rows.some(
    (row) =>
      row.name.startsWith(PANE_PREFIX) &&
      row.name.length > PANE_PREFIX.length &&
      String(slug).startsWith(row.name.slice(PANE_PREFIX.length)) &&
      (row.parent === terminal || row.address === terminal),
  );
  if (named) return true;
  if (!explicit) return false;
  const mine = rows.find((row) => row.address === terminal);
  return Boolean(mine) && !mine.parent;
}

/**
 * Push every wake line this session has not seen yet, in the order the driver booked them, and return
 * their texts. The cursor is a position in the file, not its newest text: one `advance()` round can
 * book several wakes, and the driver hands them out oldest first (`take_wake`), so a poll that jumped
 * to the last line would lose the earlier ones for good. A wake already pushed is pushed once, even
 * after a restart; an empty line pushes nothing; and a run this session does not lead is not polled.
 * An explicit `AGENT_RUNTIME_WAKES` narrows which files are read; who may read them does not change,
 * so a worker that inherits the variable still receives nothing.
 */
export function pollWakes(pi, options = {}) {
  const env = options.env || process.env;
  const files = wakeFiles(env);
  if (!files.length) return [];
  const rows = (options.panes || hcPanes)(env);
  const terminal = env.HC_TERMINAL_ID || "";
  const explicit = Boolean(env.AGENT_RUNTIME_WAKES);
  const state = statePath(env);
  const stored = loadSeen(state);
  const session = stored.sessions[sessionId(env)] || {};
  const pushed = [];
  for (const file of files) {
    if (!ownsRun(slugOf(file), rows, terminal, explicit)) continue;
    pushed.push(...pushFile(pi, session, file));
  }
  if (Object.keys(session).length) stored.sessions[sessionId(env)] = session;
  saveSeen(state, stored);
  return pushed;
}

/** One run's unseen lines, in file order, with its cursor advanced past all of them. */
function pushFile(pi, session, file) {
  const lines = readLines(file);
  const cursor = session[file] || { lines: 0, last: "", text: "" };
  const pushed = [];
  for (const line of lines.slice(startOf(lines, cursor))) {
    const text = wakeText(line);
    if (!text) continue;
    const wake = toWake({ line: text, seen: cursor.text });
    if (!wake) continue;
    cursor.text = wake.seen;
    send(pi, wake.content);
    pushed.push(wake.content);
  }
  cursor.lines = lines.length;
  cursor.last = lines.length ? fingerprint(lines[lines.length - 1]) : "";
  session[file] = cursor;
  return pushed;
}

/** Wire the rhythm into one Pi session. Background work starts at `session_start`, never in the factory. */
export function install(pi, options = {}) {
  const env = options.env || process.env;
  const panes = memoizedPanes();
  let timer = null;

  pi.on("turn_end", async (event) => {
    // An errored or aborted response is a hard exit; only a completed turn can be kept open.
    if (event && event.outcome && event.outcome !== "completed") return undefined;
    return turnProposal(stopDecision({ env }));
  });

  pi.on("tool_call", async (event) => toToolCall(event) || undefined);

  pi.on("session_start", async () => {
    pollWakes(pi, { env, panes });
    if (timer) clearInterval(timer);
    timer = setInterval(() => pollWakes(pi, { env, panes }), WAKE_POLL_MS);
    timer.unref?.();
  });

  pi.on("session_shutdown", async () => {
    if (timer) clearInterval(timer);
    timer = null;
  });
}

export default function (pi) {
  install(pi);
}

function stateRoot(env) {
  return env.XDG_STATE_HOME || path.join(env.HOME || os.homedir(), ".local", "state");
}

function realpath(target) {
  try {
    return fs.realpathSync(target);
  } catch {
    return target;
  }
}

function directories(dir) {
  try {
    return fs
      .readdirSync(dir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => path.join(dir, entry.name))
      .sort();
  } catch {
    return []; // no state directory yet: no run has written a wake
  }
}

/** The run a wake file belongs to: its directory is the plan's slug. */
function slugOf(file) {
  return path.basename(path.dirname(file));
}

/** The hc terminals, or none when hc cannot say: without a listing no run is this session's. */
function hcPanes(env) {
  let result;
  try {
    result = spawnSync("hc", ["ls", "--all", "--json"], {
      env,
      encoding: "utf8",
      timeout: HC_TIMEOUT_MS,
    });
  } catch {
    return [];
  }
  if (!result || result.error || result.status !== 0) return [];
  try {
    const data = JSON.parse(result.stdout || "null");
    const rows = data && Array.isArray(data.terminals) ? data.terminals : [];
    return rows.filter(
      (row) => row && typeof row === "object" && typeof row.name === "string",
    );
  } catch {
    return [];
  }
}

/** One reading of the terminals stands for a few polls: a pane appears and dies at a run's edges. */
function memoizedPanes() {
  const cache = { rows: [], at: 0 };
  return (env) => {
    const now = Date.now();
    if (now - cache.at >= PANE_TTL_MS) {
      cache.rows = hcPanes(env);
      cache.at = now;
    }
    return cache.rows;
  };
}

/** The file's lines, without the empty element a final newline leaves behind: a trailing blank line is
 * the file's shape, not a line the cursor should count. */
function readLines(file) {
  let raw;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch {
    return [];
  }
  const lines = raw.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/** A wake line's text, or "" for a line that is empty or was cut short by a crash. */
function wakeText(line) {
  if (!line.trim()) return "";
  try {
    const row = JSON.parse(line);
    return row && typeof row.text === "string" ? row.text : "";
  } catch {
    return "";
  }
}

/** Where to resume reading: the stored position, unless the file shrank or its last line changed. */
function startOf(lines, cursor) {
  if (!cursor.lines || lines.length < cursor.lines) return 0;
  if (cursor.last && fingerprint(lines[cursor.lines - 1]) !== cursor.last) return 0;
  return cursor.lines;
}

function fingerprint(line) {
  return createHash("sha256").update(line).digest("hex");
}

function send(pi, content) {
  pi.sendMessage({ customType: WAKE_TYPE, content, display: true }, { triggerTurn: true });
}

function loadSeen(file) {
  let data = {};
  try {
    data = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    data = {};
  }
  const sessions = {};
  for (const [session, cursors] of Object.entries(objectOf(data.sessions))) {
    sessions[session] = {};
    for (const [run, cursor] of Object.entries(objectOf(cursors))) {
      sessions[session][run] = normalizeCursor(cursor);
    }
  }
  return { version: 2, sessions };
}

function objectOf(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function normalizeCursor(cursor) {
  const source = objectOf(cursor);
  return {
    lines: Number(source.lines) || 0,
    last: typeof source.last === "string" ? source.last : "",
    text: typeof source.text === "string" ? source.text : "",
  };
}

function saveSeen(file, seen) {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    const temp = file + "." + process.pid + ".tmp";
    fs.writeFileSync(temp, JSON.stringify(seen, null, 2) + "\n", { mode: 0o600 });
    fs.renameSync(temp, file);
  } catch {
    // The cursor is a convenience: a state file that cannot be written must not stop the session.
  }
}
