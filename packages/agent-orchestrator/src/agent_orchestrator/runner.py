"""The evidence runner: the one place a plan's `check` runs, and the one author of a receipt.

A check runs once, under `bash -euo pipefail`, in a disposable detached worktree at the target commit with the
oracles placed, a temporary HOME and XDG_STATE_HOME, the real cache directories, no virtualenv on PATH and only the
env names the plan declares. The result is PASS, FAIL or UNKNOWN by rule (D16); nothing is rerun, and an agent's own
run never yields a receipt, because only the driver calls this module. Stdlib only.
"""

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

SCHEMA_VERSION = 1
LOG_LIMIT = 1_000_000
JUNIT_LIMIT = 50_000_000
GIT_TIMEOUT = 120

# Names a tool reads its caches from. They are passed through (or defaulted to the real home's) so `uv run` in the
# worktree builds its own venv from the warm cache instead of the network.
CACHE_NAMES = (
    "XDG_CACHE_HOME",
    "UV_CACHE_DIR",
    "UV_PYTHON_INSTALL_DIR",
    "PIP_CACHE_DIR",
    "npm_config_cache",
    "CARGO_HOME",
    "RUSTUP_HOME",
    "GOCACHE",
    "GOMODCACHE",
    "PRE_COMMIT_HOME",
)
TOOLS = (
    ("bash", ["bash", "--version"]),
    ("git", ["git", "--version"]),
    ("python3", ["python3", "--version"]),
    ("uv", ["uv", "--version"]),
)


class RunnerError(RuntimeError):
    """The runner could not set a run up (no such commit, no git); never a check's own failure."""


class Run:
    """One run of one evidence item: its receipt, its log, and where they were written (None until persisted)."""

    def __init__(self, receipt, log):
        self.receipt = receipt
        self.log = log
        self.path = None
        self.sha256 = None


def now():
    return datetime.now(timezone.utc).isoformat()


def git(*args, cwd=None, check=True):
    done = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    if check and done.returncode:
        raise RunnerError("git %s failed: %s" % (args[0], done.stderr.strip()[-300:]))
    return done


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def is_venv_bin(entry, virtual_env):
    """A PATH entry that belongs to a virtualenv: the active one, or any directory beside a pyvenv.cfg."""
    if not entry:
        return False
    path = Path(entry)
    if virtual_env and (path == Path(virtual_env, "bin") or path.is_relative_to(virtual_env)):
        return True
    return (path.parent / "pyvenv.cfg").is_file()


def clean_path(path, virtual_env):
    return os.pathsep.join(
        entry for entry in (path or "").split(os.pathsep) if not is_venv_bin(entry, virtual_env)
    )


def real_defaults(home):
    """Cache locations a tool would use under the real HOME, for the names the caller did not set."""
    home = Path(home)
    found = {"XDG_CACHE_HOME": home / ".cache"}
    for name, candidate in (
        ("PIP_CACHE_DIR", home / "Library" / "Caches" / "pip"),
        ("UV_PYTHON_INSTALL_DIR", home / ".local" / "share" / "uv" / "python"),
        ("CARGO_HOME", home / ".cargo"),
        ("RUSTUP_HOME", home / ".rustup"),
        ("npm_config_cache", home / ".npm"),
    ):
        if candidate.is_dir():
            found[name] = candidate
    return {name: str(path) for name, path in found.items()}


def check_env(base, names, scratch, junit):
    """The environment of a check, or the declared names that are unset in `base`."""
    missing = [name for name in names if name not in base]
    if missing:
        return None, missing
    home = base.get("HOME") or os.path.expanduser("~")
    env = {"PATH": clean_path(base.get("PATH", ""), base.get("VIRTUAL_ENV"))}
    for name in ("LANG", "LC_ALL", "TERM", "TMPDIR"):
        if name in base:
            env[name] = base[name]
    env.update(real_defaults(home))
    env.update({name: base[name] for name in CACHE_NAMES if name in base})
    env["HOME"] = str(scratch / "home")
    env["XDG_STATE_HOME"] = str(scratch / "state")
    env.update({name: base[name] for name in names})
    env["EEC_JUNIT"] = str(junit)
    for name in ("HOME", "XDG_STATE_HOME"):
        os.makedirs(env[name], exist_ok=True)
    return env, []


