"""The rulebook a repository names: the config key, and the file both briefs point at."""

import pytest
from agent_orchestrator import config
from agent_orchestrator.driver.pointers import rulebook

pytestmark = pytest.mark.integration


@pytest.mark.spec("A repository names its rulebook")
def test_the_rulebook_is_the_file_the_config_names_in_the_checkout(tmp_path):
    (tmp_path / ".orchestrate").mkdir()
    (tmp_path / ".orchestrate" / "config.yaml").write_text("rulebook: docs/rulebook.md\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "rulebook.md").write_text("rules")
    assert config.load(tmp_path).rulebook == "docs/rulebook.md"
    assert rulebook(tmp_path, tmp_path) == str(tmp_path / "docs" / "rulebook.md")


@pytest.mark.spec("A repository names its rulebook")
def test_a_repository_without_the_key_gets_no_rulebook(tmp_path):
    assert config.load(tmp_path).rulebook is None
    assert rulebook(tmp_path, tmp_path) is None


@pytest.mark.spec("A repository names its rulebook")
def test_a_rulebook_that_is_not_in_the_checkout_is_refused(tmp_path):
    (tmp_path / ".orchestrate").mkdir()
    (tmp_path / ".orchestrate" / "config.yaml").write_text("rulebook: docs/rulebook.md\n")
    with pytest.raises(ValueError, match="rulebook.md"):
        rulebook(tmp_path, tmp_path)
