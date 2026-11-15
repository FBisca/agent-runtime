"""The `stdlib-only` rule: a member that may carry only the standard library (00c-agent-review-safeguards/S6, S7).

The members are configuration (`safeguards.stdlib_only`: paths, matched as a prefix) and the rule is code:
an import the change adds in a covered member is read as the module it names, and a module the standard
library does not ship is a dependency the member must not have (SG-D6). The reason names the member's path
and the module beside the rule, the statement it matched and where that came from (SG-D3).

These run in process over diff text: no repository, no provider, no filesystem.
"""

import pytest
from agent_review import safeguards
from agent_review.change import read

pytestmark = pytest.mark.unit

DECLARATION = {"stdlib_only": ["hooks/"]}


def diff(path, added, removed="", status="modified"):
    """One changed path as `git diff` spells it, with the lines it adds and removes."""
    body = "".join(f"+{line}\n" for line in added.splitlines())
    body += "".join(f"-{line}\n" for line in removed.splitlines())
    header = "diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -1,3 +1,3 @@\n".format(path)
    if status == "added":
        header = f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,3 @@\n"
    if status == "deleted":
        header = f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n--- a/{path}\n+++ /dev/null\n@@ -1,3 +0,0 @@\n"
    return header + body


def decided(text, declaration=DECLARATION):
    """The reasons `text` is escalated for."""
    return safeguards.decide(read(text), declaration)


def test_S6_a_declared_member_that_imports_outside_the_standard_library_escalates():
    reasons = decided(diff("hooks/pre_tool_use.py", "import requests\n"))
    assert [reason.rule for reason in reasons] == ["stdlib-only"]
    reason = reasons[0]
    assert reason.path == "hooks/pre_tool_use.py", "the reason names the member's own path"
    assert reason.module == "requests", "the reason names the imported module"
    assert reason.pattern == "import requests", "the reason carries the line it matched"
    assert reason.source == "diff", "the import is read from the change, not from a list"


def test_S6_the_reason_reads_as_the_report_contract_holds_it_with_the_module_beside_the_rule():
    reason = decided(diff("hooks/pre_tool_use.py", "import requests\n"))[0]
    assert reason.as_dict() == {
        "rule": "stdlib-only",
        "path": "hooks/pre_tool_use.py",
        "pattern": "import requests",
        "source": "diff",
        "module": "requests",
    }, "a `stdlib-only` reason adds `module` to the reason's fields (SG-D3)"


def test_S6_a_from_import_outside_the_standard_library_names_the_module_not_the_name():
    reason = decided(diff("hooks/lead_context.py", "from yaml import safe_load\n"))[0]
    assert reason.module == "yaml"
    assert reason.pattern == "from yaml import safe_load"


def test_S6_the_module_is_the_top_level_name_a_dotted_import_reaches_beside():
    reason = decided(diff("hooks/lead_context.py", "import yaml.parser\n"))[0]
    assert reason.module == "yaml", "a dotted import is a dependency on its top-level distribution"


def test_S6_one_line_naming_several_modules_names_the_first_outside_the_standard_library():
    reason = decided(diff("hooks/lead_context.py", "import json, requests\n"))[0]
    assert reason.module == "requests", "a module the standard library ships is not the reason"


def test_S6_every_outside_module_the_member_adds_is_its_own_reason():
    text = diff(
        "hooks/pre_tool_use.py",
        "import json\n\nfrom yaml import safe_load\n\n\ndef load(path):\n"
        "    import dotenv\n\n    return safe_load(path)\n",
    )
    reasons = decided(text)
    assert [reason.module for reason in reasons] == ["yaml", "dotenv"], (
        "a from-import and a function-local import are imports too, and each names its own module"
    )
    assert {reason.path for reason in reasons} == {"hooks/pre_tool_use.py"}
    assert [reason.pattern for reason in reasons] == [
        "from yaml import safe_load",
        "import dotenv",
    ], "each reason carries the statement that named its module"


def test_S6_one_module_the_member_imports_twice_is_one_reason():
    assert [
        reason.module
        for reason in decided(diff("hooks/pre_tool_use.py", "import yaml\n\nimport yaml.parser\n"))
    ] == ["yaml"], "a reason names a module the member must not have, once"


def test_S6_a_member_declared_as_a_file_covers_that_file_and_no_sibling():
    declaration = {"stdlib_only": ["hooks/pre_tool_use.py"]}
    assert decided(diff("hooks/pre_tool_use.py", "import requests\n"), declaration)[0].module == (
        "requests"
    )
    assert decided(diff("hooks/lead_context.py", "import requests\n"), declaration) == ()


