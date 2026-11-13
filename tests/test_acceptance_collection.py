"""The root pytest run collects each package's promoted acceptance tests, with the package's tests/ importable.

A promoted acceptance file lives at `<package>/acceptance/<domain>/test_x.py`, beside the package's own
tests/. Its module imports the package's helpers (`fixtures`) the way the package's own tests do, so the
package's tests/ must be on the import path when the acceptance file is collected.
"""

import ast
import re
import socket
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
CONFIG = ROOT / ".orchestrate" / "config.yaml"
ACCEPTANCE_GLOB = "packages/*/acceptance"
ORCHESTRATOR_TESTS = "packages/agent-orchestrator/tests"
# The repository root is a package too (`package: .`): its tests/ and its promoted acceptance/.
ROOT_ACCEPTANCE = "acceptance"
ROOT_TESTS = "tests"


pytestmark = pytest.mark.unit


def _pytest_config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]


def test_S13_root_config_collects_the_orchestrator_acceptance_dir():
    ini = _pytest_config()
    assert ACCEPTANCE_GLOB in ini["testpaths"]


def test_S13_root_config_puts_the_orchestrator_tests_on_the_import_path():
    ini = _pytest_config()
    assert ORCHESTRATOR_TESTS in ini["pythonpath"]


def test_S13_root_config_collects_the_root_acceptance_dir_with_the_root_tests_importable():
    ini = _pytest_config()
    assert ROOT_ACCEPTANCE in ini["testpaths"]
    assert ROOT_TESTS in ini["pythonpath"]


def test_S13_repo_config_names_plans_acceptance_and_promote():
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert data["plans"]["dir"] == "docs/plans"
    assert data["acceptance"]["dir"] == "acceptance"
    assert data["archive"]["mode"] == "promote"


@pytest.mark.integration
def test_S13_promoted_acceptance_tests_are_collected_in_any_package(tmp_path):
    """Drives a real pytest subprocess on a copy of the root config.

    The orchestrator's promoted test imports its package's helper; a promoted test under another package's
    acceptance dir is collected too, so the glob is not orchestrator-only.
    """
    (tmp_path / "pyproject.toml").write_text(
        PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    package = tmp_path / "packages" / "agent-orchestrator"
    (package / "tests").mkdir(parents=True)
    (package / "tests" / "fixtures.py").write_text(
        "HELPER = 'from-package-tests'\n", encoding="utf-8"
    )
    domain = package / "acceptance" / "demo"
    domain.mkdir(parents=True)
    (domain / "test_x.py").write_text(
        textwrap.dedent(
            """\
            from fixtures import HELPER


            def test_promoted_imports_package_fixtures():
                assert HELPER == "from-package-tests"
            """
        ),
        encoding="utf-8",
    )

    other = tmp_path / "packages" / "agent-eval" / "acceptance" / "demo"
    other.mkdir(parents=True)
    (other / "test_y.py").write_text(
        "def test_promoted_in_another_package():\n    assert True\n", encoding="utf-8"
    )

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "root_helper.py").write_text(
        "ROOT = 'from-root-tests'\n", encoding="utf-8"
    )
    root_domain = tmp_path / "acceptance" / "demo"
    root_domain.mkdir(parents=True)
    (root_domain / "test_z.py").write_text(
        textwrap.dedent(
            """\
            from root_helper import ROOT


            def test_promoted_at_the_root_imports_root_tests():
                assert ROOT == "from-root-tests"
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_promoted_imports_package_fixtures" in result.stdout
    assert "test_promoted_in_another_package" in result.stdout
    assert "test_promoted_at_the_root_imports_root_tests" in result.stdout
    assert "3 passed" in result.stdout


SPECS = ROOT / "docs" / "specs"
TESTS_ROOT = ("tests", "acceptance", "packages/agent-orchestrator/tests", "packages/agent-eval/tests")


def _defined_names(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def test_S2_every_node_id_a_living_spec_cites_names_a_test_that_exists():
    """A `path::Class::test` citation resolves to a file that exists and a test defined in it."""
    stale = []
    for spec in sorted(SPECS.glob("*.md")):
        for path, node in re.findall(r"([A-Za-z0-9_./-]+\.py)::([A-Za-z0-9_:]+)", spec.read_text(encoding="utf-8")):
            if path.startswith("agent-skills/") or "<domain>" in path:
                continue  # a skill's own tests and a path template are outside this suite
            target = ROOT / path
            if not target.is_file():
                stale.append(f"{spec.name}: missing file {path}")
            elif node.split("::")[-1] not in _defined_names(target):
                stale.append(f"{spec.name}: {path}::{node} is not defined")
    assert stale == []


def test_S4_every_test_module_under_testpaths_declares_its_level():
    """A `test_*.py` under a testpaths directory sets pytestmark to unit or integration."""
    undeclared = []
    for root in TESTS_ROOT:
        for module in sorted((ROOT / root).rglob("test_*.py")):
            if not re.search(r"^pytestmark\s*=\s*pytest\.mark\.(unit|integration)\b", module.read_text(encoding="utf-8"), re.M):
                undeclared.append(str(module.relative_to(ROOT)))
    assert undeclared == []


@pytest.mark.integration
def test_S5_option_only_invocations_select_what_the_bare_run_selects():
    """`-p no:cacheprovider` (an option with a value) collects the same tests as the bare run."""

    def collected(*args):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", *args],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
        return [line for line in result.stdout.splitlines() if "::" in line]

    bare = collected()
    assert bare, "the bare run collects nothing"
    assert collected("-p", "no:cacheprovider") == bare
    assert collected("-p", "no:warnings") == bare


def test_S3_a_unit_test_cannot_start_a_process_or_open_a_socket():
    """The fast suite's guard refuses a process and a network connection in a unit-marked test."""
    with pytest.raises(RuntimeError, match="unit"):
        subprocess.run([sys.executable, "-c", "pass"], capture_output=True)
    with pytest.raises(RuntimeError, match="unit"):
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)
