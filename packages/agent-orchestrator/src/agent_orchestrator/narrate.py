"""What the driver tells the person watching its pane. The ledger and events.jsonl are the record; this is the
narration: one short line per thing that happened, in a fixed vocabulary of kinds and steps, so the eye can find
the line that needs someone among the polling. Tone rules for the text live in this package's AGENTS.md."""

import os
import re
import sys
import time

# kind -> (symbol, ANSI colour). `ask` colours the whole line: it is the one a person must act on.
KINDS = {
    "info": ("·", ""),
    "start": ("▸", "36"),
    "done": ("✓", "32"),
    "wait": ("…", "2"),
    "warn": ("!", "33"),
    "ask": ("?", "1;35"),
    "fail": ("✗", "31"),
}
# The step column: what part of a ticket's life (or the plan's) the line is about. Ledger gate names stay in the
# ledger; STEP_OF maps them here.
STEPS = (
    "build",
    "review",
    "fix",
    "decide",
    "ci",
    "merge",
    "cleanup",
    "verify",
    "mutation",
    "archive",
    "gaps",
    "setup",
)
STEP_OF = {
    "reported": "build",
    "reviewing": "review",
    "converged": "review",
    "reviewed": "review",
    "fix-pending": "fix",
    "redo-pending": "fix",
    "requested": "decide",
    "decided": "decide",
    "ready-for-ci": "ci",
    "awaiting-merge": "merge",
    "cleanup": "cleanup",
}
REPEAT_SECONDS = (
    300  # a wait that has not changed is said again this often, so a stuck unit still shows
)
WIDTH = 200  # the text is cut here; the ledger keeps the whole of it
PR_URL = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)")
REPEATED = re.compile(r"(\S+): \1: ")  # "path: path: why", how a contract error quotes its own file
UNIT = re.compile(r"(?i)t\d+")
# The banner a drive opens with, so the pane shows where one run starts and what it runs on.
LOGO = (
    r"   ___  ___  ___ _  _ ___ ___ _____ ___    _ _____ ___",
    r"  / _ \| _ \/ __| || | __/ __|_   _| _ \  /_\_   _| __|",
    r" | (_) |   / (__| __ | _|\__ \ | | |   / / _ \| | | _|",
    r"  \___/|_|_\\___|_||_|___|___/ |_| |_|_\/_/ \_\_| |___|",
)
CLASS_ORDER = ("small", "medium", "large")


def step_of(gate):
    return STEP_OF.get(gate)


