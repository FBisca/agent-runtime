"""The one reviewer an escalation spawns, and what it is asked to investigate (02-agent-review-deep-review/S1).

A calibrated signal - a value past its threshold, or a confidence below its band - is investigated where it
points: one reviewer is spawned, its scope is focused, and the instruction sets that match the fired rules
join the ones that always do (D12, D14, P2-D3, P2-D5). The report says what the review asked for, and the
canned reviewer records the same request, so a spawn is checked without reaching a model (P2-D1).

Two seams are checked here that a canned reviewer cannot reach: the family's own CLI, replaced by a stub on
PATH that records its argv and stdin, and the run that stops - a nonzero exit, a timeout, or an answer that
is not a review at all - which is an incomplete execution keeping the screening it paid for (D20, R10, S5)
rather than a refusal with no report.
"""

import json
import os
import subprocess
import sys

import pytest
from agent_review import readonly
from agent_review.cli import main

pytestmark = pytest.mark.integration

QUESTION = "repo.migration-safety"

# Each family's read-only, non-interactive run, as its own CLI spells it: the arguments that hold it to reading
# and searching, then the flag that makes it answer once and print. A family the runtime answers for without a
# row here is what this table's assert names, so a fourth family cannot arrive uninvoked.
EXPECTED_ARGV = {
    "claude": [
        "--permission-mode",
        "plan",
        "--allowedTools",
        "Read,Grep,Glob",
        "--disallowedTools",
        "Bash,Edit,Write,NotebookEdit",
        "-p",
    ],
    "pi": ["--tools", "read,grep,find,ls", "--no-mcp", "--no-extensions", "-p"],
}

# What a reviewer answers for a change whose two calibrated signals fired: one disposition each, dismissed with
# the line that answers it, and no finding. A family's CLI prints it as JSON on stdout.
ANSWER = json.dumps(
    {
        "findings": [],
        "dispositions": [
            {
                "signal": "value-above-threshold",
                "question": QUESTION,
                "disposition": "dismissed",
                "evidence": "store.py:2 closes the stream the base revision leaked",
            },
            {
                "signal": "confidence-below-band",
                "question": QUESTION,
                "disposition": "dismissed",
                "evidence": "store.py:2 closes the stream the base revision leaked",
            },
        ],
    }
)

# A stand-in for a family's CLI. It records what it was given before it does anything else, so a run that is
# stopped by a timeout still shows the prompt arriving; env decides how it ends.
STUB = """\
#!{python}
import json, os, sys, time
with open(os.environ["STUB_RECORD"], "w", encoding="utf-8") as stream:
    json.dump({{ "argv": sys.argv[1:], "stdin": sys.stdin.read() }}, stream)
if os.environ.get("STUB_SLEEP"):
    time.sleep(float(os.environ["STUB_SLEEP"]))
if os.environ.get("STUB_EXIT"):
    sys.stderr.write(os.environ.get("STUB_STDERR", ""))
    sys.exit(int(os.environ["STUB_EXIT"]))
sys.stdout.write(os.environ.get("STUB_ANSWER", {answer!r}))
"""

CONFIG = """\
model: jev-latest
sensitive_paths: {sensitive_paths}
thresholds:
  {question}:
    value: 1.5
    confidence: 0.6
deep_review:
  timeout_seconds: {timeout}
  instructions:
    - id: evidence-first
      text: Cite the changed line for every finding you report.
    - id: contract-signal
      text: Read the area of a question whose calibrated number fired, whole.
      applies:
        rules: [value-above-threshold, confidence-below-band]
    - id: payments
      text: Read the payment service with the change in hand.
      applies:
        paths: ["services/payments/**"]
questions:
  - id: {question}
    priority: critical
    type: score
    criteria: [none, minor, moderate, major, severe]
    ask: How severely does the change violate the migration contract
"""

BASE_FILES = {
    "store.py": "def load(path):\n    return open(path).read()\n",
    "docs/guide.md": "guide\n",
}

MIGRATION = "db/migrations/002_index.sql"

HEAD_EDIT = "def load(path):\n    with open(path) as stream:\n        return stream.read()\n"

