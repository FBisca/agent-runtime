"""Acceptance for the screening provider contract (00b-agent-review-provider-contract/S1, S5, S7).

The runner places this file at `packages/agent-review/acceptance/screening/`. It drives the `agent-review`
console script over a real two-revision repository, a config it writes, a loopback System One endpoint
and a canned provider, and it imports nothing from the package at module import, so it collects before
the package changes. The contract it pins:

  - S1: a screening without a fixture posts one request to the provider it is configured with, at the
    `/systemone` route, authenticated by `TYPESAFE_API_KEY` - also when a fallback credential is present -
    with `state` an object and `questions` an id -> question map in System One's typed shape; a missing
    credential stops the run incomplete naming `TYPESAFE_API_KEY`
  - S5: a state over the limit in force is incomplete with the provider's own reason
    (`max_tokens_exceeded`), the change is never truncated to fit, and the same change completes when
    long context is requested
  - S7: a screening through the canned provider completes with no credential and every socket refused, so
    every offline check is carried by the fixture and no check needs the network

Each class carries a control that passes at base and after.
"""

import contextlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.integration

CONFIG = textwrap.dedent(
    """\
    model: jev-latest
    questions:
      - id: contract.error-handling
        priority: critical
        ask: Does every new failure at a boundary surface an error a caller can act on
      - id: design.naming
        priority: advisory
        ask: Do the new names say what they mean
    """
)

ANSWER = {
    "model": "jev-1.14.0",
    "answers": {
        "contract.error-handling": {"type": "noul", "noul": 0.9},
        "design.naming": {"type": "noul", "noul": 0.4},
    },
    "usage": {"input_tokens": 1234, "output_tokens": 42},
}

LIMIT = 32_000

BASE_FILE = "def load(path):\n    return open(path).read()\n"

HEAD_FILE = (
    "def load(path):\n"
    "    try:\n"
    "        return open(path).read()\n"
    "    except OSError:\n"
    "        return None\n"
)

BULK_FILE = "".join(f"def step_{n}(value):\n    return value + {n}\n\n" for n in range(1200))

CREDENTIAL_PREFIXES = ("TYPESAFE_", "AGENT_REVIEW_", "OPENAI_")
PROXY_VARIABLES = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")


def git(where, *args):
    """`git <args>` in `where`, its stdout stripped; a failing git is the fixture's failure, not the tool's."""
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


def header(headers, name):
    """One header by name, whatever case the tool sent it in."""
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


class CannedProvider:
    """A loopback System One endpoint: it records each request and answers the way the provider does."""

    def __init__(self, limit=None):
        self.limit = limit
        self.requests = []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                provider.requests.append((self.path, dict(self.headers.items()), body))
                status, payload = provider.answer(body, dict(self.headers.items()))
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                """The provider's access log is noise in a check."""

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self):
        port = self.server.server_address[1]
        return f"http://127.0.0.1:{port}/v1"

    def answer(self, body, headers):
        """The provider's answer: an oversized state is refused unless long context was requested."""
        if self.limit is None:
            return 200, ANSWER
        sent = json.loads(body)
        state = sent.get("state") if isinstance(sent, dict) else None
        oversized = len(json.dumps(state)) > self.limit
        long_context = header(headers, "TypeSafe-Danger-Zone-Long-Context") == "true"
        if oversized and not long_context:
            return 400, {"detail": {"error_type": "max_tokens_exceeded"}}
        return 200, ANSWER

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


@contextlib.contextmanager
def refused_sockets():
    """Every socket refused, so a check that reaches the network fails instead of passing quietly."""

    def refuse(*args, **kwargs):
        raise OSError("the network is refused: this check is offline")

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch("socket.socket", refuse))
        stack.enter_context(mock.patch("socket.create_connection", refuse))
        stack.enter_context(mock.patch("socket.getaddrinfo", refuse))
        yield


