"""The workbench's read side: `workbench plans` prints what the plan loader and the ledger say, as one JSON
object, and writes nothing.

The plan listing, the cross-cutting read-only checks and the fixture every workbench test module imports stay
here; the `workbench plan` view of one plan — its layers, the loader's refusals, the approval drift and the
tickets' attempts — lives beside this module, in test_workbench_plan.py."""

import copy
import hashlib
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
import yaml
from fixtures import RunFixture, load_script
from test_plan import contract

pytestmark = pytest.mark.integration

SECRET = "hunter2-workbench-secret"
BODY = "REPORT-BODY-MARKER the worker wrote this"


T0 = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)


def at(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat()


def dead_pid():
    """A pid no process holds, so `kill(pid, 0)` raises ProcessLookupError."""
    for pid in range(99999, 1000, -1):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            continue
    raise AssertionError("no free pid found")


def state_event(minutes, unit, task=None):
    return {
        "at": at(minutes),
        "kind": "state",
        "unit": unit,
        "task": task or unit,
        "gate": [None, None],
        "status": ["ACTIVE", "ACTIVE"],
    }


def graph(*edges):
    """A plan/1 contract whose tickets T1..Tn carry the given blocked_by lists, in plan order."""
    data = contract()
    data["slug"] = "bench"
    base = data["tickets"][0]
    data["tickets"] = []
    for index, blockers in enumerate(edges, 1):
        ticket = copy.deepcopy(base)
        ticket.update(id="T%d" % index, delivers="delivers %d" % index)
        if blockers:
            ticket["blocked_by"] = list(blockers)
        data["tickets"].append(ticket)
    return data


def tree_digest(*roots):
    """Every path under the roots with its size and content hash: two equal digests mean nothing was written."""
    seen = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames + dirnames):
                path = os.path.join(dirpath, name)
                if os.path.isfile(path) and not os.path.islink(path):
                    with open(path, "rb") as stream:
                        seen[path] = hashlib.sha256(stream.read()).hexdigest()
                else:
                    seen[path] = "dir" if os.path.isdir(path) else "link"
    return seen


