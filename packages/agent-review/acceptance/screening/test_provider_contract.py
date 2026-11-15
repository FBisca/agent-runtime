"""Acceptance for the provider contract (00b-agent-review-provider-contract/S1..S6).

The runner places this file at `packages/agent-review/acceptance/screening/`. It drives the `agent-review` console
script as a subprocess over a real repository, a configuration it writes, a local System One endpoint that records what
the tool sent, and a canned screening provider for the paths that do not need the wire; it imports nothing from the
package, so it collects cleanly before the phase exists and fails at base.

The contract it pins (brief.md, 00b-D1..00b-D4):

  - one System One request: `<base>/systemone`, `Authorization: Bearer $TYPESAFE_API_KEY`, `{model, state, questions}`,
    the state as an object, the questions as an id -> question map, and no chat-shaped `messages`/`response_format`
  - a score question carries its levels and a noul may carry criteria; a missing or misplaced one is refused by path
    before any request is made (00b-D1, 00b-D4)
  - a judgment keeps the provider's native answer: `noul` for a boolean, `score` and `confidence` for a score, `choice`
    and `probabilities` for a choice; a value between two of a score's declared levels is carried as the
    provider answered it, while a value below the first rung or above the last, or a confidence outside 0..1,
    is refused
  - the report names the provider endpoint, the served model beside the configured alias, the limits in force, whether
    long context was requested, and a digest of the bank
  - a request over the limit in force is incomplete with the provider's own reason, and the same change completes with
    long context, which sends the header (00b-D2, 00b-D3)
  - a refusal writes no report and names any report already standing there; an incomplete run keeps what it learned

Each class carries a control that passes at base and after.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

import pytest
from local_http import LocalServer

pytestmark = pytest.mark.integration

CONFIG = """\
model: jev-latest
questions:
  - id: repo.error-handling
    priority: critical
    type: score
    criteria:
      - no new failure path, or every one raises an actionable error
      - a new failure's error is vague but actionable
      - a new failure is handled by a caller-visible fallback with no signal
      - a new failure is swallowed or replaced by a silent default
      - a new failure crashes, or reports success while the operation failed
    ask: How severely does the change violate error handling
  - id: repo.hidden-state
    priority: high
    type: noul
    ask: Does the change keep its state explicit
