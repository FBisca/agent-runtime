"""The prompts the driver sends an agent live in `prompts/*.md`; this module loads and fills them.

A template is Markdown with `$name` placeholders and no logic, filled by `string.Template.substitute`, so a
placeholder the caller did not supply fails loudly. Python builds each conditional or list block into one value:
a block's own text is a fragment file beside the brief that uses it, and a fragment's value is its file without
the final newline.

One rule keeps optional blocks out of the templates: a placeholder alone on its line, whose value is empty, takes
its whole line with it. A block that opens or closes a paragraph carries that blank line in its own text.
Tests snapshot every brief, so a prompt is reviewed as a diff of `prompts/` and `tests/snapshots/`.
"""

import re
from importlib.resources import files
from string import Template


def _text(name):
    return (
        files("agent_orchestrator.driver")
        .joinpath("prompts", name + ".md")
        .read_text(encoding="utf-8")
    )


def render(name, **values):
    """The brief `prompts/<name>.md` with its placeholders filled; every placeholder must have a value."""
    text = _text(name)
    values = {key: str(value) for key, value in values.items()}
    for key in [key for key, value in values.items() if value == ""]:
        text = re.sub(r"^\$%s\n" % re.escape(key), "", text, flags=re.MULTILINE)
    return Template(text).substitute(values)


def block(name, **values):
    """A fragment's text, to be one placeholder's value in a brief: its file without the final newline."""
    return render(name, **values).removesuffix("\n")


def bullets(lines):
    """One `- ` line per entry."""
    return "\n".join("- " + line for line in lines)