class WorkbenchFixture(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.env_patch = mock.patch.dict(
            os.environ,
            {
                "XDG_STATE_HOME": str(self.root / "xdg"),
                "HC_TERMINAL_ID": "lead-test",
                "WORKBENCH_SECRET": SECRET,
            },
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.resolve_patch = mock.patch.object(
            load_script(), "resolve_runtime", return_value={"role": "lead"}
        )
        self.resolve_patch.start()
        self.addCleanup(self.resolve_patch.stop)
        # The fixture's Markdown plan is removed: each test lays out its own plans.
        self.plan.unlink()
        self.plans = self.repo / "docs" / "plans"

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(list(argv))
        return code, out.getvalue(), err.getvalue()

    def write_plan(self, data, name=None, approve=True):
        path = self.plans / (name or "2026-11-11-%s.plan.yaml" % data["slug"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# kept\n" + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        # Discovery lists the repository's tracked plan files.
        self.git(self.repo, "add", str(path))
        if approve:
            code, out, err = self.run_cli(
                "plan",
                "approve",
                str(path),
                "--repo",
                str(self.repo),
                "--state-dir",
                str(self.state_dir),
            )
            self.assertEqual(code, 0, out + err)
        return path

    def edit(self, path, change):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text("# kept\n" + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def write_ledger(self, slug, statuses, plan_state=None, tasks=None):
        key = load_script().repo_key(self.repo)
        path = self.state_dir / key / (slug + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "tasks": tasks or {}, "ticket_status": statuses}
        if plan_state:
            data["plan"] = {"state": plan_state, "history": [{"state": plan_state, "at": "t"}]}
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def workbench(self, verb, *extra, repo=True):
        argv = ["workbench", verb, *extra, "--json", "--state-dir", str(self.state_dir)]
        if repo:
            argv += ["--repo", str(self.repo)]
        return self.run_cli(*argv)

    def view(self, path):
        code, out, err = self.workbench("plan", str(path))
        self.assertEqual(code, 0, out + err)
        return json.loads(out)

    def approved(self, slug, *edges):
        data = graph(*(edges or ([],)))
        data["slug"] = slug
        return self.write_plan(data)

    def draft(self, slug):
        data = graph([])
        data["slug"] = slug
        return self.write_plan(data, approve=False)

    def broken(self, slug):
        """A plan that does not load: a ticket blocked by one that does not exist."""
        data = graph([], ["T1"])
        data["slug"] = slug
        data["tickets"][1]["blocked_by"] = ["T9"]
        return self.write_plan(data, approve=False)

    def markdown(self, name, slug):
        path = self.plans / name
        path.write_text(
            "# Plan: %s\n\nSlug: `%s`. Status: APPROVED 2026-11-01.\n\n## Tickets\n\n"
            "| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| T1 | first | S1 | none | src/a/ | small | simple | agent | TO-DO |\n" % (slug, slug),
            encoding="utf-8",
        )
        self.git(self.repo, "add", str(path))
        return path

    def log_events(self, slug, rows):
        run = self.state_dir / load_script().repo_key(self.repo) / slug
        run.mkdir(parents=True, exist_ok=True)
        with open(run / "events.jsonl", "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        return run

    def listing(self):
        code, out, err = self.workbench("plans")
        self.assertEqual(code, 0, out + err)
        return json.loads(out)

    def by_slug(self, shown):
        return {entry["slug"]: entry for entry in shown["plans"]}


class PlansTests(WorkbenchFixture):
    def test_S2_one_json_object_lists_every_plan_with_its_state_and_ticket_counts(self):
        approved = self.write_plan(graph([], ["T1"], ["T1"]))
        draft_data = graph([], [])
        draft_data["slug"] = "sketch"
        draft = self.write_plan(draft_data, approve=False)
        self.write_ledger(
            "bench",
            {"T1": "COMPLETE 2026-11-11", "T2": "IN PROGRESS · round 2", "T3": "TO-DO"},
            plan_state="RUNNING",
        )
        legacy = self.plans / "2026-11-01-old.plan.md"
        legacy.write_text(
            "# Plan: old\n\nSlug: `old`. Status: APPROVED 2026-11-01.\n\n## Tickets\n\n"
            "| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| T1 | first | S1 | none | src/a/ | small | simple | agent | COMPLETE |\n"
            "| T2 | second | S1 | T1 | src/b/ | small | simple | agent | TO-DO |\n",
            encoding="utf-8",
        )
        code, out, err = self.workbench("plans")
        self.assertEqual(code, 0, err)
        shown = json.loads(out)
        self.assertEqual(shown["schema_version"], 1)
        by_slug = {entry["slug"]: entry for entry in shown["plans"]}
        self.assertEqual(set(by_slug), {"bench", "sketch", "old"})
        bench = by_slug["bench"]
        self.assertEqual(
            (bench["format"], bench["path"], bench["state"], bench["legacy"]),
            ("yaml", str(approved.resolve()), "RUNNING", False),
        )
        self.assertEqual(
            bench["tickets"],
            {"total": 3, "by_state": {"COMPLETE": 1, "IN PROGRESS": 1, "TO-DO": 1}},
        )
        sketch = by_slug["sketch"]
        self.assertEqual(
            (sketch["state"], sketch["path"], sketch["tickets"]),
            ("DRAFT", str(draft.resolve()), {"total": 2, "by_state": {"TO-DO": 2}}),
        )
        old = by_slug["old"]
        self.assertEqual((old["format"], old["legacy"]), ("markdown", True))
        self.assertEqual(old["tickets"], {"total": 2, "by_state": {"COMPLETE": 1, "TO-DO": 1}})
        self.assertNotIn("graph", old)
        self.assertNotIn("layers", old)

    def test_S2_an_approved_plan_without_a_ledger_is_approved(self):
        self.write_plan(graph([]))
        shown = json.loads(self.workbench("plans")[1])
        self.assertEqual(shown["plans"][0]["state"], "APPROVED")

    def test_S2_a_path_names_the_plan_directory_without_a_repo_flag(self):
        path = self.write_plan(graph([]))
        code, out, err = self.workbench("plans", str(path), repo=False)
        self.assertEqual(code, 0, err)
        self.assertEqual([p["slug"] for p in json.loads(out)["plans"]], ["bench"])

    def test_S11_a_plan_that_does_not_load_is_an_error_entry_with_null_counts_not_zero(self):
        self.write_plan(graph([]))
        broken = graph([], ["T1"])
        broken["slug"] = "broken"
        broken["tickets"][1]["blocked_by"] = ["T9"]
        self.write_plan(broken, approve=False)
        code, out, err = self.workbench("plans")
        self.assertEqual(code, 0, err)
        by_slug = {entry["slug"]: entry for entry in json.loads(out)["plans"]}
        self.assertEqual(by_slug["bench"]["tickets"]["total"], 1)
        bad = by_slug["broken"]
        self.assertIsNone(bad["tickets"])
        self.assertIsNone(bad["state"])
        self.assertEqual(bad["error"]["kind"], "invalid_plan")
        self.assertIn("T9", bad["error"]["message"])

    def error(self, *argv, **kwargs):
        code, out, err = self.workbench(*argv, **kwargs)
        self.assertEqual(code, 2, out + err)
        self.assertEqual(out, "", "a refusal prints no view on stdout")
        shown = json.loads(err)
        self.assertEqual(shown["schema_version"], 1)
        self.assertNotIn("plans", shown)
        self.assertNotIn("tickets", shown)
        return shown["error"]

    def test_S3_outside_a_repository_is_not_a_repo_with_the_way_out(self):
        elsewhere = self.root / "not-a-repo"
        elsewhere.mkdir()
        error = self.run_cli(
            "workbench", "plans", "--json", "--repo", str(elsewhere), "--state-dir", "x"
        )
        self.assertEqual(error[1], "")
        shown = json.loads(error[2])["error"]
        self.assertEqual(error[0], 2)
        self.assertEqual(shown["kind"], "not_a_repo")
        self.assertIn("--repo", shown["way_out"])
        cwd = os.getcwd()
        os.chdir(elsewhere)
        self.addCleanup(os.chdir, cwd)
        code, out, err = self.run_cli("workbench", "plans", "--json", "--state-dir", "x")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(json.loads(err)["error"]["kind"], "not_a_repo")

    def test_S3_a_repository_without_plans_is_no_plans_never_an_empty_list(self):
        error = self.error("plans")
        self.assertEqual(error["kind"], "no_plans")
        self.assertIn("/start-planning", error["way_out"])
        self.assertIn("path", error["way_out"])

    def test_S3_several_plan_directories_are_ambiguous_and_all_named(self):
        self.write_plan(graph([]))
        other = graph([])
        other["slug"] = "other"
        (self.repo / ".plans").mkdir()
        path = self.repo / ".plans" / "2026-11-11-other.plan.yaml"
        path.write_text(yaml.safe_dump(other), encoding="utf-8")
        self.git(self.repo, "add", "docs/plans", ".plans")
        error = self.error("plans")
        self.assertEqual(error["kind"], "ambiguous")
        self.assertEqual(sorted(error["directories"]), [".plans", "docs/plans"])
        self.assertIn("docs/plans", error["message"])
        self.assertIn(".plans", error["message"])
        self.assertIn("path", error["way_out"])
        self.assertIn("--repo", error["way_out"])

    def test_S3_several_plan_directories_with_a_repo_config_naming_one_list_that_one(self):
        self.write_plan(graph([]))
        fixture = self.repo / "tests" / "data" / "reference.plan.yaml"
        fixture.parent.mkdir(parents=True)
        fixture.write_text(yaml.safe_dump(graph([])), encoding="utf-8")
        config = self.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir()
        config.write_text("plans:\n  dir: docs/plans\n", encoding="utf-8")
        self.git(self.repo, "add", "tests/data", ".orchestrate")
        code, out, err = self.workbench("plans")
        self.assertEqual(code, 0, err)
        listed = [entry["path"] for entry in json.loads(out)["plans"]]
        self.assertTrue(listed, out)
        self.assertTrue(all("/docs/plans/" in path for path in listed), listed)

    def test_S3_a_plan_that_does_not_exist_is_a_named_error(self):
        error = self.error("plan", str(self.plans / "nope.plan.yaml"))
        self.assertEqual(error["kind"], "no_such_plan")

    LEGACY_TICKETS = (
        "## Tickets\n\n"
        "| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| T1 | first | S1 | none | src/a/ | small | simple | agent | COMPLETE |\n"
        "| T2 | second | S1 | T1 | src/b/ | small | simple | agent | COMPLETE |\n"
        "| T3 | third | S1 | T2 | src/c/ | small | simple | agent | TO-DO |\n"
    )

    def legacy_listing(self, name, text):
        self.plans.mkdir(parents=True, exist_ok=True)
        (self.plans / name).write_text(text, encoding="utf-8")
        self.git(self.repo, "add", str(self.plans / name))
        code, out, err = self.workbench("plans")
        self.assertEqual(code, 0, err)
        return {entry["path"]: entry for entry in json.loads(out)["plans"]}[str(self.plans / name)]

    def test_S2_a_legacy_ledger_without_ticket_status_keeps_the_plan_files_statuses(self):
        ledger = self.state_dir / "old.json"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(json.dumps({"version": 1, "tasks": {}}), encoding="utf-8")
        entry = self.legacy_listing(
            "2026-11-01-old.plan.md",
            "# Plan: old\n\nSlug: `old`. Status: APPROVED 2026-11-01.\n\n" + self.LEGACY_TICKETS,
        )
        self.assertEqual(entry["tickets"], {"total": 3, "by_state": {"COMPLETE": 2, "TO-DO": 1}})

    def test_S2_a_second_table_with_id_and_status_columns_yields_no_tickets(self):
        entry = self.legacy_listing(
            "2026-11-02-twotables.plan.md",
            "# Plan: two\n\nSlug: `two`. Status: APPROVED 2026-11-02.\n\n"
            + self.LEGACY_TICKETS
            + "\n## Risks\n\n| # | a | b | c | d | e | f | g | Status |\n|---|---|---|---|---|---|---|---|---|\n"
            "| R1 | slow | x | x | x | x | x | x | open |\n",
        )
        self.assertEqual(entry["tickets"], {"total": 3, "by_state": {"COMPLETE": 2, "TO-DO": 1}})

    def test_S2_a_legacy_plan_without_a_slug_line_lists_under_its_file_name(self):
        entry = self.legacy_listing(
            "2026-11-03-noslug.plan.md",
            "# Plan: none\n\nStatus: APPROVED 2026-11-03.\n\n" + self.LEGACY_TICKETS,
        )
        self.assertEqual(entry["slug"], "2026-11-03-noslug")
        self.assertIsNone(entry.get("error"))
        self.assertEqual(entry["tickets"]["total"], 3)


class PlanListTests(WorkbenchFixture):
    def test_S25_a_markdown_twin_of_a_yaml_plan_is_not_listed_and_a_lone_markdown_plan_is_legacy(
        self,
    ):
        yaml_path = self.approved("bench")
        twin = self.markdown(yaml_path.name.replace(".plan.yaml", ".plan.md"), "bench")
        self.markdown("2026-11-01-old.plan.md", "old")
        shown = self.listing()
        entries = self.by_slug(shown)
        self.assertEqual(len(shown["plans"]), 2, shown["plans"])
        self.assertEqual(sorted(entries), ["bench", "old"])
        self.assertNotIn(str(twin.resolve()), [entry["path"] for entry in shown["plans"]])
        self.assertEqual((entries["bench"]["format"], entries["bench"]["legacy"]), ("yaml", False))
        self.assertEqual((entries["old"]["format"], entries["old"]["legacy"]), ("markdown", True))

    def test_S26_each_plan_is_grouped_active_or_older(self):
        self.approved("live")
        self.write_ledger("live", {"T1": "IN PROGRESS"}, plan_state="RUNNING")
        for slug, plan_state in (
            ("delivered", "DELIVERED"),
            ("verifying", "VERIFYING"),
            ("done", "ARCHIVED"),
            ("accepted", "ACCEPTED"),
            ("refused", "REJECTED"),
        ):
            self.approved(slug)
            self.write_ledger(slug, {"T1": "COMPLETE"}, plan_state=plan_state)
        self.approved("ready")
        self.draft("sketch")
        self.broken("gone")
        self.write_ledger("gone", {}, plan_state="ARCHIVED")
        self.broken("authoring")
        self.broken("stuck")
        self.write_ledger("stuck", {}, plan_state="RUNNING")
        self.markdown("2026-11-01-old.plan.md", "old")
        groups = {slug: entry["group"] for slug, entry in self.by_slug(self.listing()).items()}
        self.assertEqual(
            groups,
            {
                "live": "active",
                "delivered": "active",
                "verifying": "active",
                "ready": "active",
                "sketch": "active",
                "authoring": "active",
                "stuck": "active",
                "done": "older",
                "accepted": "older",
                "refused": "older",
                "gone": "older",
                "old": "older",
            },
        )

    def test_S27_plans_carry_their_last_activity_and_are_ordered_active_first_newest_first(self):
        for slug, minutes in (("early", 5), ("late", 50)):
            self.approved(slug)
            self.write_ledger(slug, {"T1": "IN PROGRESS"}, plan_state="RUNNING")
            self.log_events(
                slug,
                [{"at": at(0), "kind": "start", "pid": os.getpid()}, state_event(minutes, "t1")],
            )
        self.approved("done")
        self.write_ledger("done", {"T1": "COMPLETE"}, plan_state="ARCHIVED")
        self.log_events("done", [{"at": at(500), "kind": "finish"}])
        shown = self.listing()
        entries = self.by_slug(shown)
        self.assertEqual(
            datetime.fromisoformat(entries["late"]["last_activity"]), T0 + timedelta(minutes=50)
        )
        self.assertEqual(
            datetime.fromisoformat(entries["early"]["last_activity"]), T0 + timedelta(minutes=5)
        )
        self.assertEqual([entry["slug"] for entry in shown["plans"]], ["late", "early", "done"])

    def test_S27_without_events_the_plan_files_mtime_is_the_last_activity(self):
        path = self.approved("quiet")
        stamp = (T0 + timedelta(days=3)).timestamp()
        os.utime(path, (stamp, stamp))
        shown = self.listing()
        self.assertEqual(
            datetime.fromisoformat(self.by_slug(shown)["quiet"]["last_activity"]),
            T0 + timedelta(days=3),
        )

    def test_S27_a_torn_last_line_does_not_hide_the_newest_event(self):
        self.approved("torn")
        run = self.log_events("torn", [{"at": at(0), "kind": "start", "pid": os.getpid()}])
        with open(run / "events.jsonl", "a", encoding="utf-8") as stream:
            stream.write(json.dumps(state_event(7, "t1")) + '\n{"at": "2026-11-1')
        entry = self.by_slug(self.listing())["torn"]
        self.assertEqual(datetime.fromisoformat(entry["last_activity"]), T0 + timedelta(minutes=7))

    def test_S27_a_plan_with_no_event_and_no_file_has_null_last_activity_and_sorts_last(self):
        from agent_orchestrator import workbench_needs

        missing = self.plans / "gone.plan.yaml"
        self.assertIsNone(workbench_needs.last_activity(self.plans / "events.jsonl", missing))
        rows = [
            {"slug": "none", "group": "active", "last_activity": None},
            {"slug": "old", "group": "older", "last_activity": at(900)},
            {"slug": "early", "group": "active", "last_activity": at(1)},
            {"slug": "late", "group": "active", "last_activity": at(2)},
        ]
        rows.sort(key=workbench_needs.order_key)
        self.assertEqual([row["slug"] for row in rows], ["late", "early", "none", "old"])


class ReadOnlyTests(WorkbenchFixture):
    def test_S11_no_workbench_command_writes_under_the_repository_or_the_state_directory(self):
        path = self.write_plan(graph([], ["T1"]))
        # A ledger under the old `<state>/<slug>.json` key is what a run would import and a lock would guard.
        old = self.state_dir / "bench.json"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(
            json.dumps(
                {
                    "version": 1,
                    "tasks": {
                        "t1": {"packet": {"plan": str(path), "unit": "t1"}, "status": "COMPLETE"}
                    },
                    "ticket_status": {"T1": "COMPLETE"},
                }
            ),
            encoding="utf-8",
        )
        before = tree_digest(self.repo, self.state_dir, self.root / "xdg", self.root / "home")
        for argv in (("plans",), ("plan", str(path))):
            code, out, err = self.workbench(*argv)
            self.assertEqual(code, 0, err)
        self.assertEqual(
            before, tree_digest(self.repo, self.state_dir, self.root / "xdg", self.root / "home")
        )
        self.assertTrue(old.is_file())
        self.assertFalse(
            any(
                name.endswith(".lock") or name.startswith(".import")
                for _, _, files in os.walk(self.state_dir)
                for name in files
            )
        )

    def test_S11_output_holds_no_report_or_message_bodies_and_no_environment_values(self):
        path = self.write_plan(graph([]))
        self.write_ledger(
            "bench",
            {"T1": "COMPLETE"},
            tasks={
                "t1": {
                    "packet": {"unit": "t1", "plan": str(path)},
                    "status": "COMPLETE",
                    "report": {"summary": BODY},
                    "messages": [{"text": BODY}],
                    "pr": "https://github.com/o/r/pull/7",
                }
            },
        )
        shown = self.workbench("plans")[1] + self.workbench("plan", str(path))[1]
        self.assertNotIn(BODY, shown)
        self.assertNotIn("REPORT-BODY", shown)
        self.assertNotIn(SECRET, shown)
        self.assertNotIn("WORKBENCH_SECRET", shown)


class AgentsTests(WorkbenchFixture):
    def with_agents(self, slug, agents, tasks=None, plan_state="RUNNING"):
        path = self.write_ledger(
            slug, {"T1": "IN PROGRESS", "T2": "TO-DO"}, plan_state=plan_state, tasks=tasks
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        data["agents"] = agents
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_S1_each_ticket_carries_the_ledger_agents_of_its_unit(self):
        path = self.approved("bench", [], [])
        self.with_agents(
            "bench",
            {
                "bench-t1": {"role": "worker", "unit": "t1", "address": "term-1"},
                "bench-t1-review": {"role": "reviewer", "unit": "t1", "address": None},
                "bench-archive": {"role": "worker", "unit": "archive", "address": "term-a"},
                # never matched by name: the name says t2, the unit says t1
                "bench-t2": {"role": "worker", "unit": "t1", "address": "term-x"},
            },
        )
        tickets = {t["id"]: t for t in self.view(path)["tickets"]}
        self.assertEqual(
            sorted(tickets["T1"]["agents"], key=lambda a: a["name"]),
            [
                {"name": "bench-t1", "role": "worker", "address": "term-1"},
                {"name": "bench-t1-review", "role": "reviewer", "address": None},
                {"name": "bench-t2", "role": "worker", "address": "term-x"},
            ],
        )
        self.assertEqual(tickets["T2"]["agents"], [])

    def test_S2_each_plan_lists_its_ticket_agents_and_the_prs_awaiting_merge(self):
        self.approved("bench", [], [])
        self.approved("old")
        self.write_ledger("old", {"T1": "COMPLETE"}, plan_state="ARCHIVED")
        merge = {
            "packet": {"unit": "t2"},
            "gate": "awaiting-merge",
            "pr": "https://github.com/o/r/pull/7",
        }
        self.with_agents(
            "bench",
            {
                "bench-t1": {"role": "worker", "unit": "t1", "address": "term-1"},
                "bench-archive": {"role": "worker", "unit": "archive", "address": "term-a"},
            },
            tasks={
                "t2": merge,
                "t1": {"packet": {"unit": "t1"}, "gate": "ci", "pr": "https://x/pull/1"},
            },
        )
        by_slug = self.by_slug(self.listing())
        self.assertEqual(
            by_slug["bench"]["agents"],
            [{"name": "bench-t1", "role": "worker", "ticket": "T1", "address": "term-1"}],
        )
        self.assertEqual(
            by_slug["bench"]["awaiting_merge"],
            [{"ticket": "T2", "pr": "https://github.com/o/r/pull/7"}],
        )
        self.assertEqual((by_slug["old"]["agents"], by_slug["old"]["awaiting_merge"]), ([], []))