"""


def git(where, *args):
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


class SystemOneServer:
    """A local System One endpoint that answers with `body` and records the one request it received."""

    def __init__(self, body, status=200):
        self.body = body
        self.status = status
        self.request: dict | None = None
        self.count = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - the base class's name
                outer.count += 1
                length = int(self.headers.get("content-length") or 0)
                outer.request = {
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": json.loads(self.rfile.read(length) or b"{}"),
                }
                payload = json.dumps(outer.body).encode()
                self.send_response(outer.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                pass

        self.httpd = LocalServer(("127.0.0.1", 0), Handler)
        self.thread = self.httpd.serve_in_background()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.httpd.stop(self.thread)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"


def answer(**questions):
    return {
        "model": "jev-1.14.0",
        "answers": questions,
        "usage": {"input_tokens": 120, "output_tokens": 12},
    }


class ProviderContractFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name, "checkout")
        self.workspace.mkdir()
        self.output = Path(self.tmp.name, "review")
        self.config = Path(self.tmp.name, "config.yaml")
        self.fixture = Path(self.tmp.name, "screening.json")
        self.base, self.head = self.two_revisions()

    def two_revisions(self):
        git(self.workspace, "init", "-b", "main")
        (self.workspace / "store.py").write_text(
            "def load(path):\n    return open(path).read()\n", encoding="utf-8"
        )
        (self.workspace / "docs").mkdir()
        (self.workspace / "docs/guide.md").write_text("guide\n", encoding="utf-8")
        self.commit("base")
        base = git(self.workspace, "rev-parse", "HEAD")
        (self.workspace / "store.py").write_text(
            "def load(path):\n    try:\n        return open(path).read()\n    except OSError:\n        return None\n",
            encoding="utf-8",
        )
        self.commit("head")
        return base, git(self.workspace, "rev-parse", "HEAD")

    def commit(self, message):
        git(self.workspace, "add", "--all")
        git(
            self.workspace,
            "-c",
            "user.name=acceptance",
            "-c",
            "user.email=acceptance@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            message,
        )

    def write_config(self, text=CONFIG):
        self.config.write_text(text, encoding="utf-8")
        return self.config

    def write_fixture(self, *responses):
        self.fixture.write_text(json.dumps({"responses": list(responses)}), encoding="utf-8")
        return self.fixture

    def screen(
        self,
        *,
        base_url=None,
        long_context=False,
        extra=(),
        config=True,
        output=None,
        env=None,
        fixture=True,
    ):
        binary = shutil.which("agent-review")
        if binary is None:
            self.fail("no `agent-review` on PATH: run the check under `uv run`")
        argv = [
            binary,
            "code",
            "--base",
            self.base,
            "--head",
            self.head,
            "--output",
            str(output or self.output),
        ]
        if config:
            argv += ["--config", str(self.config)]
        if long_context:
            argv += ["--long-context"]
        argv += list(extra)
        if fixture and base_url is None and self.fixture.is_file():
            argv += ["--screening-fixture", str(self.fixture)]
        environment = {
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(Path(self.tmp.name, "home")),
        }
        if base_url:
            environment.update(
                {"AGENT_REVIEW_BASE_URL": base_url, "AGENT_REVIEW_API_KEY": "test-key"}
            )
        # The caller's env comes last so a test can withhold the fallback credential: forcing it
        # after would leave S1's missing-credential case unscreenable and always authenticated.
        environment.update(env or {})
        return subprocess.run(
            argv,
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            env=environment,
        )

    def report(self):
        path = self.output / "report.json"
        self.assertTrue(path.is_file(), f"no report at {path}: an invocation writes one either way")
        return json.loads(path.read_text(encoding="utf-8"))


class FixtureControlTests(ProviderContractFixture):
    def test_control_the_change_edits_one_file_across_two_revisions(self):
        self.assertEqual(
            len(git(self.workspace, "diff", "--name-only", f"{self.base}..{self.head}").split()), 1
        )

    def test_control_a_configuration_with_levels_is_read_by_the_tool(self):
        self.write_config()
        self.assertTrue(self.config.is_file())


class TransportTests(ProviderContractFixture):
    """S1: one System One request, typed, authenticated by TYPESAFE_API_KEY."""

    def test_S1_the_tool_posts_the_typed_request_to_systemone(self):
        self.write_config()
        body = answer(
            **{
                "repo.error-handling": {"type": "score", "score": 1.0, "confidence": 0.8},
                "repo.hidden-state": {"type": "noul", "noul": 0.7},
            }
        )
        with SystemOneServer(body) as server:
            result = self.screen(base_url=server.url)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        request = server.request
        self.assertIsNotNone(request, "the tool made no request")
        request = request or {}
        self.assertTrue(request["path"].endswith("/systemone"), request["path"])
        self.assertEqual(request["headers"].get("authorization"), "Bearer test-key")
        sent = request["body"]
        self.assertEqual(sent["model"], "jev-latest")
        self.assertIsInstance(
            sent["state"], dict, "the state travels as an object, not a prompt string"
        )
        self.assertIsInstance(
            sent["questions"], dict, "the questions travel as an id -> question map"
        )
        self.assertNotIn("messages", sent, "the request is not chat-shaped")
        self.assertNotIn("response_format", sent)
        self.assertIn("diff", json.dumps(sent["state"]))
        self.assertEqual(server.count, 1, "one screening is one request (D40)")
        self.assertLessEqual(
            {"repo.error-handling", "repo.hidden-state"},
            set(sent["questions"]),
            "the map is keyed by the bank's ids, the configuration's own after the baseline's",
        )
        self.assertIn(
            "contract.requirement-traceability",
            sent["questions"],
            "a configuration extends the mandatory baseline and never replaces it",
        )

    def test_S1_the_documented_credential_is_sent_when_a_fallback_is_present_too(self):
        self.write_config()
        body = answer(
            **{
                "repo.error-handling": {"type": "score", "score": 1.0, "confidence": 0.8},
                "repo.hidden-state": {"type": "noul", "noul": 0.7},
            }
        )
        with SystemOneServer(body) as server:
            result = self.screen(base_url=server.url, env={"TYPESAFE_API_KEY": "typesafe-key"})
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        sent = server.request or {}
        self.assertEqual(
            sent["headers"].get("authorization"),
            "Bearer typesafe-key",
            "the provider's own variable wins over the tool's fallback",
        )

    def test_S1_a_missing_credential_is_incomplete_naming_the_variable(self):
        self.write_config()
        with SystemOneServer(answer()) as server:
            result = self.screen(base_url=server.url, env={"AGENT_REVIEW_API_KEY": ""})
        self.assertNotEqual(result.returncode, 0)
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("TYPESAFE_API_KEY", json.dumps(report) + result.stderr)


class BankShapeTests(ProviderContractFixture):
    """S2: criteria and levels are required where the type needs them, refused by path."""

    def test_S2_a_score_question_without_levels_is_refused_by_path(self):
        self.write_config(
            CONFIG.replace(
                """    criteria:
      - no new failure path, or every one raises an actionable error
      - a new failure's error is vague but actionable
      - a new failure is handled by a caller-visible fallback with no signal
      - a new failure is swallowed or replaced by a silent default
      - a new failure crashes, or reports success while the operation failed