class ProviderContractFixture(unittest.TestCase):
    """A repository with two revisions, a config, an output directory and an offline environment."""

    bulk = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name, "checkout")
        self.workspace.mkdir()
        self.output = Path(self.tmp.name, "review")
        self.config = Path(self.tmp.name, "config.yaml")
        self.config.write_text(CONFIG, encoding="utf-8")
        self.fixture = Path(self.tmp.name, "screening.json")
        self.base, self.head = self.two_revisions()

    def two_revisions(self):
        """A repository whose head adds error handling, and a bulk module when the change must be big."""
        commit = (
            "-c",
            "user.name=acceptance",
            "-c",
            "user.email=acceptance@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
        )
        git(self.workspace, "init", "-b", "main")
        store = self.workspace / "store.py"
        store.write_text(BASE_FILE, encoding="utf-8")
        git(self.workspace, "add", "--all")
        git(self.workspace, *commit, "base")
        base = git(self.workspace, "rev-parse", "HEAD")
        store.write_text(HEAD_FILE, encoding="utf-8")
        if self.bulk:
            (self.workspace / "bulk.py").write_text(BULK_FILE, encoding="utf-8")
        git(self.workspace, "add", "--all")
        git(self.workspace, *commit, "head")
        return base, git(self.workspace, "rev-parse", "HEAD")

    def environment(self, extra):
        """The environment one screening runs in: none of this machine's credentials, proxies or endpoint."""
        environment = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(CREDENTIAL_PREFIXES) and name.lower() not in PROXY_VARIABLES
        }
        environment.update(extra)
        return environment

    def screen(self, *, endpoint=None, extra=(), env=None):
        """One screening of the fixture's change, through the console script a caller runs."""
        binary = shutil.which("agent-review")
        if binary is None:
            self.fail(
                "no `agent-review` on PATH: run the check under `uv run`, which installs the workspace member"
            )
        environment = self.environment(dict(env or {}))
        if endpoint is not None:
            environment["AGENT_REVIEW_BASE_URL"] = endpoint
        return subprocess.run(
            [
                binary,
                "code",
                "--base",
                self.base,
                "--head",
                self.head,
                "--output",
                str(self.output),
                "--config",
                str(self.config),
                *extra,
            ],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            env=environment,
        )

    def report(self):
        """The report the invocation wrote, read as JSON."""
        path = self.output / "report.json"
        self.assertTrue(path.is_file(), f"no report at {path}: an invocation writes one either way")
        return json.loads(path.read_text(encoding="utf-8"))

    def change(self):
        """The whole change between the two revisions, as `git diff` prints it."""
        return git(self.workspace, "diff", self.base, self.head)


