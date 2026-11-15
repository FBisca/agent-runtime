"""S1-S9: the deterministic safeguards, driven through the console script over real changes.

A safeguard is the half of the gate that needs no model: it decides from the diff alone, before the provider
is asked, and it says which rule fired and which path it matched. Three rules are proved here - a delete or
overwrite nothing confines to the repository root, a change to a path the repository protects, and an import
outside the standard library in a member declared stdlib-only - each with the control that must stay quiet.

Two runs are over this repository's own history, which is where the rules were learned: `429625b` introduced
the unguarded `shutil.rmtree` and `109c98b` confined it. Every other run builds a scratch repository, so a
rule is proved against the shape it refuses rather than against a fixture that already agrees with it.

At base the whole file is red: the tool has no escalation verdict, no rule set and no `usage.requests: 0`
path. The controls become green when the provider contract lands, because they screen through a canned
provider in that contract's shape; they are here so a scenario cannot pass on a run that never screened.

No check reaches the network: a positive case names no credential and no canned provider, so a tool that
asked anyway would fail rather than escalate.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# The pair the first rule was learned from: the defect, and the fix that confines it.
INTRODUCED = "429625b"
FIXED = "109c98b"

CONFIG = """\
model: jev-latest
questions:
  - id: repo.error-handling
    priority: critical
    type: noul
    ask: Does the change keep every new failure actionable
  - id: repo.hidden-state
    priority: high
    type: noul
    ask: Does the change keep its state explicit
safeguards:
  stdlib_only:
    - hooks/
    - pkg/plugin/
"""

CANNED = {
    "model": "jev-1.14.0",
    "answers": {
        "repo.error-handling": {"type": "noul", "noul": 0.8},
        "repo.hidden-state": {"type": "noul", "noul": 0.7},
    },
    "usage": {"input_tokens": 120, "output_tokens": 12},
}

UNGUARDED = """\
import shutil
from pathlib import Path


def discard(folder):
    shutil.rmtree(folder)
"""

GUARDED = """\
import shutil
from pathlib import Path


def discard(folder, plans_root):
    target = Path(folder).resolve()
    if not target.is_relative_to(Path(plans_root).resolve()):
        raise ValueError("refusing to delete outside the plans directory: %s" % target)
    shutil.rmtree(target)
"""


def git(where, *args):
    """Run git in `where`, failing loudly: a helper that hides a git error would hide the test's own setup."""
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


