/**
 * The Pi rhythm extension's translation layer, and nothing else.
 *
 * The extension decides nothing: `agent-runtime-hook` decides whether a turn may end, and the driver
 * writes the wakes. What the adapter owns is the shape of the answer in Pi's dialect — one more
 * provider request at a turn boundary, one push into an idle session, and the refusal of a sleep that
 * would park a turn the driver has to wake. Every function here is pure, so a defect lands on the
 * translation and not on a Pi session.
 */

const SLEEP_LIMIT = 60; // seconds; a sleep this long parks a turn that a wake has to end

const SLEEP_REASON =
  "agent-runtime: %s parks the turn while a wake has to arrive on its own; call wait(all) " +
  "instead, which returns the moment a worker reports or the driver books a wake";

// A word that introduces a command without being the command: `time sleep 90` still sleeps.
const PREFIXES = new Set([
  "time",
  "nohup",
  "exec",
  "command",
  "sudo",
  "env",
  "nice",
  "ionice",
  "then",
  "do",
  "else",
  "!",
  "{",
  "(",
]);

const UNITS = { "": 1, s: 1, m: 60, h: 3600, d: 86400 };

/**
 * The Stop answer as Pi's turn-end proposal: a block becomes one more provider request carrying the
 * hook's reason, and an allowed stop proposes nothing at all.
 */
export function toTurnEnd(answer) {
  if (!answer || answer.block !== true) return null;
  const content = typeof answer.reason === "string" ? answer.reason : "";
  return { continue: true, entries: [{ type: "custom_message", content }] };
}

/**
 * One wake line as a push: the newest line the lead has not seen yet, with the fingerprint that marks
 * it seen. An empty line, and a wake already pushed (after a restart too), proposes nothing.
 */
export function toWake({ line, seen } = {}) {
  const text = typeof line === "string" ? line.trim() : "";
  if (!text || text === (typeof seen === "string" ? seen.trim() : "")) return null;
  return { content: text, seen: text };
}

/**
 * A `bash` call as Pi's tool-call verdict: a command that sleeps a minute or longer is refused, with
 * the reason naming `wait(all)`; anything shorter, and any other tool, is untouched.
 */
export function toToolCall({ toolName, input } = {}) {
  if (String(toolName || "").toLowerCase() !== "bash") return null;
  const command = input && typeof input.command === "string" ? input.command : "";
  const seconds = longestSleep(command);
  if (seconds < SLEEP_LIMIT) return null;
  const waited = Number.isFinite(seconds) ? seconds + " s of sleep" : "an endless sleep";
  return { block: true, reason: SLEEP_REASON.replace("%s", waited) };
}

/** The longest sleep in a shell command, in seconds, read segment by segment (`;`, `&&`, `|`, …). */
export function longestSleep(command) {
  let longest = 0;
  for (const segment of String(command || "").split(/\|\||&&|[;|&\n\r{}()]/)) {
    const words = segment.trim().split(/\s+/).filter(Boolean);
    let index = 0;
    while (index < words.length && (isAssignment(words[index]) || PREFIXES.has(words[index]))) {
      index += 1;
    }
    if (!words[index] || basename(words[index]) !== "sleep") continue;
    let total = 0;
    for (const word of words.slice(index + 1)) total += secondsOf(word);
    longest = Math.max(longest, total);
  }
  return longest;
}

function isAssignment(word) {
  return /^[A-Za-z_][A-Za-z0-9_]*=/.test(word);
}

function basename(word) {
  return word.slice(word.lastIndexOf("/") + 1);
}

/** `sleep`'s own argument grammar: one or more numbers with an optional s/m/h/d suffix, as in
 * `1h30m`; `infinity` never ends. Anything else is not a literal duration (a variable, a pipe), so it
 * is not counted. */
function secondsOf(word) {
  if (/^(?:inf(?:inity)?)$/i.test(word)) return Infinity;
  if (!/^(?:[0-9]+(?:\.[0-9]+)?[smhd]?)+$/.test(word)) return 0;
  let total = 0;
  for (const part of word.matchAll(/([0-9]+(?:\.[0-9]+)?)([smhd]?)/g)) {
    total += Number(part[1]) * UNITS[part[2]];
  }
  return total;
}
