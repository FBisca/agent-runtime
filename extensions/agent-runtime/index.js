/**
 * The Pi rhythm extension: Pi has no Stop or PreToolUse hook of its own, so this adapter is where the
 * runtime's rhythm reaches a Pi session.
 *
 * It asks `agent-runtime-hook` for the Stop decision (the same entry Claude Code and Codex run) and
 * turns a block into one more provider request; it watches the driver's `wakes.jsonl` and pushes a
 * wake into an idle session instead of the lead sleeping for it; and it refuses a shell command that
 * sleeps a minute or longer, naming `wait(all)` as the way to wait. Every decision is the runtime's:
 * the hook decides the stop, the driver writes the wakes, and the server refuses a mutating verb while
 * the lead has undelivered reports. A Pi session without this extension keeps its own scheduling.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { toToolCall, toTurnEnd, toWake } from "./handlers.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WAKE_POLL_MS = 2000; // how often wakes.jsonl is read while the session sits idle
const HOOK_TIMEOUT_MS = 5000;
const STOP_PAYLOAD = { hook_event_name: "Stop", stop_hook_active: false };
const NUDGE_TYPE = "agent-runtime-stop"; // the guard's reason, as a custom message
const WAKE_TYPE = "agent-runtime-wake"; // one driver wake, as a custom message

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

/** Where this extension remembers the last wake it pushed for each run. */
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
 * Push every wake the lead has not seen yet and return their texts. One wake per file is pushed — the
 * newest — because the driver books one per escalation and a fresh wake supersedes the older ones; a
 * wake already pushed, even before a restart, is pushed once, and an empty line pushes nothing.
 */
export function pollWakes(pi, options = {}) {
  const env = options.env || process.env;
  const state = statePath(env);
  const seen = loadSeen(state);
  const pushed = [];
  for (const file of wakeFiles(env)) {
    const wake = toWake({ line: lastWake(file), seen: seen[file] });
    if (!wake) continue;
    seen[file] = wake.seen;
    pi.sendMessage(
      { customType: WAKE_TYPE, content: wake.content, display: true },
      { triggerTurn: true },
    );
    pushed.push(wake.content);
  }
  saveSeen(state, seen);
  return pushed;
}

/** Wire the rhythm into one Pi session. Background work starts at `session_start`, never in the factory. */
export function install(pi, options = {}) {
  const env = options.env || process.env;
  let timer = null;

  pi.on("turn_end", async (event) => {
    // An errored or aborted response is a hard exit; only a completed turn can be kept open.
    if (event && event.outcome && event.outcome !== "completed") return undefined;
    return turnProposal(stopDecision({ env }));
  });

  pi.on("tool_call", async (event) => toToolCall(event) || undefined);

  pi.on("session_start", async () => {
    pollWakes(pi, { env });
    if (timer) clearInterval(timer);
    timer = setInterval(() => pollWakes(pi, { env }), WAKE_POLL_MS);
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

/** The newest wake text in one file; a line a crash cut short is skipped. */
function lastWake(file) {
  let raw;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch {
    return "";
  }
  let text = "";
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      if (row && typeof row.text === "string" && row.text.trim()) text = row.text;
    } catch {
      // the one line a crash cut short
    }
  }
  return text;
}

function loadSeen(file) {
  try {
    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    return data && typeof data === "object" && !Array.isArray(data) ? data : {};
  } catch {
    return {};
  }
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
