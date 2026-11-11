// The mod's only contact with the orchestrator: it runs the read-only `agent-orchestrator workbench` commands and
// keeps the answers. Nothing here imports Herdr (`exec` is handed in), so `node --test` drives it with a fake.
//
// What it guarantees: a fixed argv per command (never a shell, the plan path is one argument), the space's folder as
// the working directory, at most one fetch in flight, and an answer older than the latest request is dropped. It
// writes nothing: the only verbs it knows are `workbench plans` and `workbench plan`.
import { readPlans, readPlan, planRows, pickPlan } from "./model.js";

export const COMMAND = "agent-orchestrator";
// The watcher is the fast path; the timer is the net for a change it did not see (a remote space, a watch that ended).
export const FALLBACK_MS = 30000;
// Longer than a plan takes to load (it runs git), shorter than the fallback: a hung command shows as a timeout.
export const TIMEOUT_MS = 20000;
// How often changes the watcher saw are turned into one fetch: a running driver writes its ledger many times a second.
export const FLUSH_MS = 2000;

export const plansArgv = () => ["workbench", "plans", "--json"];
export const planArgv = (path) => ["workbench", "plan", path, "--json"];

export function createData({ exec }) {
  let scope = null;
  let epoch = 0; // bumped by anything that makes a running fetch's answer the wrong one to show
  let selected = null;
  let plans = { status: "loading" };
  let answers = new Map(); // plan path -> {status, value | error}: one plan's error never hides another plan
  let running = null;
  let queued = false;
  let dirty = false;

  async function call(argv) {
    try {
      return await exec(COMMAND, argv, { cwd: scope.folder || undefined, on: scope.machine ?? null, timeout: TIMEOUT_MS });
    } catch (error) {
      return { thrown: String(error?.message ?? error) };
    }
  }

  async function cycle(notify) {
    do {
      queued = false;
      if (!scope) return;
      const mine = epoch;
      const listed = readPlans(await call(plansArgv()));
      if (mine !== epoch) continue; // asked for something else meanwhile: this answer is old
      plans = listed.ok ? { status: "ok", value: listed.value } : { status: "error", error: listed.error };
      if (listed.ok) selected = pickPlan(planRows(listed.value.plans), selected);
      notify();
      if (!listed.ok || !selected) continue;
      const path = selected;
      const loaded = readPlan(await call(planArgv(path)));
      if (mine !== epoch) continue;
      answers.set(path, loaded.ok ? { status: "ok", value: loaded.value } : { status: "error", error: loaded.error });
      notify();
    } while (queued);
  }

  return {
    /** Point the mod at a space's folder (and machine). A different place invalidates everything held and in flight. */
    setScope(next) {
      if (scope && scope.folder === next.folder && (scope.machine ?? null) === (next.machine ?? null)) return false;
      scope = { folder: next.folder, machine: next.machine ?? null };
      epoch += 1;
      selected = null;
      plans = { status: "loading" };
      answers = new Map();
      return true;
    },

    /** The person picked a plan: whatever is in flight for the previous one is old now. */
    select(path) {
      if (selected === path) return;
      selected = path;
      epoch += 1;
      queued = true;
    },

    /** Fetch now, unless a fetch is in flight: then ask for one more run after it, however many times this is called.
     *  `notify` is called as answers are applied; it is only used by the call that starts the run. */
    refresh(notify = () => {}) {
      if (running) {
        queued = true;
        return running;
      }
      running = cycle(notify).finally(() => {
        running = null;
      });
      return running;
    },

    /** Something changed (the watcher saw it): remembered, fetched at the next `flush`. */
    touch() {
      dirty = true;
    },

    flush(notify) {
      if (!dirty) return Promise.resolve();
      dirty = false;
      return this.refresh(notify);
    },

    state() {
      const shown = plans.status === "ok" && selected ? answers.get(selected) ?? { status: "loading" } : null;
      return { scope, plans, selected, plan: shown };
    },
  };
}

/** Calls `onChange` each time `watch(dir)` reports a change, until the watch ends (it rejects). */
export async function watchLoop(watch, dir, onChange) {
  try {
    for (;;) {
      await watch(dir);
      onChange();
    }
  } catch {
    // The watch ended (the folder is gone, or the tree is too big to watch): the fallback timer carries on.
  }
}