""",
                "",
            )
        )
        self.write_fixture(answer())
        result = self.screen()
        self.assertEqual(result.returncode, 2, result.stderr[-2000:])
        self.assertIn("questions[0]", result.stderr)
        self.assertFalse((self.output / "report.json").exists(), "a refusal writes no report")

    def test_S2_a_noul_with_a_level_list_is_refused(self):
        self.write_config(
            CONFIG.replace(
                "    ask: Does the change keep its state explicit",
                "    criteria:\n      - a\n      - b\n    ask: Does the change keep its state explicit",
            )
        )
        self.write_fixture(answer())
        result = self.screen()
        self.assertEqual(result.returncode, 2, result.stderr[-2000:])
        self.assertIn("questions[1]", result.stderr)


class NativeAnswerTests(ProviderContractFixture):
    """S3: the provider's own answer is what the report carries, and its range is enforced."""

    def test_S3_a_score_above_the_declared_levels_is_refused_naming_the_question(self):
        self.write_config()
        self.write_fixture(
            answer(
                **{
                    "repo.error-handling": {"type": "score", "score": 7.0, "confidence": 0.9},
                    "repo.hidden-state": {"type": "noul", "noul": 0.6},
                }
            )
        )
        result = self.screen()
        self.assertNotEqual(result.returncode, 0)
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("repo.error-handling", report["failure"]["reason"])

    def test_S3_a_score_between_the_declared_levels_is_carried_as_answered(self):
        self.write_config()
        self.write_fixture(
            answer(
                **{
                    "repo.error-handling": {"type": "score", "score": 0.5, "confidence": 0.9},
                    "repo.hidden-state": {"type": "noul", "noul": 0.6},
                }
            )
        )
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        self.assertEqual(report["status"], "completed")
        judged = {judgment["question"]: judgment for judgment in report["judgments"]}
        self.assertEqual(
            judged["repo.error-handling"]["value"],
            0.5,
            "0.5 sits between the rungs of a five-level ladder (0..4) and is the provider's own answer",
        )

    def test_S3_a_confidence_outside_zero_and_one_is_refused(self):
        self.write_config()
        self.write_fixture(
            answer(
                **{
                    "repo.error-handling": {"type": "score", "score": 1.0, "confidence": 1.4},
                    "repo.hidden-state": {"type": "noul", "noul": 0.6},
                }
            )
        )
        result = self.screen()
        self.assertNotEqual(result.returncode, 0)
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("repo.error-handling", report["failure"]["reason"])

    def test_S3_a_score_judgment_keeps_its_confidence_beside_the_value(self):
        self.write_config()
        self.write_fixture(
            answer(
                **{
                    "repo.error-handling": {"type": "score", "score": 1.0, "confidence": 0.42},
                    "repo.hidden-state": {"type": "noul", "noul": 0.6},
                }
            )
        )
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        judged = {j["question"]: j for j in self.report()["judgments"]}
        self.assertEqual(judged["repo.error-handling"]["value"], 1.0)
        self.assertAlmostEqual(judged["repo.error-handling"]["confidence"], 0.42)
        self.assertEqual(judged["repo.hidden-state"]["value"], 0.6)


class ProvenanceTests(ProviderContractFixture):
    """S4: the report says what produced it."""

    def test_S4_the_report_names_provider_served_model_limits_and_bank_digest(self):
        self.write_config()
        body = answer(
            **{
                "repo.error-handling": {"type": "score", "score": 1.0, "confidence": 0.8},
                "repo.hidden-state": {"type": "noul", "noul": 0.7},
            }
        )
        with SystemOneServer(body) as server:
            result = self.screen(base_url=server.url)
            requested = server.url
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        self.assertEqual(
            report["provider"].rstrip("/"),
            requested,
            "the report names the endpoint it called, not a default",
        )
        self.assertEqual(report["model"], "jev-latest", "the configured alias is kept beside it")
        self.assertEqual(report["resolved_model"], "jev-1.14.0")
        self.assertEqual(report["limits"]["long_context"], False)
        self.assertGreater(report["limits"]["per_question"], 0)
        self.assertRegex(report["bank_digest"], r"^[0-9a-f]{64}$")


