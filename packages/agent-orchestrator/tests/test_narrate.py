"""The driver's pane: one short, coloured line per thing that happened, repeated waits collapsed."""

import io
import unittest
from typing import Any

import pytest
from agent_orchestrator import narrate

pytestmark = pytest.mark.unit

CLOCK = "\\d\\d:\\d\\d:\\d\\d"


class Tty(io.StringIO):
    def isatty(self):
        return True


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class NarrateTests(unittest.TestCase):
    def narrator(self, stream=None, color=False, env=None, **kwargs):
        self.out = stream if stream is not None else io.StringIO()
        self.clock = Clock()
        return narrate.Narrator(
            stream=self.out, color=color, clock=self.clock, env=env or {}, **kwargs
        )

    def lines(self):
        return self.out.getvalue().splitlines()

    def test_a_line_carries_unit_step_symbol_text_and_the_attempt_last(self):
        n = self.narrator()
        n.say(
            "done", "merge", "merged https://github.com/FBisca/agent-runtime/pull/200", task="t1o1"
        )
        n.say("start", "build", "started worker wv-t3", task="t3")
        n.say("ask", None, "needs you: sign the release", task="T4")
        n.say("warn", "setup", "legacy routing config in use")
        self.assertRegex(self.lines()[0], "^%s  T1    merge     ✓ merged #200  t1o1$" % CLOCK)
        self.assertRegex(self.lines()[1], "^%s  T3    build     ▸ started worker wv-t3$" % CLOCK)
        self.assertRegex(
            self.lines()[2], "^%s  T4              \\? needs you: sign the release$" % CLOCK
        )
        self.assertRegex(
            self.lines()[3], "^%s  plan  setup     ! legacy routing config in use$" % CLOCK
        )

    def test_an_unknown_kind_or_step_is_refused(self):
        n = self.narrator()
        with self.assertRaisesRegex(ValueError, "kind 'loud'"):
            n.say("loud", "build", "x", task="t1")
        with self.assertRaisesRegex(ValueError, "step 'converged'"):
            n.say("info", "converged", "x", task="t1")

    def test_colour_only_on_a_terminal_without_NO_COLOR(self):
        n = self.narrator(stream=Tty(), color=None)
        n.say("ask", "decide", "Lead decision needed", task="t5r2")
        n.say("done", "merge", "merged #1", task="t1")
        ask, done = self.lines()
        self.assertTrue(ask.startswith("\x1b[1;35m") and ask.endswith("\x1b[0m"), repr(ask))
        self.assertIn("\x1b[32m✓", done)
        for stream, env in ((io.StringIO(), {}), (Tty(), {"NO_COLOR": "1"})):
            n = self.narrator(stream=stream, color=None, env=env)
            n.say("ask", "decide", "Lead decision needed", task="t5r2")
            self.assertNotIn("\x1b", self.out.getvalue())

    def test_a_repeated_wait_prints_once_then_every_five_minutes_until_the_unit_moves(self):
        n = self.narrator()
        ci, merge = "waiting for CI on #208", "waiting for GitHub to compute whether #208 can merge"
        for at, text, task in (
            (0, ci, "t3r1"),
            (60, ci, "t3r1"),
            (200, merge, "t3r1"),  # another wait of the unit is its own line...
            (230, ci, "t3r1"),  # ...and does not reset the first one
            (240, ci, "t2"),  # another unit waits on its own
            (300, ci, "t3r1"),
            (400, ci, "t3r1"),
        ):
            self.clock.now = at
            n.say("wait", "ci", text, task=task)
        self.clock.now = 410
        n.say("done", "ci", "CI green; #208 ready for review", task="t3r1")
        self.clock.now = 420
        n.say("wait", "merge", ci, task="t3r1")
        texts = [line.split("  ", 1)[1] for line in self.lines()]
        self.assertEqual(
            texts,
            [
                "T3    ci        … waiting for CI on #208  t3r1",
                "T3    ci        … waiting for GitHub to compute whether #208 can merge  t3r1",
                "T2    ci        … waiting for CI on #208",
                "T3    ci        … waiting for CI on #208 (still, 5m)  t3r1",
                "T3    ci        ✓ CI green; #208 ready for review  t3r1",
                "T3    merge     … waiting for CI on #208  t3r1",
            ],
        )

    def test_paths_urls_and_whitespace_are_shortened(self):
        n = self.narrator(roots=["/x/state/run"], home="/Users/me")
        n.say(
            "ask",
            "decide",
            "Lead decision needed (report): cannot read /x/state/run/t5r3/report.json:\n"
            "/x/state/run/t5r3/report.json: report: acceptance_met is required",
            task="t5r3",
        )
        n.say("warn", "setup", "the table of /Users/me/.claude/CLAUDE.md is deprecated")
        n.say("fail", "build", "boom " + "x" * 400, task="t1")
        self.assertTrue(
            self.lines()[0].endswith(
                "? Lead decision needed (report): cannot read t5r3/report.json: report: "
                "acceptance_met is required  t5r3"
            ),
            self.lines()[0],
        )
        self.assertTrue(
            self.lines()[1].endswith("! the table of ~/.claude/CLAUDE.md is deprecated")
        )
        cut = self.lines()[2].split("✗ ", 1)[1]
        self.assertEqual((len(cut), cut[-1]), (narrate.WIDTH, "…"))


