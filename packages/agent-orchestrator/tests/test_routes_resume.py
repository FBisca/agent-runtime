"""A run that resumes keeps the routes its ledger recorded; routing it cannot recover or that conflicts stops it.

S3 — an existing run resumes with recorded routes, including legacy routes: the record wins over a changed or
missing policy, a conflicting argument fails before any spawn without touching the ledger, a missing or incomplete
record needs an explicit --routes-file, and an argument that matches the record is accepted.
"""

import json
import unittest

import pytest
from fixtures import RunFixture  # noqa: E402

pytestmark = pytest.mark.integration

KIND = "agent-orchestrator/ledger"


def entry(klass, backend, model, effort=None):
    return {"class": klass, "backend": backend, "model": model, "effort": effort}


def recorded(source="file", path="/gone/routes.json", cli=None, steps=None, **classes):
    """The `routes` record a ledger keeps: three classes (small is codex/gpt-s@high unless named), the steps."""
    chosen = {
        "small": ("codex", "gpt-s", "high"),
        "medium": ("claude", "sonnet", None),
        "large": ("claude", "opus", None),
    }
    chosen.update(classes)
    record = {
        "source": source,
        "path": path,
        "classes": {k: entry(k, *v) for k, v in chosen.items()},
        "steps": {
            step: {"step": step, "backend": b, "model": m, "effort": e}
            for step, (b, m, e) in (steps or {}).items()
        },
    }
    if cli:
        record["cli"] = cli
    return record


def route(klass, backend, model, **extra):
    return dict({"class": klass, "backend": backend, "model": model}, **extra)