class Narrator:
    def __init__(self, stream=None, color=None, clock=time.time, env=None, roots=(), home=None):
        self.stream = (
            stream  # None: sys.stderr when a line is written, so a redirected stderr is honoured
        )
        self.color = color  # None: colour when the stream is a terminal and NO_COLOR is unset
        self.clock = clock
        self.env = os.environ if env is None else env
        self.roots = list(roots)
        self.home = os.path.expanduser("~") if home is None else home
        self.waits = {}  # unit -> {text: (first said, last said)}

    def say(self, kind, step, text, task=None):
        """One line for `task` (a task id such as t5r3, or a ticket id such as T5; None for the plan). A wait the
        unit already said is held back until REPEAT_SECONDS pass; any other line of the unit forgets its waits.
        True when a line was written."""
        if kind not in KINDS:
            raise ValueError("narrate: kind %r is not one of %s" % (kind, ", ".join(KINDS)))
        if step is not None and step not in STEPS:
            raise ValueError("narrate: step %r is not one of %s" % (step, ", ".join(STEPS)))
        unit, attempt = self.unit(task)
        text = self.shorten(text)
        now = self.clock()
        if kind == "wait":
            waits = self.waits.setdefault(unit, {})
            if text in waits:
                first, last = waits[text]
                if now - last < REPEAT_SECONDS:
                    return False
                waits[text] = (first, now)
                text = "%s (still, %dm)" % (text, (now - first) // 60)
            else:
                waits[text] = (now, now)
        else:
            self.waits.pop(unit, None)
        stream = self.stream or sys.stderr
        print(self.line(kind, step, text, unit, attempt, now, stream), file=stream, flush=True)
        return True

    def banner(self, plan, goal, statuses, repo, base, max_workers, gates, routes):
        """The logo and the run's card: the plan, its tickets, where it builds and what each class and step runs on.
        `routes` is the ledger's record of them (`routes.record`)."""
        stream = self.stream or sys.stderr
        color = self.colored(stream)

        def paint(value, ansi):
            return "\x1b[%sm%s\x1b[0m" % (ansi, value) if color else value

        def row(label, value):
            return "  %s%s" % (paint("%-9s" % label, "2"), value)

        counts = {}
        for status in statuses:
            counts[status.lower()] = counts.get(status.lower(), 0) + 1
        tickets = " · ".join(
            [str(len(statuses))] + ["%d %s" % (counts[k], k) for k in sorted(counts)]
        )
        lines = [paint(line, "36") for line in LOGO] + [""]
        lines.append(row("plan", "%s · %s" % (plan, self.shorten(goal))))
        lines.append(row("tickets", tickets))
        lines.append(row("repo", "%s · base %s" % (self.shorten(repo), base)))
        lines.append(row("workers", "max %d" % max_workers))
        if gates:
            lines.append(row("gates", " · ".join(self.shorten(gate) for gate in gates)))
        if routes.get("path"):
            source = "%s (%s)" % (self.shorten(routes["path"]), routes.get("source"))
        else:
            source = "legacy table (deprecated)"
        lines.append(row("routes", source))
        classes = routes.get("classes") or {}
        steps = routes.get("steps") or {}
        order = sorted(classes, key=lambda k: (CLASS_ORDER.index(k) if k in CLASS_ORDER else 99, k))
        entries = [classes[k] for k in order] + [steps[k] for k in sorted(steps)]
        backend_w = max((len(e["backend"]) for e in entries), default=0)
        model_w = max((len(e["model"]) for e in entries), default=0)

        def route(name, entry, default):
            return "    %-8s %-*s · %-*s · %s" % (
                name,
                backend_w,
                entry["backend"],
                model_w,
                entry["model"],
                entry.get("effort") or paint(default, "2"),
            )

        lines += [route(k, classes[k], "effort by matrix") for k in order]
        if steps:
            lines.append("  " + paint("steps", "2"))
            lines += [route(k, steps[k], "effort by step") for k in sorted(steps)]
        print("\n".join(lines) + "\n", file=stream, flush=True)

    def unit(self, task):
        if not task:
            return "plan", None
        found = UNIT.match(task)
        unit = found.group(0).upper() if found else task.upper()
        return unit, (task if task.lower() != unit.lower() else None)

    def shorten(self, text):
        text = " ".join(str(text).split())
        for root in self.roots:
            text = text.replace(str(root).rstrip("/") + "/", "")
        if self.home and self.home != "/":
            text = text.replace(self.home.rstrip("/") + "/", "~/")
        text = REPEATED.sub(r"\1: ", PR_URL.sub(r"#\1", text))
        return text if len(text) <= WIDTH else text[: WIDTH - 1] + "…"

    def colored(self, stream):
        if self.color is not None:
            return self.color
        isatty = getattr(stream, "isatty", None)
        return bool(isatty and isatty()) and "NO_COLOR" not in self.env

    def line(self, kind, step, text, unit, attempt, now, stream):
        symbol, code = KINDS[kind]
        clock = time.strftime("%H:%M:%S", time.localtime(now))
        step = step or ""
        if not self.colored(stream):
            return "%s  %-5s %-9s %s %s%s" % (
                clock,
                unit,
                step,
                symbol,
                text,
                "  " + attempt if attempt else "",
            )

        def paint(value, ansi):
            return "\x1b[%sm%s\x1b[0m" % (ansi, value) if ansi else value

        if kind == "ask":
            plain = "%s  %-5s %-9s %s %s%s" % (
                clock,
                unit,
                step,
                symbol,
                text,
                "  " + attempt if attempt else "",
            )
            return paint(plain, code)
        body = paint(text, code) if kind in ("warn", "fail", "wait") else text
        return "%s  %s %s %s %s%s" % (
            paint(clock, "2"),
            paint("%-5s" % unit, "1"),
            paint("%-9s" % step, "2"),
            paint(symbol, code),
            body,
            "  " + paint(attempt, "2") if attempt else "",
        )


NARRATOR = Narrator()


def say(kind, step, text, task=None):
    return NARRATOR.say(kind, step, text, task=task)
