"""The root pytest run collects each package's promoted acceptance tests, with the package's tests/ importable.

A promoted acceptance file lives at `<package>/acceptance/<domain>/test_x.py`, beside the package's own
tests/. Its module imports the package's helpers (`fixtures`) the way the package's own tests do, so the
package's tests/ must be on the import path when the acceptance file is collected.
"""

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
ORCHESTRATOR_ACCEPTANCE = "packages/agent-orchestrator/acceptance"
ORCHESTRATOR_TESTS = "packages/agent-orchestrator/tests"


pytestmark = pytest.mark.unit


def _pytest_config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]


def test_S13_root_config_collects_the_orchestrator_acceptance_dir():
    ini = _pytest_config()
    assert ORCHESTRATOR_ACCEPTANCE in ini["testpaths"]


def test_S13_root_config_puts_the_orchestrator_tests_on_the_import_path():
    ini = _pytest_config()
    assert ORCHESTRATOR_TESTS in ini["pythonpath"]


def test_S13_repo_config_names_plans_acceptance_and_promote():
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert data["plans"]["dir"] == "docs/plans"
    assert data["acceptance"]["dir"] == "acceptance"
    assert data["archive"]["mode"] == "promote"


@pytest.mark.integration
def test_S13_promoted_acceptance_test_is_collected_and_imports_package_fixtures(tmp_path):
    """Drives a real pytest subprocess on a copy of the root config, with a promoted test beside a helper."""
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

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_promoted_imports_package_fixtures" in result.stdout
    assert "1 passed" in result.stdout