class SystemOneRequestTests(ProviderContractFixture):
    """S1: one typed request to the configured provider, authenticated by TYPESAFE_API_KEY."""

    def provider(self, limit=None):
        endpoint = CannedProvider(limit=limit)
        self.addCleanup(endpoint.close)
        return endpoint

    def test_control_the_two_revisions_carry_a_change(self):
        self.assertNotEqual(self.base, self.head)
        self.assertIn("except OSError", self.change())

    def test_S1_a_screening_posts_one_typed_systemone_request_with_the_typesafe_credential(self):
        provider = self.provider()
        result = self.screen(
            endpoint=provider.endpoint,
            env={"TYPESAFE_API_KEY": "typesafe-key", "AGENT_REVIEW_API_KEY": "fallback-key"},
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(len(provider.requests), 1, "one screening is one request (D40)")
        path, headers, body = provider.requests[0]
        self.assertEqual(path, "/v1/systemone", "the tool posts to System One's own route")
        self.assertEqual(
            header(headers, "Authorization"),
            "Bearer typesafe-key",
            "TYPESAFE_API_KEY is the credential, also when a fallback is present",
        )
        sent = json.loads(body)
        self.assertEqual(sent["model"], "jev-latest")
        self.assertIsInstance(sent["state"], dict, "state is a JSON object")
        self.assertIn(
            "except OSError", json.dumps(sent["state"]), "the change travels in the state"
        )
        self.assertEqual(
            set(sent["questions"]),
            {"contract.error-handling", "design.naming"},
            "the questions travel as an id -> question map",
        )
        asked = sent["questions"]["contract.error-handling"]
        self.assertEqual(asked["type"], "noul", "a bounded yes/no question is System One's noul")
        self.assertIn("error a caller can act on", asked["instructions"])
        self.assertNotIn("messages", sent, "the chat completion shape is gone")
        self.assertEqual(self.report()["status"], "completed")

    def test_S1_a_missing_credential_is_incomplete_naming_typesafe_api_key(self):
        result = self.screen(endpoint="http://127.0.0.1:1/v1")
        self.assertNotEqual(result.returncode, 0, "a missing credential must not screen cleanly")
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("TYPESAFE_API_KEY", report["failure"]["reason"])
        self.assertIn("TYPESAFE_API_KEY", result.stderr)


class InputLimitTests(ProviderContractFixture):
    """S5: an oversized state is the provider's refusal, never a truncated change."""

    bulk = True

    def provider(self, limit=None):
        endpoint = CannedProvider(limit=limit)
        self.addCleanup(endpoint.close)
        return endpoint

    def test_control_the_change_is_larger_than_the_limit_in_force(self):
        self.assertGreater(
            len(self.change()), LIMIT, "the fixture's change must be over the provider's limit"
        )

    def test_S5_an_oversized_state_is_incomplete_with_the_provider_reason_never_truncated(self):
        provider = self.provider(limit=LIMIT)
        result = self.screen(endpoint=provider.endpoint, env={"TYPESAFE_API_KEY": "typesafe-key"})
        self.assertNotEqual(result.returncode, 0, "an oversized state must not screen cleanly")
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn(
            "max_tokens_exceeded",
            report["failure"]["reason"],
            "the provider's own reason is kept",
        )
        sent = json.loads(provider.requests[0][2])
        self.assertEqual(
            sent["state"]["diff"].rstrip("\n"),
            self.change(),
            "the change is never truncated to fit",
        )

    def test_S5_the_same_change_completes_when_long_context_is_requested(self):
        provider = self.provider(limit=LIMIT)
        result = self.screen(
            endpoint=provider.endpoint,
            env={"TYPESAFE_API_KEY": "typesafe-key"},
            extra=["--long-context"],
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        _, headers, body = provider.requests[0]
        self.assertEqual(header(headers, "TypeSafe-Danger-Zone-Long-Context"), "true")
        self.assertEqual(json.loads(body)["state"]["diff"].rstrip("\n"), self.change())
        self.assertEqual(self.report()["status"], "completed")


class CannedProviderTests(ProviderContractFixture):
    """S7: the fixture provider carries every offline check, with no credential and no socket."""

    def canned(self):
        self.fixture.write_text(json.dumps({"responses": [ANSWER]}), encoding="utf-8")

    def test_control_every_socket_is_refused(self):
        with refused_sockets():
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", 1), timeout=1)

    def test_S7_a_canned_screening_completes_with_no_credential_and_every_socket_refused(self):
        from agent_review.cli import main

        self.canned()
        saved = {
            name: os.environ.pop(name)
            for name in list(os.environ)
            if name.startswith(CREDENTIAL_PREFIXES)
        }
        self.addCleanup(os.environ.update, saved)
        cwd = os.getcwd()
        os.chdir(self.workspace)
        self.addCleanup(os.chdir, cwd)
        with refused_sockets():
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
        self.assertEqual(code, 0, "the fixture screens offline, with no credential")
        report = self.report()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["verdict"], "screened")
        self.assertEqual(
            [judgment["question"] for judgment in report["judgments"]],
            ["contract.error-handling", "design.naming"],
            "the canned answers are the judgments",
        )
        self.assertEqual(report["usage"]["requests"], 1)
