"""The deterministic safeguards: the change read as paths and hunks, and the rules decided from it.

00c-agent-review-safeguards/S1, S2 (SG-D2, SG-D3, SG-D5): a delete or overwrite on a target the change
computes, with nothing in the change confining it, is one `target-outside-root` reason naming the rule and
the changed path - and a change that confines the target, or adds no destructive call at all, carries no
reason. A rule proves that nothing confines the target, never that the target is outside the root: the
value a computed target holds is not in the diff (SG-D5). These are in-process tests over diff text, so the
rules are decided without a provider, a repository or a request.
"""

import difflib
import textwrap

import pytest
from agent_review import safeguards
from agent_review.change import ADDED, read

pytestmark = pytest.mark.unit

SOURCE = "packages/agent-orchestrator/src/agent_orchestrator/driver.py"

BEFORE = textwrap.dedent(
    """\
    class Driver:
        def __init__(self, plan_path):
            self.plan_path = plan_path

        def read_plan(self):
            return self.plan_path
    """
)

UNGUARDED = textwrap.dedent(
    """\
        def discard_plan(self, plan):
            folder = self.plan_path.parent
            shutil.rmtree(folder)
            self.discarded = True
    """
)

GUARDED = textwrap.dedent(
    """\
        def discard_plan(self, plan):
            folder = self.plan_path.parent
            if not folder.is_relative_to(self.root):
                raise ValueError("refusing to delete %s outside %s" % (folder, self.root))
            shutil.rmtree(folder)
    """
)

GUARD_ADDED_BESIDE_THE_CALL = textwrap.dedent(
    """\
        def directly_under(self, folder, plans_dir):
            return folder.resolve().parent == Path(plans_dir).resolve()

        def guard_plan_folder(self, folder):
            if not self.directly_under(folder, self.plans_dir):
                raise ValueError("a plan folder outside plans.dir is not deleted")

        def discard_plan(self, plan):
            folder = self.layout.folder
            self.guard_plan_folder(folder)
            shutil.rmtree(folder)
    """
)

# The guard the change adds confines `self.folder`, and the delete is of a target nothing confines: the guard
# is called with nothing, so it confines no target the delete reads.
GUARD_THAT_CONFINES_ANOTHER_TARGET = textwrap.dedent(
    """\
        def directly_under(self, plans_dir):
            folder = self.folder
            return folder.resolve().parent == Path(plans_dir).resolve()

        def guard_plan_folder(self):
            if not self.directly_under(self.plans_dir):
                raise ValueError("a plan folder outside plans.dir is not deleted")

        def discard_plan(self, plan):
            self.guard_plan_folder()
            folder = self.plan_path.parent
            shutil.rmtree(folder)
    """
)

# The change's only containment test is over another relationship, in the delete's own function.
CONFINEMENT_OVER_ANOTHER_RELATIONSHIP = textwrap.dedent(
    """\
        def discard_plan(self, plan):
            kept = self.other.relative_to(self.repo)
            folder = self.plan_path.parent
            shutil.rmtree(folder)
    """
)

# One containment test over `self.keep`, and a delete of each of two computed targets.
ONE_CONFINEMENT_TWO_TARGETS = textwrap.dedent(
    """\
        def clean(self):
            if not self.keep.is_relative_to(self.root):
                raise ValueError("refusing to delete %s outside %s" % (self.keep, self.root))
            shutil.rmtree(self.keep)
            shutil.rmtree(self.other)
    """
)

# The resolved spelling SG-D5 names: the test is on the target's resolved self.
RESOLVED_CONTAINMENT = textwrap.dedent(
    """\
        def discard_plan(self, plan, root):
            target = self.plan_path.parent
            if not target.resolve().is_relative_to(root):
                raise ValueError("refusing to delete %s outside %s" % (target, root))
            shutil.rmtree(target)
    """
)

# The defect `429625b` shipped, in its own words: the target is computed from the plan path in one method,
# and the change's only containment test (`folder.relative_to(self.repo)`) is in a different method, over a
# different relationship (the folder is inside the repository, not the target inside a root).
# The test helper the defect shipped beside (`429625b`): it unlinks the plan file and then writes it again,
# so the delete reaches nothing the change did not put there.
FIXTURE_THAT_RECREATES_ITS_TARGET = textwrap.dedent(
    """\
        def folder_plan(self, data):
            self.plan.unlink()
            self.folder = self.plans / "demo"
            self.plan = self.folder / "plan.yaml"
            self.plan.write_text(yaml.safe_dump(data), encoding="utf-8")
    """
)