def repository_root():
    """The checkout this test runs in, found by git rather than by counting the path's parents.

    The file is placed at `packages/agent-review/acceptance/safeguards/` when the evidence runs, and the
    engine runs it in a scratch checkout, so its depth differs between the plan folder and its destination.
    """
    return Path(git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel"))


class SafeguardFixture(unittest.TestCase):
    """A scratch repository, a configuration, and the console script run as a person runs it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name, "checkout")
        self.workspace.mkdir()
        self.output = Path(self.tmp.name, "review")
        self.config = Path(self.tmp.name, "config.yaml")
        self.canned = Path(self.tmp.name, "canned.json")
        self.write_config(CONFIG)
        self.canned.write_text(json.dumps({"responses": [CANNED]}), encoding="utf-8")
        self.seed()

    def seed(self):
        """A repository with a member of each kind the rule set names, and one honest change to screen."""
        git(self.workspace, "init", "-b", "main")
        files = {
            "pkg/service.py": "def load(path):\n    return path\n",
            "pkg/helper.py": "def helper():\n    return 'helper'\n",
            "pkg/__init__.py": "",
            "server/agent_runtime.py": "import json\n",
            "hooks/pre_tool_use.py": "import json\n",
            "hooks/hook_lib.py": "def owns(team):\n    return team == 'ours'\n",
            "pyproject.toml": '[project]\nname = "scratch"\n',
            "README.md": "scratch\n",
        }
        for name, text in files.items():
            path = self.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.commit("base")
        self.base = git(self.workspace, "rev-parse", "HEAD")
        (self.workspace / "pkg/service.py").write_text(
            "def load(path):\n    return path or None\n", encoding="utf-8"
        )
        self.commit("head")
        self.head = git(self.workspace, "rev-parse", "HEAD")

    def commit(self, message):
        git(self.workspace, "add", "--all")
        git(
            self.workspace,
            "-c",
            "user.name=acceptance",
            "-c",
            "user.email=acceptance@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            message,
        )
        return git(self.workspace, "rev-parse", "HEAD")

    def write_text(self, name, text):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_config(self, text=CONFIG):
        self.config.write_text(text, encoding="utf-8")
        return self.config

    def invoke(
        self,
        *,
        base=None,
        head=None,
        cwd=None,
        config=True,
        canned=False,
        output=None,
    ):
        """One `agent-review code` invocation, with no credential in its environment.

        Without `canned` the run has no provider at all: a run that escalated spent no request, and a run
        that reached for one stopped incomplete instead of answering. That is the observable a positive
        case rests on.
        """
        binary = shutil.which("agent-review")
        if binary is None:
            self.fail("no `agent-review` on PATH: run the check under `uv run`")
        argv = [
            binary,
            "code",
            "--base",
            base or self.base,
            "--head",
            head or self.head,
            "--output",
            str(output or self.output),
        ]
        if config:
            argv += ["--config", str(self.config)]
        if canned:
            argv += ["--screening-fixture", str(self.canned)]
        return subprocess.run(
            argv,
            cwd=cwd or self.workspace,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                "HOME": str(Path(self.tmp.name, "home")),
            },
        )

    def report(self):
        path = self.output / "report.json"
        self.assertTrue(path.is_file(), f"no report at {path}: an invocation writes one either way")
        return json.loads(path.read_text(encoding="utf-8"))

    def reasons(self, rule=None):
        """The escalation reasons the report carries, optionally narrowed to one rule."""
        found = self.report().get("escalation", {}).get("reasons", [])
        self.assertIsInstance(found, list, "escalation.reasons is a list of reasons")
        return [reason for reason in found if rule is None or reason.get("rule") == rule]

    def assert_escalated(self, result, rule):
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        self.assertEqual(report["verdict"], "escalated", report.get("escalation"))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["usage"]["requests"], 0, "a safeguard must not spend a request")
        self.assertEqual(report["judgments"], [], "nothing was asked, so nothing was answered")
        return [reason for reason in self.reasons() if reason.get("rule") == rule]


class FixtureControlTests(SafeguardFixture):
    """What every check's selection carries so the oracle cannot read green without a scenario behind it.

    These pass at base and after: they prove the scratch repository and the pinned history are what the
    scenario tests think they are, never that a safeguard works.
    """

    def test_control_the_scratch_repository_holds_every_member_the_rules_name(self):
        for name in (
            "pkg/service.py",
            "pkg/helper.py",
            "server/agent_runtime.py",
            "hooks/pre_tool_use.py",
            "hooks/hook_lib.py",
            "pyproject.toml",
        ):
            self.assertTrue((self.workspace / name).is_file(), name)
        changed = git(self.workspace, "diff", "--name-status", f"{self.base}..{self.head}")
        self.assertEqual(changed, "M\tpkg/service.py")

    def test_control_the_history_the_historical_cases_pin_resolves(self):
        root = repository_root()
        git(root, "merge-base", "--is-ancestor", INTRODUCED, FIXED)
        git(root, "merge-base", "--is-ancestor", FIXED, "HEAD")
        self.assertNotEqual(INTRODUCED[:7], FIXED[:7], "the defect and its fix are two commits")


class OutsideRootTests(SafeguardFixture):
    """S1 and S2: a delete or overwrite the change does not confine to the configured root."""

    def test_S1_the_unguarded_rmtree_in_this_history_escalates(self):
        root = repository_root()
        base = git(root, "rev-parse", f"{INTRODUCED}^")
        head = git(root, "rev-parse", INTRODUCED)
        result = self.invoke(base=base, head=head, cwd=root)
        reasons = self.assert_escalated(result, "target-outside-root")
        self.assertEqual(len(reasons), 1, reasons)
        self.assertEqual(
            reasons[0]["path"], "packages/agent-orchestrator/src/agent_orchestrator/driver.py"
        )
        self.assertIn("target-outside-root", result.stdout + result.stderr)

    def test_S1_an_unguarded_delete_of_a_computed_target_escalates(self):
        self.write_text("pkg/service.py", UNGUARDED)
        head = self.commit("discard a folder")
        result = self.invoke(base=self.base, head=head)
        reasons = self.assert_escalated(result, "target-outside-root")
        self.assertEqual(len(reasons), 1, reasons)
        self.assertEqual(reasons[0]["path"], "pkg/service.py")

    def test_S2_the_confining_fix_does_not_escalate(self):
        root = repository_root()
        base = git(root, "rev-parse", f"{FIXED}^")
        head = git(root, "rev-parse", FIXED)
        result = self.invoke(base=base, head=head, cwd=root, canned=True)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(self.reasons("target-outside-root"), [])
        self.assertEqual(self.report()["verdict"], "screened", self.report())

    def test_S2_a_confinement_test_in_the_same_change_does_not_escalate(self):
        self.write_text("pkg/service.py", GUARDED)
        head = self.commit("discard a folder under the plans directory")
        result = self.invoke(base=self.base, head=head, canned=True)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(self.reasons("target-outside-root"), [])
        self.assertEqual(self.report()["verdict"], "screened", self.report())


class ProtectedPathTests(SafeguardFixture):
    """S3-S5: a changed path the baseline or the repository declares protected."""

    def protect(self):
        """One change that reaches a protected path five ways: modified, added, renamed out of, renamed into,
        deleted."""
        self.write_text("pyproject.toml", '[project]\nname = "scratch"\nversion = "1"\n')
        self.write_text(".github/workflows/ci.yml", "on: push\n")
        self.write_text("server/extra.py", "import json\n")
        os.rename(self.workspace / "hooks/pre_tool_use.py", self.workspace / "pkg/pre_tool_use.py")
        os.rename(self.workspace / "pkg/helper.py", self.workspace / "server/helper.py")
        (self.workspace / "hooks/hook_lib.py").unlink()
        return self.commit("reach into every protected path")

    def test_S3_every_protected_path_reached_escalates_naming_pattern_and_source(self):
        head = self.protect()
        result = self.invoke(base=self.base, head=head)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        matched = {reason["path"]: reason for reason in self.reasons("sensitive-path")}
        self.assertEqual(
            sorted(matched),
            [
                ".github/workflows/ci.yml",
                "hooks/hook_lib.py",
                "hooks/pre_tool_use.py",
                "pyproject.toml",
                "server/extra.py",
                "server/helper.py",
            ],
            self.report(),
        )
        self.assertEqual(matched["pyproject.toml"]["pattern"], "pyproject.toml")
        self.assertEqual(matched[".github/workflows/ci.yml"]["pattern"], ".github/workflows/**")
        self.assertEqual(matched["server/extra.py"]["pattern"], "server/**")
        self.assertEqual(
            matched["server/helper.py"]["pattern"],
            "server/**",
            "the side a rename arrives at is a changed path too",
        )
        self.assertEqual(
            matched["hooks/pre_tool_use.py"]["pattern"],
            "hooks/**",
            "the side a rename leaves is a changed path too",
        )
        self.assertTrue(all(reason["source"] == "global" for reason in matched.values()), matched)
        self.assertEqual(self.report()["verdict"], "escalated")
        self.assertEqual(self.report()["usage"]["requests"], 0)

    def test_S4_a_near_miss_and_an_ordinary_path_do_not_escalate(self):
        self.write_text(".github/ISSUE_TEMPLATE.md", "template\n")
        self.write_text("pkg/service.py", "def load(path):\n    return path or '/'\n")
        head = self.commit("an issue template beside the workflows directory")
        result = self.invoke(base=self.base, head=head, canned=True)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(self.reasons("sensitive-path"), [], self.report())
        self.assertEqual(self.report()["verdict"], "screened")

    def test_S5_a_repository_configuration_extends_the_baseline_without_replacing_it(self):
        self.write_text(
            ".agent-review.yaml",
            CONFIG + "sensitive_paths:\n  - pkg/**\n",
        )
        self.write_text("pyproject.toml", '[project]\nname = "scratch"\nversion = "1"\n')
        self.write_text("pkg/service.py", "def load(path):\n    return path or '/'\n")
        head = self.commit("reach into the repository's own protected path")
        result = self.invoke(base=self.base, head=head, config=False)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(self.report()["verdict"], "escalated")
        self.assertEqual(self.report()["usage"]["requests"], 0)
        reasons = self.reasons("sensitive-path")
        self.assertEqual(len(reasons), 2, reasons)
        matched = {reason["path"]: reason for reason in reasons}
        self.assertEqual(
            {reason.get("source") for reason in matched.values()},
            {"global", "repository"},
            self.report(),
        )
        self.assertEqual(matched["pkg/service.py"]["pattern"], "pkg/**")
        self.assertEqual(matched["pkg/service.py"]["source"], "repository")
        self.assertEqual(matched["pyproject.toml"]["source"], "global")


class CapabilityTests(SafeguardFixture):
    """S6 and S7: an import the member's declaration does not allow."""

    def test_S6_a_third_party_import_in_an_stdlib_only_member_escalates(self):
        self.write_text("hooks/pre_tool_use.py", "import json\n\nimport yaml\n")
        head = self.commit("a hook that needs a package the plugin cannot ship")
        result = self.invoke(base=self.base, head=head)
        reasons = self.assert_escalated(result, "stdlib-only")
        self.assertEqual(len(reasons), 1, reasons)
        self.assertEqual(reasons[0]["path"], "hooks/pre_tool_use.py")
        self.assertEqual(reasons[0]["module"], "yaml")
        self.assertIn("stdlib-only", result.stdout + result.stderr)

    def test_S6_a_from_import_and_a_local_import_escalate_too(self):
        self.write_text(
            "hooks/pre_tool_use.py",
            "import json\n\nfrom yaml import safe_load\n\n\ndef load(path):\n"
            "    import dotenv\n\n    return safe_load(path)\n",
        )
        head = self.commit("a hook that imports a package two other ways")
        result = self.invoke(base=self.base, head=head)
        modules = {reason["module"] for reason in self.assert_escalated(result, "stdlib-only")}
        self.assertEqual(
            modules, {"yaml", "dotenv"}, "a from-import and a function-local import are imports too"
        )

    def test_S7_the_standard_library_and_an_undeclared_member_stay_quiet(self):
        # A declared member outside `hooks/`: the mandatory baseline protects that directory, so a change there
        # escalates as `sensitive-path` and can never show that the screening proceeded.
        self.write_text("pkg/plugin/loader.py", "import json\nimport os\n")
        self.write_text("pkg/yaml_helpers.py", "import yaml\n")
        head = self.commit(
            "a stdlib import in a declared member and a package import in a free member"
        )
        result = self.invoke(base=self.base, head=head, canned=True)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(self.reasons("stdlib-only"), [], self.report())
        self.assertEqual(self.report()["verdict"], "screened")


class EscalationReportTests(SafeguardFixture):
    """S8: what a hit writes and says, and what the exit code and the summary owe the caller."""

    def test_S8_a_hit_completes_escalated_and_names_the_way_out(self):
        self.write_text("server/extra.py", "import json\n")
        head = self.commit("touch a protected member")
        result = self.invoke(base=self.base, head=head)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["verdict"], "escalated")
        self.assertEqual(report["judgments"], [])
        self.assertEqual(report["usage"]["requests"], 0)
        self.assertEqual(report["input"]["base"], self.base)
        self.assertEqual(report["input"]["head"], head)
        said = (result.stdout + result.stderr).lower()
        self.assertIn("sensitive-path", said)
        self.assertIn("server/extra.py", said)
        self.assertIn("deep review", said, "a refusal names the reason and the way out")


class ConfigurationTests(SafeguardFixture):
    """S9: a rule configuration the schema does not allow is refused before the change is read."""

    def test_S9_a_scalar_where_patterns_belong_is_refused_by_path(self):
        self.write_config(CONFIG + 'sensitive_paths: "hooks/**"\n')
        result = self.invoke(base=self.base, head=self.head)
        self.assertEqual(result.returncode, 2, result.stderr[-2000:])
        self.assertIn("sensitive_paths", result.stderr)
        self.assertFalse((self.output / "report.json").exists(), "a refusal writes no report")

    def test_S9_a_scalar_where_a_member_list_belongs_is_refused_by_path(self):
        self.write_config(CONFIG + "safeguards:\n  stdlib_only: hooks/\n")
        result = self.invoke(base=self.base, head=self.head)
        self.assertEqual(result.returncode, 2, result.stderr[-2000:])
        self.assertIn("safeguards.stdlib_only", result.stderr)
        self.assertFalse((self.output / "report.json").exists(), "a refusal writes no report")