@lru_cache(maxsize=8)
def tool_versions(path):
    versions = {}
    for name, argv in TOOLS:
        try:
            done = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=10,
                env={"PATH": path, "HOME": tempfile.gettempdir()},
            )
            lines = (done.stdout or done.stderr).strip().splitlines()
            versions[name] = lines[0] if done.returncode == 0 and lines else None
        except (OSError, subprocess.TimeoutExpired):
            versions[name] = None
    return versions


ASSERTION = re.compile(r"(?:assert\b|(?:[\w.]+\.)?AssertionError\b)")
UNBOUND = re.compile(r"(\w+): unbound variable")


def is_assertion(failure):
    """Whether a JUnit <failure> is an assertion: pytest writes `assert ...` or `AssertionError: ...` as the message,
    unittest names the type. Any other exception (a missing module or attribute, a TypeError) is not one."""
    kind = failure.get("type") or ""
    return bool(
        ASSERTION.fullmatch(kind) or ASSERTION.match((failure.get("message") or "").lstrip())
    )


def junit_counts(path):
    """Case counts from a JUnit file: None when there is no file, "unreadable" when it does not parse."""
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > JUNIT_LIMIT:
            return "unreadable"
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return "unreadable"
    counts = {
        "tests": 0,
        "executed": 0,
        "passed": 0,
        "failures": 0,
        "assertions": 0,
        "errors": 0,
        "skipped": 0,
    }
    for case in root.iter("testcase"):
        counts["tests"] += 1
        if case.find("error") is not None:
            counts["errors"] += 1
            counts["executed"] += 1
        elif case.find("failure") is not None:
            counts["failures"] += 1
            counts["assertions"] += any(is_assertion(f) for f in case.findall("failure"))
            counts["executed"] += 1
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
            counts["executed"] += 1
    return counts


def classify(code, junit):
    """(result, reason) of a check that ran to its end, by D16."""
    if code == 127:
        return "UNKNOWN", "missing tool"
    if junit == "unreadable":
        return "UNKNOWN", "unreadable JUnit"
    if junit is None:
        return ("PASS", None) if code == 0 else ("FAIL", None)
    if code == 0:
        if junit["executed"] == 0:
            return "UNKNOWN", "no cases executed"
        if junit["skipped"]:
            return "UNKNOWN", "cases skipped"
        if junit["failures"] or junit["errors"]:
            return "FAIL", "error" if junit["errors"] else None
        return "PASS", None
    if junit["errors"]:
        return "FAIL", "error"
    if junit["failures"]:
        return "FAIL", None
    if junit["tests"] == 0:
        return "UNKNOWN", "no cases executed"
    return "FAIL", "error"


def label(result, reason):
    return "%s (%s)" % (result, reason) if reason else result


def redact(text, values):
    for value in sorted({v for v in values if v}, key=len, reverse=True):
        text = text.replace(value, "***")
    return text


def kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def execute(check, cwd, env, timeout, log_path):
    """(exit code or None, timed out) of `check` under bash -euo pipefail in its own process group."""
    with open(log_path, "wb") as log:
        process = subprocess.Popen(
            ["bash", "-euo", "pipefail", "-c", check],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            kill_group(process)
            process.wait()
            code, timed_out = None, True
        else:
            kill_group(process)  # a check's background children must not outlive it
    return code, timed_out


def worktree(repo, commit, scratch, name):
    path = scratch / name
    git("-C", str(repo), "worktree", "add", "--detach", "--quiet", str(path), commit)
    return path


def drop_worktree(repo, path):
    git("-C", str(repo), "worktree", "remove", "--force", str(path), check=False)
    shutil.rmtree(path, ignore_errors=True)
    git("-C", str(repo), "worktree", "prune", check=False)


def place_oracles(tree, place_dir, oracles):
    for name, content in oracles.items():
        target = Path(tree) / (place_dir or ".") / name.removesuffix(".oracle")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def tail(path):
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - LOG_LIMIT))
            return stream.read().decode("utf-8", "replace")
    except OSError:
        return ""


