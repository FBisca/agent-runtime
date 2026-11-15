"""The sensitive-path rule: a mandatory baseline, extended by a repository, decided from every changed path.

00c-agent-review-safeguards/S3, S4, S5, S9 (SG-D3, SG-D4, SG-D6, D13, D30): a change to a path the baseline
or the repository declares sensitive escalates with the rule, the changed path, the pattern it matched and
where that pattern came from; a near miss escalates nothing; a repository's list extends the mandatory
baseline instead of replacing it; and a rule configuration the schema does not allow is refused by field
path before any change is read.

The rule reads the change as `change.read` spells it - an added, modified or deleted file, and both sides of
a rename - so a pattern is proved against the diff text, not against the working tree.
"""

import difflib

import pytest
from agent_review import questions, safeguards
from agent_review.change import Change, read
from agent_review.errors import ReviewError

pytestmark = pytest.mark.unit

CONFIG = """\
model: test-model-v1
questions:
  - id: repo.error-handling
    priority: critical
    type: noul
    ask: Does the change keep the documented error-handling contract
"""


def unified(path, before, after, status=None):
    """The diff of one path, spelled the way `git diff` spells it."""
    body = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    header = f"diff --git a/{path} b/{path}\n"
    if status is not None:
        header += f"{status} file mode 100644\n"
    return header + body


def renamed(old, new):
    """A 100% rename: both names, and no hunk to read."""
    return (
        f"diff --git a/{old} b/{new}\nsimilarity index 100%\nrename from {old}\nrename to {new}\n"
    )


def decided(change: Change, global_paths=(), repository=()):
    """The change's reasons as (rule, path, pattern, source) tuples, over the configured paths."""
    rules = safeguards.rule_set(global_paths=global_paths, repository_paths=repository)
    return [
        (reason.rule, reason.path, reason.pattern, reason.source)
        for reason in safeguards.decide(change, rules)
    ]


def test_the_mandatory_baseline_ships_with_the_member():
    """The five paths D13 requires are the packaged bank's, so no repository has to declare them."""
    assert questions.baseline().sensitive_paths == (
        ".github/workflows/**",
        "server/**",
        "hooks/**",
        "pyproject.toml",
        "uv.lock",
    )


def test_S3_a_baseline_path_matched_as_added_modified_deleted_or_renamed_carries_one_reason_per_path():
    change = read(
        unified(".github/workflows/ci.yml", "", "on: push\n", status="new")
        + unified("pyproject.toml", '[project]\nname = "scratch"\n', '[project]\nname = "x"\n')
        + unified("hooks/hook_lib.py", "def owns(team):\n    return team\n", "", status="deleted")
        + renamed("hooks/pre_tool_use.py", "pkg/pre_tool_use.py")
        + renamed("pkg/helper.py", "server/helper.py")
    )
    global_paths = questions.baseline().sensitive_paths
    assert sorted(decided(change, global_paths)) == [
        (safeguards.SENSITIVE_PATH, ".github/workflows/ci.yml", ".github/workflows/**", "global"),
        (safeguards.SENSITIVE_PATH, "hooks/hook_lib.py", "hooks/**", "global"),
        (safeguards.SENSITIVE_PATH, "hooks/pre_tool_use.py", "hooks/**", "global"),
        (safeguards.SENSITIVE_PATH, "pyproject.toml", "pyproject.toml", "global"),
        (safeguards.SENSITIVE_PATH, "server/helper.py", "server/**", "global"),
    ], "a rename is a changed path on both of its sides"


def test_S4_a_near_miss_under_a_matching_directory_matches_no_pattern():
    change = read(
        unified(".github/ISSUE_TEMPLATE.md", "", "template\n", status="new")
        + unified("serverx/tool.py", "", "value = 1\n", status="new")
        + unified("hooks_old/hook.py", "", "value = 1\n", status="new")
        + unified("server/agent.py", "value = 1\n", "value = 2\n")
    )
    global_paths = questions.baseline().sensitive_paths
    assert decided(change, global_paths, ("pkg/**",)) == [
        (safeguards.SENSITIVE_PATH, "server/agent.py", "server/**", "global")
    ], "a path under a matching directory is not a path the pattern names"