def test_S6_a_declared_directory_covers_what_lies_below_it_and_no_near_name():
    assert (
        decided(diff("hooks/nested/tool.py", "import requests\n"))[0].path == "hooks/nested/tool.py"
    )
    assert decided(diff("hooks_old/tool.py", "import requests\n")) == (), (
        "a prefix is matched on a path boundary, never on a name that merely starts the same"
    )


def test_S6_a_member_written_without_its_trailing_slash_is_the_same_member():
    declaration = {"stdlib_only": ["hooks"]}
    assert decided(diff("hooks/pre_tool_use.py", "import requests\n"), declaration)[0].module == (
        "requests"
    )


def test_S7_a_declared_member_that_adds_a_standard_library_import_is_not_escalated():
    assert decided(diff("hooks/pre_tool_use.py", "import json\nfrom pathlib import Path\n")) == ()


def test_S7_an_undeclared_member_that_adds_a_third_party_import_is_not_escalated():
    assert decided(diff("lib/tool.py", "import requests\n")) == (), (
        "a member no declaration covers may import what it likes"
    )


def test_S7_a_rule_set_carries_the_declared_members_and_decides_with_them():
    rules = safeguards.rule_set(stdlib_only_paths=["hooks/", "hooks", "hooks/**"])
    assert rules.stdlib_only == ("hooks",), (
        "the declaration is parsed once, and one member spelled three ways is one member"
    )
    reasons = safeguards.decide(read(diff("hooks/pre_tool_use.py", "import requests\n")), rules)
    assert [reason.module for reason in reasons] == ["requests"], (
        "a member carried on the rule set decides the rule the same way"
    )


def test_S6_a_trailing_comment_on_an_import_line_cannot_hide_the_module():
    for line in (
        "import requests  # HTTP client\n",
        "import requests#http\n",
        "import requests  # noqa: E402\n",
        "import requests  # type: ignore\n",
        "import os, requests  # both\n",
    ):
        reasons = decided(diff("hooks/pre_tool_use.py", line))
        assert [reason.module for reason in reasons] == ["requests"], line


def test_S6_a_second_statement_on_the_added_line_is_read_as_an_import_too():
    reasons = decided(diff("hooks/pre_tool_use.py", "import requests; import yaml\n"))
    assert [reason.module for reason in reasons] == ["requests", "yaml"], (
        "a simple-statement line makes two imports, and both are the member's dependencies"
    )


def test_S6_an_import_quoted_in_a_docstring_is_not_a_dependency_the_member_adds():
    text = diff(
        "hooks/pre_tool_use.py", 'def load(path):\n    return """\nimport requests\n"""\n'
    )
    assert decided(text) == (), "an import a docstring quotes is text, not a module the member imports"


def test_S7_a_declaration_that_covers_no_member_escalates_nothing():
    assert decided(diff("hooks/pre_tool_use.py", "import requests\n"), {}) == ()
    assert decided(diff("hooks/pre_tool_use.py", "import requests\n"), None) == ()


def test_S7_a_relative_import_is_the_members_own_module_rather_than_a_dependency():
    assert decided(diff("hooks/pre_tool_use.py", "from . import hook_lib\n")) == ()
    assert decided(diff("hooks/pre_tool_use.py", "from ..tools import helper\n")) == ()


def test_S7_a_member_that_only_removes_a_third_party_import_is_not_escalated():
    assert decided(diff("hooks/pre_tool_use.py", "", removed="import requests\n")) == (), (
        "the rule reads what the change adds: dropping a dependency is the change to want"
    )


def test_S7_an_added_file_in_a_declared_member_is_read_for_its_imports():
    reason = decided(diff("hooks/guard.py", "import requests\n", status="added"))[0]
    assert reason.module == "requests"


def test_S7_a_deleted_file_in_a_declared_member_escalates_nothing():
    assert decided(diff("hooks/guard.py", "", removed="import requests\n", status="deleted")) == ()


def test_S7_an_import_inside_a_function_of_a_declared_member_is_still_read():
    text = diff("hooks/pre_tool_use.py", "def send():\n    import http.client\n    return None\n")
    assert decided(text) == (), "`http` is in the standard library however deep the import sits"


def test_S7_a_line_that_mentions_an_import_without_being_one_is_not_read():
    assert (
        decided(diff("hooks/pre_tool_use.py", "# import requests\nnote = 'import requests'\n"))
        == ()
    )
