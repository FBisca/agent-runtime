"""The driver package after the split: the ceilings, the published names and the record it keeps.

`agent_orchestrator.driver` is a package whose `__init__.py` is a re-export facade: the engine's
collaborators live in the modules beside it, every public name they define is reachable as
`driver.<name>`, and no module or class of the package outgrows the structure budget. The durable
record is what outlives the split, so a ledger the older engine wrote still opens here, with its
version, its kind marker and its tasks, and needs no migration.
"""

import ast
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest
from fixtures import load_script

pytestmark = pytest.mark.unit

# <repo>/packages/agent-orchestrator/tests/test_driver/<this file>
REPO = Path(__file__).resolve().parents[4]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
DIR = MEMBER / "driver"

# The two ceilings the driver package is paid on, written out rather than imported: a changed ceiling
# is the spec's decision, and this test should fail when it is made.
CEILINGS = {"module-lines": 1000, "class-methods": 25}

# The measurement the gate runs, asked in process: the same five numbers over the same source, so a
# module or a class that outgrows a ceiling fails here as it fails the build.
budget = runpy.run_path(str(REPO / "scripts" / "structure_budget.py"))

# The ledger one engine wrote, in the shape it wrote it: the version, the kind marker, one attempt, and
# the top-level keys a run keeps beside its tasks. Literal on purpose — this is the file left on disk,
# and the engine after the split reads what an older one wrote, not what today's writer would.
BEFORE_THE_SPLIT = """{
  "version": 1,
  "kind": "agent-orchestrator/ledger",
  "tasks": {
    "t1": {
      "packet": {"task_id": "t1", "worker": "demo-t1", "unit": "t1", "backend": "claude"},
      "status": "ACTIVE",
      "runtime_id": "rt-1",
      "gate": "reported",
      "started_at": "2026-11-15T09:00:00+00:00"
    }
  },
  "ticket_status": {"T1": "IN PROGRESS"},
  "rounds": {"t1": 2}
}"""


def defined_names(source):
    """The public names one module defines: its functions, its classes and its constant-like targets.

    An import binds a name too, but publishing `driver.Path` is nobody's contract: what a split may
    drop is a name the package itself wrote, so only the definitions count.
    """
    found = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append(node.name)
        elif isinstance(node, ast.Assign):
            found.extend(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found.append(node.target.id)
    return [name for name in found if not name.startswith("_")]


class StructureTests(unittest.TestCase):
    """S11: the driver package is paid on module lines and class methods."""

    def test_S11_no_module_or_class_of_the_driver_package_outgrows_its_ceiling(self):
        """structure-budget/S11"""
        offenders = []
        for path in sorted(DIR.rglob("*.py")):
            numbers = budget["metrics_of"](path.read_text(encoding="utf-8"))
            for metric, ceiling in CEILINGS.items():
                if numbers[metric] > ceiling:
                    offenders.append(
                        "%s: %s %d > ceiling %d"
                        % (path.relative_to(REPO), metric, numbers[metric], ceiling)
                    )
        self.assertEqual(offenders, [], "the paid metrics are under their ceilings")
        self.assertFalse(
            (MEMBER / "driver.py").exists(), "the flat driver module the package replaced is gone"
        )


class FacadeTests(unittest.TestCase):
    """S22: every name the package's modules define is still reachable as `driver.<name>`."""

    def test_S22_every_public_name_the_package_modules_define_is_reachable_on_the_package(self):
        """structure-budget/S22"""
        from agent_orchestrator import driver

        defined = {}
        for path in sorted(DIR.glob("*.py")):
            for name in defined_names(path.read_text(encoding="utf-8")):
                defined.setdefault(name, []).append(path.name)

        def where(names):
            return ", ".join("%s (%s)" % (name, ", ".join(defined[name])) for name in names)

        # Reachable as an attribute, whoever binds it: an import in the facade publishes a name too,
        # so `driver.PlanError` answers even though the package did not write it.
        missing = sorted(set(defined) - set(vars(driver)))
        self.assertEqual(
            missing, [], "the facade drops names the modules define: %s" % where(missing)
        )
        # And published: `__all__` is the set a star import and a caller reading the package by name
        # agree on, so a name the facade only happens to import is not part of the contract.
        unpublished = sorted(set(defined) - set(driver.__all__))
        self.assertEqual(
            unpublished,
            [],
            "the facade does not publish names the modules define: %s" % where(unpublished),
        )

    def test_S22_no_module_of_the_package_takes_the_name_of_a_published_one(self):
        """structure-budget/S22"""
        from agent_orchestrator import driver

        # Importing a module sets its name on the package, so a module named `routes` answers as `routes`
        # whatever the facade exported under that name: one published name would mean the routing module
        # or the driver's own, whichever import ran last. `__all__` is what the facade publishes.
        taken = sorted(path.stem for path in DIR.glob("*.py") if path.stem in driver.__all__)
        self.assertEqual(taken, [], "modules that take a published name: %s" % ", ".join(taken))


class RecordTests(unittest.TestCase):
    """S13: a ledger written before the split is read after it, with no migration."""

    @pytest.mark.integration
    def test_S13_a_ledger_written_before_the_split_keeps_its_shape_through_the_facade(self):
        """structure-budget/S13"""
        from agent_orchestrator import driver

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plan.yaml").write_text("schema: plan/1\n", encoding="utf-8")
            run = root / "run"
            run.mkdir()
            ledger_path = root / "demo.json"
            ledger_path.write_text(BEFORE_THE_SPLIT, encoding="utf-8")
            engine = load_script().Driver(
                SimpleNamespace(plan=str(root / "plan.yaml"), repo=str(root))
            )
            engine.slug, engine.run_dir, engine.ledger_path = "demo", run, ledger_path
            self.assertIsInstance(engine.book, driver.Book, "the Driver holds the record's owner")

            # The read, through the surface the package publishes: the attempt the older engine
            # recorded answers as it stands.
            self.assertEqual(engine.task("t1")["status"], "ACTIVE")
            self.assertEqual(engine.unit_of("t1"), "t1")
            # The write, through the same surface.
            engine.mark("t1", gate="converged")

            after = json.loads(ledger_path.read_text(encoding="utf-8"))

        self.assertEqual(after["version"], 1, "the version the older engine wrote is kept")
        self.assertEqual(
            after["kind"],
            "agent-orchestrator/ledger",
            "the kind marker the older engine wrote is kept",
        )
        self.assertEqual(list(after["tasks"]), ["t1"], "the attempt the older engine wrote is kept")
        self.assertEqual(after["tasks"]["t1"]["gate"], "converged")
        self.assertIn("updated_at", after["tasks"]["t1"], "the mark was a write, not a read")
        self.assertEqual(after["ticket_status"], {"T1": "IN PROGRESS"})
        self.assertEqual(after["rounds"], {"t1": 2})


if __name__ == "__main__":
    unittest.main()