COMMIT = (
    "-c",
    "user.name=agent-review",
    "-c",
    "user.email=agent-review@example.invalid",
    "-c",
    "commit.gpgsign=false",
    "commit",
    "-m",
)


def git(where, *args):
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr[-2000:]}"
    return result.stdout.strip()


class Spawn:
    """A repository whose head edit is small, a calibrated critical question, and a canned reviewer.

    `sensitive_paths` and `migration` shape a change a safeguard decides rather than the model: the head
    edit then adds a file under the declared path, so the escalation comes from the safeguard list.
    """

    def __init__(self, tmp_path, *, sensitive_paths=(), migration=False):
        self.checkout = tmp_path / "checkout"
        self.checkout.mkdir()
        self.output = tmp_path / "review"
        self.config = tmp_path / "config.yaml"
        self.screening = tmp_path / "screening.json"
        self.reviewer = tmp_path / "reviewer"
        self.reviewer.mkdir()
        self.sensitive_paths = tuple(sensitive_paths)
        self.migration = migration
        self.write_config()
        self.base, self.head = self.two_revisions()

    def write_config(self, *, timeout: float = 600):
        """The configuration this review runs with: one calibrated question and the review's own budget."""
        self.config.write_text(
            CONFIG.format(
                question=QUESTION,
                timeout=timeout,
                sensitive_paths=json.dumps(list(self.sensitive_paths)),
            ),
            encoding="utf-8",
        )
        return self.config

    def two_revisions(self):
        git(self.checkout, "init", "-b", "main")
        for name, text in BASE_FILES.items():
            path = self.checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *COMMIT, "base")
        base = git(self.checkout, "rev-parse", "HEAD")
        (self.checkout / "store.py").write_text(HEAD_EDIT, encoding="utf-8")
        if self.migration:
            added = self.checkout / MIGRATION
            added.parent.mkdir(parents=True, exist_ok=True)
            added.write_text("create index items_id on items (id);\n", encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *COMMIT, "head")
        return base, git(self.checkout, "rev-parse", "HEAD")

    def write_review(self, dispositions):
        """The canned reviewer's answer: the dispositions of the signals this change fired."""
        (self.reviewer / "answers.json").write_text(
            json.dumps({"answers": [{"dispositions": dispositions}]}), encoding="utf-8"
        )
        return self.reviewer

    def write_screening(self, value=2.5, confidence=0.3):
        """The one canned answer the whole bank gets: a score above its threshold, with a low confidence."""
        self.screening.write_text(
            json.dumps(
                {
                    "responses": [
                        {
                            "model": "jev-1.14.0",
                            "answers": {
                                QUESTION: {
                                    "type": "score",
                                    "score": value,
                                    "confidence": confidence,
                                }
                            },
                            "usage": {"input_tokens": 900, "output_tokens": 20},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return self.screening

    def run(self, harness="pi"):
        return main(
            [
                "code",
                "--base",
                self.base,
                "--head",
                self.head,
                "--output",
                str(self.output),
                "--config",
                str(self.config),
                "--screening-fixture",
                str(self.screening),
                "--deep-review",
                harness,
                "--deep-review-fixture",
                str(self.reviewer),
            ]
        )

    def spawn_harness(self, family, monkeypatch, tmp_path):
        """Put a stub named for `family` first on PATH, and name the file it records its argv and stdin to."""
        where = tmp_path / "bin"
        where.mkdir(exist_ok=True)
        stub = where / family
        stub.write_text(STUB.format(python=sys.executable, answer=ANSWER), encoding="utf-8")
        stub.chmod(0o755)
        self.record = tmp_path / "harness.json"
        monkeypatch.setenv("STUB_RECORD", str(self.record))
        monkeypatch.setenv("PATH", f"{where}{os.pathsep}{os.environ['PATH']}")
        return self

    def spawn(self, harness):
        """One invocation that spawns the family's own CLI, with no canned reviewer standing in for it."""
        return main(
            [
                "code",
                "--base",
                self.base,
                "--head",
                self.head,
                "--output",
                str(self.output),
                "--config",
                str(self.config),
                "--screening-fixture",
                str(self.screening),
                "--deep-review",
                harness,
            ]
        )

    def recorded(self):
        assert self.record.is_file(), f"the stub harness recorded nothing at {self.record}"
        return json.loads(self.record.read_text(encoding="utf-8"))

    def report(self):
        path = self.output / "report.json"
        assert path.is_file(), f"no report at {path}: an invocation writes one either way"
        return json.loads(path.read_text(encoding="utf-8"))

    def spawns(self):
        path = self.reviewer / "spawns.json"
        assert path.is_file(), f"the canned reviewer recorded no spawn at {path}"
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def review(tmp_path, monkeypatch):
    where = Spawn(tmp_path)
    monkeypatch.chdir(where.checkout)
    return where


def test_S1_one_reviewer_is_spawned_focused_on_the_signals_with_the_matching_instructions(
    review, capsys
):
    review.write_screening()
    review.write_review(
        [
            {
                "signal": "value-above-threshold",
                "question": QUESTION,
                "disposition": "dismissed",
                "evidence": "store.py:2 closes the stream the base revision leaked",
            },
            {
                "signal": "confidence-below-band",
                "question": QUESTION,
                "disposition": "dismissed",
                "evidence": "store.py:2 closes the stream the base revision leaked",
            },
        ]
    )
    assert review.run() == 0, capsys.readouterr().err
    deep = review.report()["deep_review"]
    assert deep["spawns"] == 1, "one escalation is one spawned reviewer (D19, P2-D3)"
    assert deep["scope"] == "focused", "a calibrated signal is investigated where it points"
    assert deep["capabilities"] == ["read", "search"], (
        "the reviewer reads and searches, nothing else"
    )
    assert deep["instructions"] == ["evidence-first", "contract-signal"], (
        "the set that always joins and the set matching a fired rule; the path set matched no change"
    )
    assert [signal["rule"] for signal in deep["signals"]] == [
        "value-above-threshold",
        "confidence-below-band",
    ], "the flagged areas the reviewer investigates are the signals that fired"
    assert [signal["question"] for signal in deep["signals"]] == [QUESTION, QUESTION]
    spawned = review.spawns()
    assert len(spawned) == 1, "the reviewer was spawned exactly once"
    assert spawned[0]["harness"] == "pi"
    assert spawned[0]["scope"] == deep["scope"]
    assert [entry["id"] for entry in spawned[0]["instructions"]] == deep["instructions"], (
        "the reviewer is asked for what the report says it was asked for"
    )
    assert "deep review: focused" in capsys.readouterr().out, (
        "a person reads that a reviewer was spawned and what scope it was given"
    )


@pytest.mark.parametrize("family", readonly.enforcing())
def test_S1_every_family_the_runtime_holds_receives_the_prompt_whole_and_its_controls(
    family, tmp_path, monkeypatch, capsys
):
    """The reviewer is the family's own CLI: nothing variadic may swallow the prompt, for any family.

    The parametrisation is the runtime's own answer, and its other side - a family that cannot be held - is
    checked by `test_deep_review_capability`, which refuses each such family by name: a family added to
    `profiles.BUILTINS` fails whichever side it lands on, until it is answered.
    """
    assert family in EXPECTED_ARGV, (
        f"no expected invocation for {family}: decide what its own CLI needs to run one question "
        "read-only and print the answer, and add it to EXPECTED_ARGV"
    )
    review = _repository(tmp_path, monkeypatch)
    review.spawn_harness(family, monkeypatch, tmp_path)
    review.write_screening()
    assert review.spawn(family) == 0, capsys.readouterr().err
    recorded = review.recorded()
    assert recorded["argv"] == EXPECTED_ARGV[family], (
        "the family is started with the arguments that hold it to reading and searching, and the flag "
        "that makes it answer once"
    )
    prompt = recorded["stdin"]
    assert prompt.startswith(f"Review the change {review.base}..{review.head}"), (
        "the prompt arrives whole on stdin, where no option of the family's own parsing can reach it"
    )
    assert "with open(path) as stream" in prompt, "the frozen change travels with it"
    assert "cite the changed line" in prompt.lower(), "and the composed instructions"
    assert review.report()["deep_review"]["harness"] == family
    assert review.report()["status"] == "completed", (
        "a reviewer that answered leaves a completed run"
    )


def test_S1_a_reviewer_that_stops_leaves_the_screening_and_records_why(
    tmp_path, monkeypatch, capsys
):
    """A nonzero exit is an incomplete execution: the screening it paid for is kept, not thrown away."""
    review = _repository(tmp_path, monkeypatch)
    review.spawn_harness("pi", monkeypatch, tmp_path)
    monkeypatch.setenv("STUB_EXIT", "3")
    monkeypatch.setenv("STUB_STDERR", "the model refused the change")
    review.write_screening()
    status = review.spawn("pi")
    report = review.report()
    assert status == 1, "a review that stopped is incomplete, not a refusal and not a clean verdict"
    assert report["status"] == "incomplete"
    assert "exited 3" in report["failure"]["reason"]
    assert "the model refused the change" in report["failure"]["reason"]
    assert report["judgments"], "the screening's judgments survive the reviewer's stop"
    assert report["usage"]["input_tokens"] == 900, "and so does what the screening paid for"
    assert [reason["rule"] for reason in report["escalation"]["reasons"]] == [
        "value-above-threshold",
        "confidence-below-band",
    ], "and the escalation that asked for the review"
    assert report["deep_review"]["spawns"] == 1, "one spawn, no retry and no fallback"
    assert "exited 3" in report["deep_review"]["failure"], (
        "the review's own record names why it stopped"
    )
    assert "exited 3" in capsys.readouterr().err, "the terminal names the reviewer's stop"
    assert "with open(path) as stream" in review.recorded()["stdin"], (
        "the reviewer read the prompt and the change before it stopped: the run keeps what it had"
    )


def test_S1_a_reviewer_that_times_out_leaves_the_screening_and_records_why(
    tmp_path, monkeypatch, capsys
):
    """A reviewer given no time to answer is an incomplete execution, with everything it already had."""
    review = _repository(tmp_path, monkeypatch)
    review.spawn_harness("pi", monkeypatch, tmp_path)
    monkeypatch.setenv("STUB_SLEEP", "30")
    review.write_config(timeout=0.2)
    review.write_screening()
    status = review.spawn("pi")
    report = review.report()
    assert status == 1, "a timeout is incomplete, not a refusal"
    assert report["status"] == "incomplete"
    assert "did not answer within" in report["failure"]["reason"]
    assert report["judgments"], "the screening it paid for is kept"
    assert report["usage"]["input_tokens"] == 900
    assert "did not answer within" in report["deep_review"]["failure"], (
        "the review's own record names why it stopped"
    )
    assert "did not answer within" in capsys.readouterr().err, (
        "the terminal names the reviewer's stop"
    )


def test_S5_a_reviewer_that_answers_prose_is_incomplete_naming_what_it_answered(
    tmp_path, monkeypatch, capsys
):
    """A family's stdout is read as a review: text that is not one is a stop, never a clean review."""
    review = _repository(tmp_path, monkeypatch)
    review.spawn_harness("pi", monkeypatch, tmp_path)
    monkeypatch.setenv("STUB_ANSWER", "no findings to report")
    review.write_screening()
    status = review.spawn("pi")
    report = review.report()
    assert status == 1, "an answer no review can read is an incomplete execution (D20, S5)"
    assert report["status"] == "incomplete"
    assert "no findings to report" in report["failure"]["reason"], (
        "the stop carries what the reviewer actually answered"
    )
    assert report["verdict"] is None, "an incomplete review concluded nothing (D34)"
    assert report["judgments"], "the screening it paid for is kept"
    assert report["deep_review"]["spawns"] == 1, "no retry and no model fallback (D20)"
    assert "no findings to report" in capsys.readouterr().err, (
        "the terminal names what the reviewer answered"
    )


def test_S2_a_safeguard_hit_whose_reviewer_stops_still_names_the_rule_the_path_and_the_way_out(
    tmp_path, monkeypatch, capsys
):
    """A safeguard decided the change before the model was asked: the review stopping keeps that reason."""
    review = _repository(
        tmp_path, monkeypatch, sensitive_paths=("db/migrations/**",), migration=True
    )
    review.spawn_harness("pi", monkeypatch, tmp_path)
    monkeypatch.setenv("STUB_EXIT", "3")
    monkeypatch.setenv("STUB_STDERR", "the model refused the change")
    review.write_screening(value=1.0, confidence=0.9)
    status = review.spawn("pi")
    out = capsys.readouterr().out
    assert status == 1, "the review stopped, so the execution did not complete"
    assert "screened nothing" not in out, "a safeguard DID decide the change; nothing was hidden"
    assert "protected-path" in out, "the terminal names the rule that fired"
    assert MIGRATION in out, "and the path it matched"
    assert "a person reads the path" in out, "and the way out R20 promises a person"
    assert "execution: incomplete" in out
    report = review.report()
    assert report["status"] == "incomplete"
    assert [reason["rule"] for reason in report["escalation"]["reasons"]] == ["protected-path"]
    assert report["deep_review"]["scope"] == "full", "a safeguard is read whole"
    assert "exited 3" in report["failure"]["reason"]


def test_S1_a_threshold_escalation_whose_reviewer_stops_keeps_its_judgment_on_the_terminal(
    tmp_path, monkeypatch, capsys
):
    """The screening answered and paid for a judgment: a stopped review cannot un-say that."""
    review = _repository(tmp_path, monkeypatch)
    review.spawn_harness("pi", monkeypatch, tmp_path)
    monkeypatch.setenv("STUB_EXIT", "3")
    monkeypatch.setenv("STUB_STDERR", "the model refused the change")
    review.write_screening()
    status = review.spawn("pi")
    out = capsys.readouterr().out
    assert status == 1
    assert "screened nothing" not in out, (
        "the screening did answer; the summary may not say it did not"
    )
    assert "escalated" in out
    assert "value-above-threshold" in out, "the rule that fired is what a person acts on"
    assert QUESTION in out and "2.5" in out, "the judgment the screening paid for is kept"
    assert "usage: 1 request, 900 input tokens" in out, "and so is what it cost"
    assert "execution: incomplete" in out
    assert "review stopped" in out and "exited 3" in out, (
        "the terminal says the review stopped and why"
    )
    assert "signals read by" not in out, "the reviewer never answered, so nothing was read"
    assert "did not answer" in out, "the provenance reports the review's own stop"


def test_a_fixture_directory_the_run_cannot_write_refuses_rather_than_recording_a_review(
    tmp_path, monkeypatch, capsys
):
    """The caller's fixture is input, not a reviewer: a broken one refuses instead of claiming a review."""
    review = _repository(tmp_path, monkeypatch)
    review.write_screening()
    review.reviewer.chmod(0o500)
    try:
        status = review.run()
    finally:
        review.reviewer.chmod(0o700)
    err = capsys.readouterr().err
    assert status == 2, "an unwritable fixture is a refusal, not an incomplete review"
    assert str(review.reviewer) in err, "the refusal names the directory the caller gave"
    assert "cannot record the deep review" in err
    assert not (review.output / "report.json").is_file(), (
        "a refusal writes no report to be read as a review that happened"
    )


def test_a_fixture_that_holds_no_answer_refuses_naming_the_file_it_reads(
    tmp_path, monkeypatch, capsys
):
    """A directory the run cannot be answered from is the caller's input: it refuses before a review is claimed."""
    review = _repository(tmp_path, monkeypatch)
    review.write_screening()
    status = review.run()
    err = capsys.readouterr().err
    assert status == 2, (
        "a fixture with nothing to answer from is a refusal, not an incomplete review"
    )
    assert "answers.json" in err, "the refusal names the file a canned reviewer answers from"
    assert str(review.reviewer) in err, "and the directory the caller gave"
    assert not (review.output / "report.json").is_file(), (
        "a refusal writes no report to be read as a review that happened"
    )


def _repository(tmp_path, monkeypatch, **options):
    """A scratch repository whose head edit fires the escalation under test, at its checkout."""
    where = Spawn(tmp_path, **options)
    monkeypatch.chdir(where.checkout)
    return where