THE_DEFECT_AS_IT_SHIPPED = textwrap.dedent(
    """\
        def plan_folder_rel(self):
            folder = self.plan_path.parent
            return folder.relative_to(self.repo).as_posix()

        def discard_plan(self, plan):
            folder = self.plan_path.parent
            shutil.rmtree(folder)
            self.discarded = True
    """
)


def unified(before, after, path=SOURCE, status=None):
    """One file's change as `git diff` writes it: the header, then the hunks between `before` and `after`."""
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


def grown(added, before=BEFORE):
    """`BEFORE` with `added`, dedented, added as a method of its class."""
    return before + textwrap.indent(textwrap.dedent(added), "    ")


def reasons_for(added, before=BEFORE):
    """The reasons one added method produces, as (rule, path, pattern) triples."""
    change = read(unified(before, grown(added, before)))
    return [(reason.rule, reason.path, reason.pattern) for reason in safeguards.decide(change)]


class TheChangeTests:
    """The diff read as structure: its paths, its hunks and the lines it adds."""


def test_the_change_names_both_revisions_paths_and_what_it_adds():
    change = read(unified(BEFORE, grown(UNGUARDED)))
    assert [file.path for file in change.files] == [SOURCE]
    assert change.files[0].status == "modified"
    added = [
        line.text for hunk in change.files[0].hunks for line in hunk.lines if line.kind == ADDED
    ]
    assert "        shutil.rmtree(folder)" in added
    assert "        def read_plan(self):" not in added, (
        "a context line is not something the change adds"
    )


def test_a_hunk_knows_the_function_its_lines_sit_in():
    change = read(unified(BEFORE, grown(UNGUARDED)))
    hunk = change.files[0].hunks[-1]
    index = next(
        index for index, line in enumerate(hunk.lines) if "shutil.rmtree(folder)" in line.text
    )
    region = [line.text for line in hunk.region(index)]
    assert "    def discard_plan(self, plan):" in region
    assert "    def read_plan(self):" not in region, "a sibling method is not the same region"


def test_a_new_file_is_read_as_added_and_a_deleted_one_as_deleted():
    added = read(unified("", "value = 1\n", path="new.py", status="new"))
    assert (added.files[0].status, added.files[0].path) == ("added", "new.py")
    deleted = read(unified("value = 1\n", "", path="gone.py", status="deleted"))
    assert (deleted.files[0].status, deleted.files[0].path) == ("deleted", "gone.py")


def test_a_rename_keeps_both_of_its_paths():
    change = read(
        "diff --git a/old.py b/new.py\n"
        "similarity index 100%\n"
        "rename from old.py\n"
        "rename to new.py\n"
    )
    assert change.files[0].status == "renamed"
    assert (change.files[0].old_path, change.files[0].path) == ("old.py", "new.py")


def test_S1_a_computed_delete_with_no_confinement_escalates_as_target_outside_root():
    reasons = safeguards.decide(read(unified(BEFORE, grown(UNGUARDED))))
    assert [reason.rule for reason in reasons] == [safeguards.TARGET_OUTSIDE_ROOT]
    assert reasons[0].path == SOURCE
    assert reasons[0].source == safeguards.DIFF, "the evidence is the change itself"
    assert "rmtree" in reasons[0].pattern, "the reason names the call a person must read"


def test_S1_the_defect_as_it_shipped_escalates_once_for_its_path():
    reasons = safeguards.decide(read(unified(BEFORE, grown(THE_DEFECT_AS_IT_SHIPPED))))
    assert [reason.rule for reason in reasons] == [safeguards.TARGET_OUTSIDE_ROOT], (
        "the change's own containment test confines the folder to the repository, not the target to a "
        "root, so it does not confine the delete"
    )
    assert reasons[0].path == SOURCE


def test_S1_the_defect_escalates_once_for_its_path_in_a_change_that_also_has_a_test_helper():
    """The defect as it shipped, beside the test helper that recreates the file it unlinks."""
    change = read(
        unified(BEFORE, grown(THE_DEFECT_AS_IT_SHIPPED))
        + unified(
            BEFORE,
            grown(FIXTURE_THAT_RECREATES_ITS_TARGET),
            path="packages/agent-orchestrator/tests/test_archive_modes.py",
        )
    )
    reasons = safeguards.decide(change)
    assert [reason.path for reason in reasons] == [SOURCE], (
        "the defect fires once; a helper that unlinks a plan file it writes itself deletes nothing the "
        "change did not put there"
    )


def test_S2_a_computed_delete_confined_in_the_same_change_is_not_escalated():
    assert reasons_for(GUARDED) == [], "the target is confined before it is deleted"


def test_S2_a_confinement_factored_into_a_guard_the_change_adds_is_not_escalated():
    assert reasons_for(GUARD_ADDED_BESIDE_THE_CALL) == [], (
        "the target is handed to a guard the change adds, which confines it"
    )