class BannerTests(unittest.TestCase):
    """orchestrator-engine/R90: the run's banner, written once when a drive starts."""

    ROUTES: dict[str, Any] = {
        "source": "user",
        "path": "/Users/me/.config/agent-runtime/routes.yaml",
        "classes": {
            "large": {"class": "large", "backend": "claude", "model": "opus", "effort": "xhigh"},
            "medium": {"class": "medium", "backend": "codex", "model": "gpt-m", "effort": "high"},
            "small": {"class": "small", "backend": "claude", "model": "haiku", "effort": None},
        },
        "steps": {
            "review": {"step": "review", "backend": "codex", "model": "gpt-sol", "effort": None},
        },
    }

    def banner(self, stream=None, color=False, **overrides):
        self.out = stream if stream is not None else io.StringIO()
        n = narrate.Narrator(stream=self.out, color=color, env={}, home="/Users/me")
        run = {
            "plan": "demo",
            "goal": "Ship the demo\n  end to end",
            "statuses": ["DONE", "TO-DO", "TO-DO", "IN PROGRESS"],
            "repo": "/Users/me/src/demo",
            "base": "origin/main",
            "max_workers": 3,
            "gates": ["uv run pytest", "ruff check"],
            "routes": self.ROUTES,
        }
        run.update(overrides)
        n.banner(**run)
        return self.out.getvalue().split("\n")

    def test_the_banner_is_the_logo_then_the_run_and_its_routes(self):
        lines = self.banner()
        self.assertEqual(lines[: len(narrate.LOGO)], list(narrate.LOGO))
        self.assertEqual(
            lines[len(narrate.LOGO) :],
            [
                "",
                "  plan     demo · Ship the demo end to end",
                "  tickets  4 · 1 done · 1 in progress · 2 to-do",
                "  repo     ~/src/demo · base origin/main",
                "  workers  max 3",
                "  gates    uv run pytest · ruff check",
                "  routes   ~/.config/agent-runtime/routes.yaml (user)",
                "    small    claude · haiku   · effort by matrix",
                "    medium   codex  · gpt-m   · high",
                "    large    claude · opus    · xhigh",
                "  steps",
                "    review   codex  · gpt-sol · effort by step",
                "",
                "",
            ],
        )

    def test_a_legacy_run_without_gates_or_steps_says_so(self):
        legacy = {
            "source": "legacy",
            "path": None,
            "classes": {"small": dict(self.ROUTES["classes"]["small"])},
            "steps": {},
        }
        lines = self.banner(gates=[], routes=legacy)
        self.assertIn("  routes   legacy table (deprecated)", lines)
        self.assertFalse([line for line in lines if line.lstrip().startswith(("gates", "steps"))])

    def test_the_logo_is_coloured_only_on_a_terminal(self):
        lines = self.banner(stream=Tty(), color=None)
        self.assertTrue(lines[0].startswith("\x1b[36m"), repr(lines[0]))
        self.assertIn("\x1b[2mplan", "\n".join(lines))
        self.assertNotIn("\x1b", "\n".join(self.banner()))


if __name__ == "__main__":
    unittest.main()