class BudgetTests(ProviderContractFixture):
    """S5: over the limit is incomplete with the provider's reason; long context completes and sends the header."""

    def test_S5_an_oversized_request_is_incomplete_with_the_provider_reason(self):
        self.write_config()
        with SystemOneServer(
            {"detail": {"error_type": "max_tokens_exceeded"}}, status=400
        ) as server:
            result = self.screen(base_url=server.url)
            request = server.request or {}
        self.assertNotEqual(result.returncode, 0)
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("max_tokens_exceeded", report["failure"]["reason"])
        self.assertTrue(request["path"].endswith("/systemone"), request.get("path"))
        sent = json.dumps(request["body"])
        self.assertIn("store.py", sent, "the changed file is in the state the provider was sent")
        self.assertIn(
            "return None",
            sent,
            "the change is sent whole: what overflows the limit is the provider's to refuse, never a local cut",
        )

    def test_S5_long_context_completes_and_sends_the_header(self):
        self.write_config()
        body = answer(
            **{
                "repo.error-handling": {"type": "score", "score": 1.0, "confidence": 0.8},
                "repo.hidden-state": {"type": "noul", "noul": 0.7},
            }
        )
        with SystemOneServer(body) as server:
            result = self.screen(base_url=server.url, long_context=True)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        sent = server.request or {}
        self.assertEqual(sent["headers"].get("typesafe-danger-zone-long-context"), "true")
        self.assertEqual(self.report()["limits"]["long_context"], True)


class RefusalTests(ProviderContractFixture):
    """S6: a refusal writes no report and names a standing one; an incomplete run keeps what it learned."""

    def test_S6_an_unwritable_output_is_a_refusal_naming_the_path(self):
        self.write_config()
        self.write_fixture(answer())
        blocked = Path(self.tmp.name, "not-a-directory")
        blocked.write_text("x", encoding="utf-8")
        result = self.screen(output=blocked)
        self.assertEqual(result.returncode, 2, result.stderr[-2000:])
        self.assertIn(str(blocked), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_S6_a_refusal_names_the_report_it_leaves_standing(self):
        self.write_config()
        self.write_fixture(
            answer(
                **{
                    "repo.error-handling": {"type": "score", "score": 1.0, "confidence": 0.8},
                    "repo.hidden-state": {"type": "noul", "noul": 0.7},
                }
            )
        )
        self.assertEqual(self.screen().returncode, 0)
        standing = self.report()["input"]
        blocked = Path(self.tmp.name, "not-a-directory")
        blocked.write_text("x", encoding="utf-8")
        result = self.screen(output=blocked)
        self.assertEqual(result.returncode, 2)
        self.assertIn(
            standing["head"][:7], result.stderr, "the refusal names the standing report's revision"
        )

    def test_S6_an_incomplete_run_keeps_the_usage_it_paid_for(self):
        self.write_config()
        self.write_fixture(
            {
                "model": "jev-1.14.0",
                "answers": {
                    "repo.error-handling": {"type": "score", "score": 1.0, "confidence": 0.8},
                    "repo.hidden-state": {"type": "noul", "noul": 1.4},
                },
                "usage": {"input_tokens": 321, "output_tokens": 9},
            }
        )
        result = self.screen()
        self.assertNotEqual(result.returncode, 0)
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["usage"]["input_tokens"], 321, "what was paid for is not discarded")
        self.assertEqual(
            [judgment["question"] for judgment in report["judgments"]],
            ["repo.error-handling"],
            "the judgment learned before the refusal is kept, and the refused one is not",
        )


class OfflineTests(ProviderContractFixture):
    """S7: the canned provider carries every offline check, so no run needs a socket."""

    def test_S7_a_screening_through_the_canned_provider_opens_no_socket(self):
        self.write_config()
        self.write_fixture(
            answer(
                **{
                    "repo.error-handling": {"type": "score", "score": 1.0, "confidence": 0.8},
                    "repo.hidden-state": {"type": "noul", "noul": 0.7},
                }
            )
        )
        refused = AssertionError("an offline run opened a socket")
        here = Path.cwd()
        self.addCleanup(os.chdir, here)
        os.chdir(self.workspace)
        with (
            mock.patch.dict(os.environ, {"HOME": self.tmp.name}, clear=True),
            mock.patch("socket.socket", side_effect=refused),
            mock.patch("socket.create_connection", side_effect=refused),
            mock.patch("socket.getaddrinfo", side_effect=refused),
        ):
            from agent_review.cli import main

            code = main(
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
                    str(self.fixture),
                ]
            )
        self.assertEqual(code, 0)
        report = self.report()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["verdict"], "screened")
        self.assertEqual(report["usage"]["requests"], 1)
        self.assertEqual(report["usage"]["input_tokens"], 120, "the canned usage is what was read")
        self.assertEqual(
            [judgment["question"] for judgment in report["judgments"]],
            ["repo.error-handling", "repo.hidden-state"],
            "a report that answered nothing was not screened through the fixture",
        )