def attempt(repo, commit, *, check, oracles, place_dir, env_names, timeout, base_env, patch=None):
    """One execution of `check` in a fresh worktree: (facts, log text). `patch` is applied before the oracles and
    skips the dirty-tree rule, since the patch itself dirties the tree."""
    scratch = Path(tempfile.mkdtemp(prefix="eec-"))
    started = time.monotonic()
    facts = {"exit": None, "junit": None, "exit_code_only": False, "detail": None}
    tree = None
    try:
        junit_path = scratch / "junit.xml"
        env, missing = check_env(base_env, env_names, scratch, junit_path)
        if missing:
            facts.update(
                result="UNKNOWN",
                reason="env",
                detail="unset env name%s: %s"
                % ("s" if len(missing) > 1 else "", ", ".join(missing)),
            )
            return facts, ""
        tree = worktree(repo, commit, scratch, "tree")
        if patch is not None:
            patch_file = scratch / "negative.patch"
            patch_file.write_bytes(patch)
            applied = git(
                "-C", str(tree), "apply", "--whitespace=nowarn", str(patch_file), check=False
            )
            if applied.returncode:
                facts.update(
                    result="UNKNOWN",
                    reason="negative patch does not apply",
                    detail=applied.stderr.strip()[-300:],
                )
                return facts, ""
        place_oracles(tree, place_dir, oracles)
        log_path = scratch / "check.log"
        code, timed_out = execute(check, tree, env, timeout, log_path)
        facts["exit"] = code
        counts = junit_counts(junit_path)
        facts["junit"] = counts if isinstance(counts, dict) else None
        facts["exit_code_only"] = counts is None
        dirty = (
            git(
                "-C", str(tree), "status", "--porcelain", "--untracked-files=no", check=False
            ).stdout.strip()
            if patch is None
            else ""
        )
        if timed_out:
            facts.update(result="UNKNOWN", reason="timeout", detail="killed after %ss" % timeout)
        elif code and counts is None and UNBOUND.search(tail(log_path)):
            # `set -u` killed the check before it asserted anything: it never ran, whatever its exit says.
            names = sorted(set(UNBOUND.findall(tail(log_path))))
            facts.update(
                result="UNKNOWN",
                reason="env",
                detail="unbound variable%s: %s" % ("s" if len(names) > 1 else "", ", ".join(names)),
            )
        elif dirty:
            facts.update(
                result="UNKNOWN",
                reason="dirty tree",
                detail="the check modified tracked files: "
                + ", ".join(line[3:] for line in dirty.splitlines()[:10]),
            )
        else:
            result, reason = classify(code, counts)
            facts.update(result=result, reason=reason)
        return facts, redact(tail(log_path), [base_env.get(name, "") for name in env_names])
    finally:
        facts["seconds"] = round(time.monotonic() - started, 3)
        if tree is not None:
            drop_worktree(repo, tree)
        shutil.rmtree(scratch, ignore_errors=True)


