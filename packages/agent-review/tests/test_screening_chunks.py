"""Chunks: a change split inside the budget in force, every changed region carried by exactly one chunk.

01-agent-review-smart-screening/S3, S4 (D41, D42, D43). The splitter reads the frozen diff and the budget
the configuration declares or the provider's limit puts in force; it never summarises, never consults the
model and never splits a hunk across two chunks, because a region carried by two chunks is a region whose
answer no chunk is about. A hunk larger than the budget is carried whole and says so. The coverage a
screening claims is counted from the chunks themselves, and a change wider than the cross-file limit
records the gap a person must read as a reason rather than as a clean screen.
"""

import textwrap

import pytest
from agent_review.change import read
from agent_review.chunks import budget, coverage, cross_file_limit, split
from agent_review.questions import QuestionBank
from agent_review.safeguards import COVERAGE_GAP, recommends_full_review

pytestmark = pytest.mark.unit

HUNK = "@@ -1,2 +1,{added} @@"


def _file(path, added, old_path=None):
    """One modified file in a git diff: two context lines, and `added` lines the change writes."""
    old = old_path or path
    lines = [
        f"diff --git a/{old} b/{path}",
        "index 1111111..2222222 100644",
        f"--- a/{old}",
        f"+++ b/{path}",
        HUNK.format(added=2 + added),
        " one",
        " two",
    ]
    lines.extend(f"+line {number} of {path}" for number in range(added))
    return "\n".join(lines)


def _change(*files):
    """The change `files` describe, and the diff text it was read from."""
    text = "\n".join(files) + "\n"
    return read(text), text


def _bank(**settings):
    return QuestionBank(model="test-model", questions=(), **settings)


def _regions(chunks):
    """Every region the chunks carry, as the (path, hunk start) pairs the diff spells."""
    return [
        (region.path, None if region.hunk is None else region.hunk.new_start)
        for chunk in chunks
        for region in chunk.regions
    ]


def _hunks(change):
    """Every hunk the change holds, as the same (path, hunk start) pairs."""
    return [(file.path, hunk.new_start) for file in change.files for hunk in file.hunks]


def test_S3_a_change_larger_than_the_budget_is_split_with_every_region_carried_once():
    change, text = _change(_file("alpha.py", 3), _file("beta.py", 3), _file("gamma.py", 3))

    chunks = split(change, text, budget=120)

    assert len(chunks) > 1, "a change larger than the budget is chunked rather than sent whole"
    assert _regions(chunks) == _hunks(change), (
        "every changed region is carried by exactly one chunk, in the diff's own order (D41)"
    )
    for chunk in chunks:
        if chunk.within_budget:
            assert chunk.tokens <= 120
        assert all(f"diff --git a/{path}" in chunk.text for path in chunk.files), (
            "a chunk names the file each of its hunks belongs to"
        )


def test_S3_the_same_frozen_change_chunks_the_same_way():
    change, text = _change(_file("alpha.py", 3), _file("beta.py", 3))
    other, other_text = _change(_file("alpha.py", 3), _file("beta.py", 3))

    first = split(change, text, budget=120)
    assert first == split(other, other_text, budget=120), (
        "the same frozen change and configuration chunk the same way (D41)"
    )


def test_S3_a_change_that_fits_the_budget_is_one_chunk_carrying_all_of_it():
    change, text = _change(_file("alpha.py", 3), _file("beta.py", 3))

    chunks = split(change, text, budget=100_000)

    assert len(chunks) == 1
    assert chunks[0].files == ("alpha.py", "beta.py")
    assert chunks[0].within_budget
    assert chunks[0].text == text, "the whole diff travels whole when it fits (D41)"


def test_S3_a_budget_too_small_for_any_region_gives_each_region_its_own_chunk():
    change, text = _change(_file("alpha.py", 8), _file("beta.py", 8))

    chunks = split(change, text, budget=1)

    assert len(chunks) == len(_hunks(change)) == 2
    assert [chunk.files for chunk in chunks] == [("alpha.py",), ("beta.py",)]


def test_S3_a_hunk_larger_than_the_budget_is_carried_whole_and_says_so():
    change, text = _change(_file("pipeline.py", 40))

    chunks = split(change, text, budget=10)

    assert len(chunks) == 1, "a region is never split across two chunks: it would belong to neither"
    assert not chunks[0].within_budget, "the report never claims a chunk fits when it does not"
    assert "line 39 of pipeline.py" in chunks[0].text, "the oversized hunk travels whole"


def test_S3_a_rename_without_a_hunk_is_carried_so_the_change_is_read_as_a_rename():
    text = textwrap.dedent(
        """\
        diff --git a/docs/guide.md b/server/guide.md
        similarity index 100%
        rename from docs/guide.md
        rename to server/guide.md
        """
    )
    change = read(text)
    assert change.files[0].hunks == ()

    chunks = split(change, text, budget=100_000)

    assert chunks[0].files == ("server/guide.md",)
    assert "rename to server/guide.md" in chunks[0].text


def test_S3_the_budget_in_force_is_the_declaration_or_the_providers_own_limit():
    assert budget(_bank(), long_context=False) == 32_000, (
        "the measured binding default is 32k tokens"
    )
    assert budget(_bank(), long_context=True) == 262_000, (
        "long context is the caller's larger budget"
    )
    assert budget(_bank(chunk={"max_diff_tokens": 400}), long_context=True) == 400, (
        "a configured budget is the budget in force"
    )


def test_S3_coverage_counts_each_changed_region_once_inside_the_cross_file_limit():
    change, text = _change(_file("alpha.py", 3), _file("beta.py", 3))
    chunks = split(change, text, budget=1)

    covered = coverage(change, chunks, _bank())

    assert covered.changed_regions == covered.covered == 2
    assert covered.gap is None, "two files are inside the cross-file limit the baseline sets"


def test_S4_a_change_wider_than_the_cross_file_limit_records_the_gap_as_a_full_review_reason():
    change, text = _change(_file("alpha.py", 1), _file("beta.py", 1))
    chunks = split(change, text, budget=100_000)

    covered = coverage(change, chunks, _bank(cross_file={"max_files": 1}))

    assert covered.gap is not None
    assert covered.gap.changed_files == 2 and covered.gap.max_files == 1
    assert "cross_file.max_files" in covered.gap.reason
    reason = covered.gap.as_escalation()
    assert reason.rule == COVERAGE_GAP
    assert "cross_file.max_files=1" in reason.pattern
    assert recommends_full_review((reason,)), (
        "a coverage gap sends the change to a full deep review (D43)"
    )
    assert cross_file_limit(_bank(cross_file={"max_files": 1})) == 1
    assert cross_file_limit(_bank()) == 40, "the packaged default reads a wide but ordinary change"


def test_S4_a_change_inside_the_cross_file_limit_records_no_gap():
    change, text = _change(_file("alpha.py", 1), _file("beta.py", 1))
    chunks = split(change, text, budget=100_000)

    covered = coverage(change, chunks, _bank(cross_file={"max_files": 2}))

    assert covered.gap is None, "a change at the limit is not wider than it"
    assert not recommends_full_review(())