def test_S5_a_repository_pattern_extends_the_baseline_instead_of_replacing_it():
    change = read(
        unified(
            "pkg/service.py",
            "def load(path):\n    return path\n",
            "def load(path):\n    return path or '/'\n",
        )
        + unified("pyproject.toml", '[project]\nname = "scratch"\n', '[project]\nname = "x"\n')
        + unified(".agent-review.yaml", "", "model: test-model-v1\n", status="new")
    )
    global_paths = questions.baseline().sensitive_paths
    assert sorted(decided(change, global_paths, ("pkg/**",))) == [
        (safeguards.SENSITIVE_PATH, "pkg/service.py", "pkg/**", "repository"),
        (safeguards.SENSITIVE_PATH, "pyproject.toml", "pyproject.toml", "global"),
    ], "the repository's own list adds to the mandatory baseline"


def test_a_repository_pattern_that_repeats_a_baseline_pattern_keeps_the_baseline_source():
    rules = safeguards.rule_set(global_paths=("server/**",), repository_paths=("server/**",))
    change = read(unified("server/agent.py", "value = 1\n", "value = 2\n"))
    reasons = safeguards.decide(change, rules)
    assert [(reason.pattern, reason.source) for reason in reasons] == [("server/**", "global")], (
        "a pattern the baseline already names is not the repository's to claim"
    )


@pytest.mark.parametrize("spelled", ["hooks/**", {"dir": "hooks/**"}, 7])
def test_S9_sensitive_paths_that_is_not_a_list_is_refused_by_path(tmp_path, spelled):
    import yaml

    path = tmp_path / "config.yaml"
    settings = yaml.safe_load(CONFIG)
    settings["sensitive_paths"] = spelled
    path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    with pytest.raises(ReviewError) as refused:
        questions.load(path)
    assert "sensitive_paths" in str(refused.value), "a refusal names the field by its path"
    assert "list of patterns" in str(refused.value), "the refusal says the shape it needs"
    assert refused.value.code == 2, "an invalid configuration is a refusal, not a screening"


@pytest.mark.parametrize("entry", [1, "", "  "])
def test_S9_sensitive_paths_that_holds_something_else_than_patterns_is_refused_by_path(
    tmp_path, entry
):
    import yaml

    path = tmp_path / "config.yaml"
    settings = yaml.safe_load(CONFIG)
    settings["sensitive_paths"] = ["hooks/**", entry]
    path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    with pytest.raises(ReviewError) as refused:
        questions.load(path)
    assert "sensitive_paths[1]" in str(refused.value), "a refusal names the entry by its path"
    assert refused.value.code == 2


@pytest.mark.parametrize("spelled", ["hooks/", {"dir": "hooks/"}, 7])
def test_S9_a_stdlib_only_declaration_that_is_not_a_list_of_paths_is_refused_by_path(
    tmp_path, spelled
):
    import yaml

    path = tmp_path / "config.yaml"
    settings = yaml.safe_load(CONFIG)
    settings["safeguards"] = {"stdlib_only": spelled}
    path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    with pytest.raises(ReviewError) as refused:
        questions.load(path)
    assert "safeguards.stdlib_only" in str(refused.value), "a refusal names the field by its path"
    assert "list of paths" in str(refused.value), "the refusal says the shape it needs"
    assert refused.value.code == 2, "an invalid configuration is a refusal, not a screening"


@pytest.mark.parametrize("entry", [1, "", "  "])
def test_S9_a_stdlib_only_entry_that_is_not_a_path_is_refused_by_path(tmp_path, entry):
    import yaml

    path = tmp_path / "config.yaml"
    settings = yaml.safe_load(CONFIG)
    settings["safeguards"] = {"stdlib_only": ["hooks/", entry]}
    path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    with pytest.raises(ReviewError) as refused:
        questions.load(path)
    assert "safeguards.stdlib_only[1]" in str(refused.value), (
        "a refusal names the entry by its path"
    )
    assert refused.value.code == 2


def test_a_configured_sensitive_path_list_is_carried_as_patterns(tmp_path):
    """A valid list loads as patterns, so the rule has something to match before any refusal matters."""
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG + 'sensitive_paths:\n  - "db/migrations/**"\n', encoding="utf-8")
    assert questions.load(path).sensitive_paths == ("db/migrations/**",)
    assert questions.baseline().sensitive_paths, "the packaged bank ships the mandatory baseline"