def run_evidence(
    repo,
    commit,
    *,
    scenario,
    phase,
    digest,
    check,
    oracles=None,
    place=None,
    env_names=(),
    timeout=600,
    negative=None,
    base_env=None,
    receipts_dir=None,
):
    """Run one evidence item once at `commit` and return its Run.

    `oracles` maps oracle file names to their approved bytes, placed under `place`. `negative` is
    `(file name, patch bytes)`: when the check passes, it must fail on a scratch copy with the patch applied, or the
    item is FAIL (vacuous). With `receipts_dir` the receipt and log are written there; without it nothing is."""
    repo = Path(repo)
    oracles = dict(oracles or {})
    base_env = dict(os.environ if base_env is None else base_env)
    commit = git("-C", str(repo), "rev-parse", "--verify", commit + "^{commit}").stdout.strip()
    tree_sha = git("-C", str(repo), "rev-parse", commit + "^{tree}").stdout.strip()
    started_at = now()
    wanted = dict(
        check=check,
        oracles=oracles,
        place_dir=place,
        env_names=list(env_names),
        timeout=timeout,
        base_env=base_env,
    )
    facts, log = attempt(repo, commit, **wanted)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "scenario": scenario,
        "phase": phase,
        "digest": digest,
        "commit": commit,
        "tree": tree_sha,
        "check": check,
        "place": place,
        "oracles": {name: sha256(content) for name, content in sorted(oracles.items())},
        "env": list(env_names),
        "timeout": timeout,
        "tools": tool_versions(clean_path(base_env.get("PATH", ""), base_env.get("VIRTUAL_ENV"))),
        "started_at": started_at,
        "exit": facts["exit"],
        "junit": facts["junit"],
        "exit_code_only": facts["exit_code_only"],
        "result": facts["result"],
        "reason": facts["reason"],
        "detail": facts["detail"],
        "seconds": facts["seconds"],
    }
    if negative is not None and facts["result"] == "PASS":
        name, patch = negative
        second, _ = attempt(repo, commit, patch=patch, **wanted)
        receipt["negative"] = {
            "file": name,
            "sha256": sha256(patch),
            "result": second["result"],
            "reason": second["reason"],
            "exit": second["exit"],
            "junit": second["junit"],
        }
        if second["result"] == "PASS":
            receipt.update(
                result="FAIL", reason="vacuous", detail="the check passes with %s applied" % name
            )
        elif (
            second["result"] == "FAIL"
            and second["reason"] is None
            and (second["junit"] is None or second["junit"]["assertions"])
        ):
            pass  # the check fails on the broken copy on an assertion: the control holds
        else:
            receipt.update(
                result="UNKNOWN",
                reason="negative control",
                detail="the check did not fail on an assertion with %s applied: %s"
                % (name, label(second["result"], second["reason"])),
            )
    receipt["label"] = label(receipt["result"], receipt["reason"])
    run = Run(receipt, log)
    if receipts_dir is not None:
        persist(run, receipts_dir)
    return run


def persist(run, receipts_dir, extra=None):
    """Write the run's receipt and log as `<scenario>.<phase>.<n>.json|log`; n counts the item's earlier receipts.

    Returns the Run, whose `path` and `sha256` the ledger keeps. `extra` is merged into the receipt first."""
    directory = Path(receipts_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if extra:
        run.receipt.update(extra)
    stem = "%s.%s" % (run.receipt["scenario"], run.receipt["phase"])
    number = 1
    while True:
        base = directory / ("%s.%d" % (stem, number))
        try:
            fd = os.open(str(base) + ".json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            number += 1
    run.receipt["log"] = base.name + ".log"
    run.receipt["log_sha256"] = sha256(run.log.encode("utf-8"))
    text = json.dumps(run.receipt, indent=2, sort_keys=True) + "\n"
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    log_fd = os.open(str(base) + ".log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(log_fd, "w", encoding="utf-8") as stream:
        stream.write(run.log)
    run.path = Path(str(base) + ".json")
    run.sha256 = sha256(text.encode("utf-8"))
    return run


def record_in_ledger(data, run):
    """Keep a persisted receipt's sha256 in the ledger data, keyed by its file stem."""
    data.setdefault("receipts", {})[run.path.stem] = run.sha256
    return data


def judge_baseline(receipt, expected, has_oracle):
    """(ok, observed, calibrated) of a baseline receipt against the item's `baseline:` (D24).

    A red is calibrated only when the check collects without errors and at least one case fails on an assertion. A
    new test oracle must also pass at least one case, its controls. A new oracle whose behaviour dies on something
    else (a missing interface) is accepted as an uncalibrated red when its controls pass; an existing test that does
    is refused. An item whose check writes no JUnit can only show a non-zero exit: accepted as red, never calibrated."""
    result, reason, junit = receipt["result"], receipt["reason"], receipt["junit"]
    observed = receipt["label"]
    if receipt.get("detail"):
        observed += ": " + receipt["detail"]
    if expected == "pass":
        return result == "PASS", observed, None
    if result == "PASS":
        return False, observed + " (vacuous: the check passes at base)", False
    if result != "FAIL" or reason == "error":
        return False, observed, False
    if junit is None:
        return True, observed, False
    if has_oracle and not junit["passed"]:
        return False, observed + " (no control case passed)", False
    if not junit["assertions"]:
        if not has_oracle:
            return False, observed + " (no case failed on an assertion)", False
        return True, observed, False
    return True, observed, True