class RouteResumeTests(RunFixture, unittest.TestCase):
    """The driver resumes a run: what its ledger recorded is what its workers run on."""

    def seed(self, record="absent"):
        """The ledger a run that already started leaves: its lead and, unless `absent`, its route record."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "kind": KIND, "tasks": {}, "lead": "orchestrate:demo"}
        if record != "absent":
            data["routes"] = record
        self.ledger_file = self.state_dir / "demo.json"
        self.ledger_file.write_text(json.dumps(data), encoding="utf-8")

    def routes_file(self, *routes, steps=(), name="routes.json"):
        path = self.root / name
        path.write_text(
            json.dumps({"version": 1, "routes": list(routes), "steps": list(steps)}),
            encoding="utf-8",
        )
        return str(path)

    def restarts(self, *args):
        """The same command again. The fake worker never reports, so the restart ends stranded (exit 1): what
        it must not do is stop on its routes, which is exit 2 before any worker."""
        result = self.run_driver(*args, expect=1)
        self.assertNotIn("cannot resume", result.stderr)
        self.assertIn("stranded", result.stderr)

    def worker(self, name="demo-t1"):
        return {call["args"]["name"]: call["args"] for call in self.calls("spawn")}[name]

    def refused(self, *args, mention=()):
        """The run stops with an explanation, before a worker exists, and the ledger is the bytes it was."""
        before = self.ledger_file.read_bytes()
        result = self.run_driver(*args, expect=2)
        self.assertEqual(self.calls("spawn"), [])
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.ledger_file.read_bytes(), before)
        for word in mention:
            self.assertIn(word, result.stderr)
        return result

    def test_S3_a_resume_keeps_the_recorded_routes_over_a_changed_policy(self):
        """orchestrator-engine/R92: S3 — the legacy table now says haiku for small, the ledger says codex."""
        self.seed(recorded())
        self.run_driver()
        worker = self.worker()
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]), ("codex", "gpt-s", "high")
        )

    def test_S3_a_resume_keeps_the_recorded_routes_over_a_changed_route_file(self):
        """orchestrator-engine/R92: S3 — a repository policy that now names other routes does not move the run."""
        self.seed(recorded())
        policy = self.repo / ".orchestrate"
        policy.mkdir()
        (policy / "routes.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "routes": [
                        route("small", "claude", "haiku"),
                        route("medium", "claude", "sonnet"),
                        route("large", "claude", "opus"),
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.run_driver()
        self.assertEqual((self.worker()["cli"], self.worker()["model"]), ("codex", "gpt-s"))

    def test_S3_a_resume_keeps_the_recorded_routes_when_the_policy_is_missing(self):
        """orchestrator-engine/R92: S3 — neither the recorded route file nor the legacy table is there; the record answers."""
        self.models.unlink()
        self.seed(recorded())
        self.run_driver()
        self.assertEqual((self.worker()["cli"], self.worker()["model"]), ("codex", "gpt-s"))

    def test_S3_a_resume_keeps_legacy_recorded_routes_over_a_changed_table(self):
        """orchestrator-engine/R92: S3 — a legacy record is a record: the table's later edit does not reach the run."""
        self.seed(
            recorded(
                source="legacy",
                path=None,
                small=("claude", "old-haiku", None),
                medium=("claude", "old-sonnet", None),
                large=("claude", "old-opus", None),
            )
        )
        self.run_driver()
        worker = self.worker()
        self.assertEqual((worker["cli"], worker["model"]), ("claude", "old-haiku"))

    def test_S3_a_resume_keeps_the_recorded_step_routes(self):
        """orchestrator-engine/R92: S3 — the implementer step's recorded route answers the worker."""
        self.seed(recorded(steps={"implementer": ("codex", "gpt-step", "xhigh")}))
        self.run_driver()
        worker = self.worker()
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]), ("codex", "gpt-step", "xhigh")
        )

    def test_S3_a_resume_leaves_the_recorded_routes_in_the_ledger_as_they_were(self):
        """orchestrator-engine/R92: S3 — the record is not rewritten from a policy the run no longer reads."""
        record = recorded()
        self.seed(record)
        self.run_driver()
        self.assertEqual(self.ledger()["routes"], record)

    def test_S3_a_conflicting_route_argument_fails_before_any_spawn(self):
        """orchestrator-engine/R92: S3 — small is recorded on codex/gpt-s@high; the argument says claude/haiku."""
        self.seed(recorded())
        self.refused("--route", "small=claude/haiku", mention=("--route", "small", "gpt-s"))

    def test_S3_a_conflicting_step_argument_fails_before_any_spawn(self):
        """orchestrator-engine/R92: S3 — the ledger names no reviewer route; the argument would add one."""
        self.seed(recorded())
        self.refused("--step", "reviewer=codex/gpt-l@xhigh", mention=("--step", "reviewer"))

    def test_S3_a_step_argument_that_differs_from_the_recorded_step_fails(self):
        """orchestrator-engine/R92: S3 — a recorded step route is not replaced on resume."""
        self.seed(recorded(steps={"reviewer": ("codex", "gpt-l", "xhigh")}))
        self.refused("--step", "reviewer=codex/gpt-l@high", mention=("--step", "reviewer"))

    def test_S3_a_conflicting_routes_file_fails_before_any_spawn(self):
        """orchestrator-engine/R92: S3 — the file resolves to routes other than the ledger's."""
        self.seed(recorded())
        path = self.routes_file(
            route("small", "claude", "haiku"),
            route("medium", "claude", "sonnet"),
            route("large", "claude", "opus"),
        )
        self.refused("--routes-file", path, mention=("--routes-file", "small"))

    def test_S3_a_conflicting_cli_argument_fails_before_any_spawn(self):
        """orchestrator-engine/R92: S3 — the run was recorded on claude and codex classes; --cli pi names neither."""
        self.seed(recorded(cli="claude"))
        self.refused("--cli", "pi", mention=("--cli", "pi"))

    def test_S3_a_route_argument_that_matches_the_ledger_is_accepted(self):
        """orchestrator-engine/R92: S3 — the same route said again is not a conflict."""
        self.seed(recorded())
        self.run_driver("--route", "small=codex/gpt-s@high")
        self.assertEqual((self.worker()["cli"], self.worker()["model"]), ("codex", "gpt-s"))

    def test_S3_a_routes_file_that_resolves_to_the_recorded_routes_is_accepted(self):
        """orchestrator-engine/R92: S3 — the file is not the one recorded, but it says the same thing."""
        self.seed(recorded())
        path = self.routes_file(
            route("small", "codex", "gpt-s", effort="high"),
            route("medium", "claude", "sonnet"),
            route("large", "claude", "opus"),
        )
        self.run_driver("--routes-file", path)
        self.assertEqual((self.worker()["cli"], self.worker()["model"]), ("codex", "gpt-s"))

    def test_S3_a_cli_argument_that_matches_the_ledger_is_accepted(self):
        """orchestrator-engine/R92: S3 — --cli says the backend the run was recorded on."""
        self.seed(recorded(cli="claude"))
        self.run_driver("--cli", "claude")
        self.assertEqual(self.worker()["model"], "gpt-s")

    def test_S3_a_missing_route_record_needs_a_routes_file(self):
        """orchestrator-engine/R92: S3 — a started run with no record cannot be recovered from a policy alone."""
        self.seed()
        self.refused(mention=("--routes-file", "demo"))

    def test_S3_an_incomplete_route_record_needs_a_routes_file(self):
        """orchestrator-engine/R92: S3 — a record that lacks a class is not a record."""
        record = recorded()
        del record["classes"]["large"]
        self.seed(record)
        self.refused(mention=("--routes-file", "large"))

    def test_S3_a_route_record_that_lacks_the_steps_is_incomplete(self):
        """orchestrator-engine/R92: S3 — a record that never said what its steps run on cannot pin them."""
        record = recorded()
        del record["steps"]
        self.seed(record)
        self.refused(mention=("--routes-file",))

    def test_S3_a_routes_file_migrates_a_missing_record_and_records_the_complete_routes(self):
        """orchestrator-engine/R92: S3 — the file answers the run, and the ledger then holds every class and step."""
        self.seed()
        path = self.routes_file(
            route("small", "codex", "gpt-s", effort="high"),
            route("medium", "claude", "sonnet"),
            route("large", "claude", "opus", effort="max"),
            steps=[{"step": "gaps", "backend": "claude", "model": "opus", "effort": "high"}],
        )
        self.run_driver("--routes-file", path)
        self.assertEqual((self.worker()["cli"], self.worker()["model"]), ("codex", "gpt-s"))
        record = self.ledger()["routes"]
        self.assertEqual(sorted(record["classes"]), ["large", "medium", "small"])
        self.assertEqual(record["classes"]["large"]["effort"], "max")
        self.assertEqual(record["steps"]["gaps"]["model"], "opus")
        self.assertEqual(record["path"], path)

    def test_S3_a_routes_file_completes_an_incomplete_record(self):
        """orchestrator-engine/R92: S3 — migrating replaces the partial record with the complete resolved routes."""
        record = recorded()
        del record["classes"]["medium"]
        self.seed(record)
        path = self.routes_file(
            route("small", "claude", "haiku"),
            route("medium", "claude", "sonnet"),
            route("large", "claude", "opus"),
        )
        self.run_driver("--routes-file", path)
        self.assertEqual(self.worker()["model"], "haiku")
        self.assertEqual(sorted(self.ledger()["routes"]["classes"]), ["large", "medium", "small"])

    def test_S3_an_invalid_routes_file_does_not_migrate_a_missing_record(self):
        """orchestrator-engine/R92: S3 — the migration file is validated before the ledger is written."""
        self.seed()
        path = self.routes_file(route("small", "gemini", "x"))
        self.refused("--routes-file", path, mention=("gemini",))

    def test_S3_a_legacy_run_with_a_brain_backend_restarts_with_the_same_command(self):
        """orchestrator-engine/R92: S3 — the table answered the brain's models, so the record carries them."""
        self.run_driver("--brain-cli", "codex")
        record = self.ledger()["routes"]
        self.assertEqual(record["brain_cli"], "codex")
        self.assertEqual(
            {name: (step["backend"], step["model"]) for name, step in record["steps"].items()},
            {"reviewer": ("codex", "gpt-m"), "gaps": ("codex", "gpt-m")},
        )
        self.restarts("--brain-cli", "codex")
        self.assertEqual(self.ledger()["routes"], record)

    def test_S3_a_legacy_run_with_a_brain_backend_restarts_without_the_flag(self):
        """orchestrator-engine/R92: S3 — an omitted --brain-cli is not a conflict; the record answers."""
        self.run_driver("--brain-cli", "codex")
        self.restarts()

    def test_S3_a_record_that_cannot_route_its_gates_takes_the_steps_the_argument_adds(self):
        """orchestrator-engine/R92: S3 — the refusal's way out: --step names the gates, the record keeps them."""
        record = recorded(cli="claude", medium=("claude", "sonnet", None))
        record["brain_cli"] = "codex"
        self.seed(record)
        self.refused("--brain-cli", "codex", mention=("--step", "reviewer"))
        self.run_driver(
            "--step",
            "reviewer=codex/gpt-m",
            "--step",
            "gaps=codex/gpt-m@high",
            "--brain-cli",
            "codex",
        )
        steps = self.ledger()["routes"]["steps"]
        self.assertEqual(steps["reviewer"]["model"], "gpt-m")
        self.assertEqual(steps["gaps"]["effort"], "high")

    def test_S3_a_step_argument_may_not_replace_a_step_while_adding_the_missing_ones(self):
        """orchestrator-engine/R92: S3 — only a step the record lacks is added; a recorded one still conflicts."""
        record = recorded(cli="claude", steps={"reviewer": ("codex", "gpt-l", "xhigh")})
        record["brain_cli"] = "codex"
        self.seed(record)
        self.refused(
            "--step", "reviewer=codex/gpt-m", "--step", "gaps=codex/gpt-m", mention=("reviewer",)
        )


if __name__ == "__main__":
    unittest.main()