def test_S2_a_containment_test_on_the_resolved_target_is_not_escalated():
    assert reasons_for(RESOLVED_CONTAINMENT) == [], (
        "`target.resolve().is_relative_to(root)` is the containment test SG-D5 names"
    )


def test_S1_confinement_over_another_relationship_leaves_the_delete_unconfined():
    reasons = reasons_for(CONFINEMENT_OVER_ANOTHER_RELATIONSHIP)
    assert [rule for rule, _, _ in reasons] == [safeguards.TARGET_OUTSIDE_ROOT], (
        "a test over another relationship in the delete's own function confines nothing about the target"
    )


def test_S1_one_confinement_test_confines_only_the_target_it_names():
    reasons = reasons_for(ONE_CONFINEMENT_TWO_TARGETS)
    assert len(reasons) == 1, "one of the two deletes is confined and the other is not"
    assert reasons[0][0] == safeguards.TARGET_OUTSIDE_ROOT
    assert "self.other" in reasons[0][2], (
        "confinement is per target: the test over self.keep does not answer for self.other"
    )


def test_S1_a_guard_confines_only_the_target_it_is_handed():
    reasons = reasons_for(GUARD_THAT_CONFINES_ANOTHER_TARGET)
    assert [rule for rule, _, _ in reasons] == [safeguards.TARGET_OUTSIDE_ROOT], (
        "the guard is called with nothing, so no delete of a computed target is confined by it"
    )


def test_a_delete_of_a_target_the_change_writes_itself_is_the_changes_own_artifact():
    assert reasons_for(FIXTURE_THAT_RECREATES_ITS_TARGET) == [], (
        "the helper unlinks the plan file it writes in the same function: what it deletes is its own"
    )


def test_a_delete_of_what_the_change_writes_itself_is_not_decided_on_the_os_module():
    assert (
        reasons_for(
            textwrap.dedent(
                """\
                def reset(self):
                    self.plan.write_text("x", encoding="utf-8")
                    os.unlink(self.plan)
            """
            )
        )
        == []
    ), "os.unlink is one delete of a file the same function writes: the change's own artifact"
    assert (
        reasons_for(
            textwrap.dedent(
                """\
                def reset(self):
                    self.directory.mkdir(parents=True)
                    os.rmdir(self.directory)
            """
            )
        )
        == []
    ), "os.rmdir is one delete of a directory the same function creates"


def test_one_deletion_is_one_call_however_the_os_module_spells_it():
    assert len(safeguards.destructive_calls("os.unlink(self.plan)")) == 1
    assert len(safeguards.destructive_calls("os.rmdir(self.directory)")) == 1


def test_S2_a_change_with_no_destructive_call_is_not_escalated():
    assert (
        reasons_for(
            textwrap.dedent(
                """\
                def load(self, path):
                    try:
                        return open(path).read()
                    except OSError:
                        return None
            """
            )
        )
        == []
    )


def test_a_literal_target_that_climbs_out_of_the_root_escalates():
    reasons = reasons_for(
        textwrap.dedent(
            """\
                def discard_plan(self):
                    shutil.rmtree("../archive")
            """
        )
    )
    assert [rule for rule, _, _ in reasons] == [safeguards.TARGET_OUTSIDE_ROOT]


def test_a_literal_target_inside_the_root_needs_no_confinement():
    assert (
        reasons_for(
            textwrap.dedent(
                """\
                    def discard_plan(self):
                        shutil.rmtree("build/cache")
                """
            )
        )
        == []
    ), "a target the change spells out inside the root says where it reaches"


def test_a_write_on_a_computed_target_is_not_decided_from_a_diff():
    assert (
        reasons_for(
            textwrap.dedent(
                """\
                    def save(self, name):
                        target = self.output / name
                        target.write_text(self.render())
                """
            )
        )
        == []
    ), (
        "whether a write destroys anything depends on what the target already held, which a diff does not "
        "show: the change that fixed this rule's own case writes a computed path (109c98b)"
    )


def test_a_read_is_not_destructive():
    assert (
        reasons_for(
            textwrap.dedent(
                """\
                    def load(self, path):
                        self.target = self.output / path
                        return open(self.target).read()
                """
            )
        )
        == []
    ), "open() without a writing mode reads; it never deletes anything"


def test_a_way_out_is_offered_for_every_rule():
    reason = safeguards.EscalationReason(
        rule=safeguards.TARGET_OUTSIDE_ROOT,
        path=SOURCE,
        pattern="shutil.rmtree(folder)",
        source=safeguards.DIFF,
    )
    way_out = safeguards.way_out(reason)
    assert SOURCE in way_out, "the way out names the file a person must read"
    assert "is_relative_to" in way_out, "the way out names the check that would confine the target"
