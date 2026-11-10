#!/usr/bin/env python3
"""PreToolUse guard: raw hc mutations are denied for every agent; subagents may not call the mutating
agent-runtime tools or acknowledge receipts. Claude (plugin) and Codex (`--cli codex`) both send
agent_id for native subagents; MCP tool names differ only by prefix."""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys

HC_INFO_FLAGS = {"--help", "-h", "--version"}

SEPARATORS = ";&|()\n"
KEYWORDS = {
    "then",
    "do",
    "else",
    "elif",
    "if",
    "while",
    "until",
    "{",
    "!",
    "time",
    "builtin",
    "command",
    "exec",
    "nohup",
    "env",
    "sudo",
}
# wrapper -> options that consume a separate value; `positional` wrappers take one fixed arg
VALUE_FLAGS = {
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "stdbuf": {"-i", "-o", "-e"},
    "watch": {"-n", "--interval"},
    "xargs": {"-n", "-I", "-P", "-L", "-d", "-s", "-E", "-a"},
    "env": {"-u", "-C"},
    "sudo": {"-u", "-g", "-C", "-h", "-p"},
}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}


def segments(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=SEPARATORS)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    current = []
    for token in lexer:
        if set(token) <= set(SEPARATORS):
            if current:
                yield current
            current = []
        else:
            current.append(token)
    if current:
        yield current


def is_assignment(word):
    name, eq, _ = word.partition("=")
    return bool(eq) and name.replace("_", "a").isalnum() and not name[0].isdigit()


def command_start(words):
    """Index of the program word after env assignments, keywords and wrapper options."""
    i = 0
    while i < len(words):
        word = words[i]
        if is_assignment(word) or word in KEYWORDS:
            i += 1
            if word in ("env", "nohup", "sudo", "command", "exec", "time", "builtin"):
                i = skip_options(words, i, word)
        elif os.path.basename(word) in ("timeout", "nice", "xargs", "stdbuf", "watch"):
            name = os.path.basename(word)
            i = skip_options(words, i + 1, name)
            if name == "timeout" and i < len(words):
                i += 1  # the duration
        else:
            return i
    return len(words)


def skip_options(words, i, name):
    while i < len(words) and words[i].startswith("-") and words[i] != "-":
        i += 2 if words[i] in VALUE_FLAGS.get(name, ()) else 1
    return i


def substitutions(command):
    """Contents of backtick and $(...) substitutions, including those inside double quotes."""
    found = re.findall(r"`([^`]*)`", command)
    i = command.find("$(")
    while i != -1:
        depth, j = 1, i + 2
        while j < len(command) and depth:
            depth += {"(": 1, ")": -1}.get(command[j], 0)
            j += 1
        found.append(command[i + 2 : j - 1 if depth == 0 else j])
        i = command.find("$(", i + 2)
    return found


def invocations(command, depth=0):
    """Yield (program basename, args) for each simple command, descending into sh -c and substitutions."""
    if depth < 3:
        for inner in substitutions(command):
            yield from invocations(inner, depth + 1)
    try:
        parsed = list(segments(command))
    except ValueError:
        return
    for words in parsed:
        i = command_start(words)
        if i >= len(words):
            continue
        program, args = os.path.basename(words[i]), words[i + 1 :]
        yield program, args
        if program == "eval" and depth < 3:
            yield from invocations(" ".join(args), depth + 1)
        if program == "hc" and depth < 3:
            sub = hc_subcommand(args)
            if sub == "exec":
                argv = [a for a in args[args.index("exec") + 1 :]]
                while argv and argv[0].startswith("-") and argv[0] != "--":
                    argv.pop(0)
                if argv[:1] == ["--"]:
                    argv.pop(0)
                if argv:
                    yield from invocations(shlex.join(argv), depth + 1)
        if program in SHELLS and depth < 3:
            for j, arg in enumerate(args):
                if (
                    arg.startswith("-")
                    and not arg.startswith("--")
                    and "c" in arg
                    and j + 1 < len(args)
                ):
                    yield from invocations(args[j + 1], depth + 1)
                    break


def hc_subcommand(args):
    """First non-option argument of hc; None when only options (or info flags) are present."""
    for arg in args:
        if arg in HC_INFO_FLAGS:
            return None
        if not arg.startswith("-"):
            return arg
    return None


def file_read_only(args, sub):
    rest = [a for a in args[args.index(sub) + 1 :] if a != "--json"]
    return bool(rest) and rest[0] in HC_INFO_FLAGS | {"read", "help"}


HC_DENIED = {"run", "send", "kill", "rm"}
HC_ASKED = {"keys", "interrupt"}
MUTATING_TOOLS = {"spawn", "message", "resume", "stop", "report", "task"}
FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SESSION_TOOLS = {"wait"}  # subagents may not block on the lead's inbox
TOOL_NAME = re.compile(r"^mcp__(?:plugin_agent-runtime_)?agent-runtime__(\w+)$")


# A plan's receipts are the driver's evidence (yaml-eec D17): `<state>/<repo-key>/<slug>/receipts/`. The state
# directory is `orchestrate` or a `state` directory; a repository's own `receipts/` folder is not matched.
RECEIPTS = re.compile(
    r"(?:orchestrate|\.local/state|(?:^|/)state)/(?:[^\s'\"]*/)?receipts(?:[/'\"\s]|$)"
)
RECEIPTS_REASON = (
    "A plan's receipts are written only by the driver's evidence runner, never by an agent: a receipt you "
    "write is not evidence. Read them freely; to change a result, fix the code and let the driver "
    "re-run the evidence (verify or run)."
)
APPROVE_REASON = (
    "plan approve is the Lead's: approval binds the contract to a person and runs its baseline. "
    "Ask the Lead to run `agent-orchestrator plan approve <plan>`; you may run plan check."
)
# Programs that only read; with `cd`, they may mention a receipts path without writing it.
READERS = {
    "cat",
    "ls",
    "head",
    "tail",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "jq",
    "wc",
    "stat",
    "diff",
    "cmp",
    "less",
    "more",
    "nl",
    "sort",
    "file",
    "sha256sum",
    "shasum",
    "md5sum",
    "cksum",
    "echo",
    "printf",
    "pwd",
    "cd",
    "test",
    "[",
    "true",
    "false",
    "realpath",
    "basename",
    "dirname",
    "du",
    "tree",
    "xxd",
    "od",
    "column",
}


def mentions_receipts(text):
    return bool(RECEIPTS.search(os.path.expanduser(text).replace("$HOME", os.path.expanduser("~"))))


def receipts_verdict(command):
    """A reason when a command may write under a plan's receipts: the path appears and something that is not a
    reader runs, or a redirection targets it."""
    if not isinstance(command, str) or not mentions_receipts(command):
        return None
    if re.search(r">>?\s*['\"]?[^\s&|;]*receipts", command):
        return RECEIPTS_REASON
    for program, _ in invocations(command):
        if program not in READERS:
            return RECEIPTS_REASON
    return None


def approve_verdict(command):
    """A reason when a command runs `plan approve` of the orchestrator, however it is launched."""
    for program, args in invocations(command):
        words = [program, *args]
        if not any("orchestrat" in os.path.basename(w) for w in words[:5]):
            continue
        if any(a == "plan" and b == "approve" for a, b in zip(words, words[1:])):
            return APPROVE_REASON
    return None


def spawned_worker():
    """Whether the runtime says this session is a spawned worker: `agent-runtime resolve --json` with role worker.

    A spawned worker is its own session with no agent_id, so the runtime's public answer is the only signal. Any
    failure reads as "not a worker": the hook is defence in depth and the CLI is the authority (D26)."""
    home = os.environ.get("AGENT_RUNTIME_HOME")
    script = os.path.join(os.path.expanduser(home), "server", "agent_runtime.py") if home else None
    argv = [sys.executable, script] if script and os.path.isfile(script) else ["agent-runtime"]
    try:
        done = subprocess.run(
            [*argv, "resolve", "--json"], capture_output=True, text=True, timeout=3, check=True
        )
        answer = json.loads(done.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return isinstance(answer, dict) and answer.get("role") == "worker"


def contract_verdict(command, is_subagent):
    """A reason when a worker (a native subagent, or a session the runtime resolves as a spawned worker) tries
    `plan approve` or a write under receipts. The runtime is asked only once a command looks like either."""
    reason = approve_verdict(command) or receipts_verdict(command)
    return reason if reason and (is_subagent or spawned_worker()) else None


def verdict(command, is_subagent):
    """(decision, reason) or None."""
    if not isinstance(command, str):
        command = json.dumps(command)
        if re.search(r"\bhc\b", command):
            return (
                "deny",
                "Cannot parse this command and it mentions hc: use the agent-runtime tools.",
            )
        return None
    try:
        list(segments(command))
    except ValueError:
        if re.search(r"\bhc\b", command):
            return (
                "deny",
                "Cannot parse this command and it mentions hc: use the agent-runtime tools.",
            )
    result = None
    reason = contract_verdict(command, is_subagent)
    if reason:
        return "deny", reason
    for program, args in invocations(command):
        runtime_args = entrypoint_args(program, args)
        if runtime_args is not None:
            gated = call_verdict(runtime_args, is_subagent)
            if gated and gated[0] == "deny":
                return gated
            result = result or gated
        if program != "hc":
            continue
        sub = hc_subcommand(args)
        if sub in HC_ASKED and not is_subagent:
            result = result or ("ask", "approve to press keys in a worker")
        elif sub in HC_DENIED | HC_ASKED or (sub == "file" and not file_read_only(args, sub)):
            return "deny", "Do not run 'hc %s' in Bash: use the agent-runtime tools." % sub
    return result


ENTRYPOINTS = {"agent-runtime", "agent_runtime.py"}
PYTHON = re.compile(r"^python[0-9.]*$")
PYTHON_VALUE_FLAGS = {"-W", "-X", "-Q"}


def entrypoint_args(program, args):
    """Arguments after the agent-runtime entrypoint, whether run by name, by path or as a python script; else None."""
    if program in ENTRYPOINTS:
        return args
    if PYTHON.match(program):
        i = 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] in ("-c", "-m", "-"):
                return None
            i += 2 if args[i] in PYTHON_VALUE_FLAGS else 1
        if i < len(args) and os.path.basename(args[i]) == "agent_runtime.py":
            return args[i + 1 :]
    return None


class CallUsage(Exception):
    pass


class CallParser(argparse.ArgumentParser):
    def error(self, message):
        raise CallUsage(message)


def parse_call(argv):
    """The `call` subcommand's arguments read as server/agent_runtime.py's parser reads them, or None when
    that parser would reject them. Keep the options and allow_abbrev=False in step with build_parser()."""
    parser = CallParser(prog="agent-runtime call", add_help=False, allow_abbrev=False)
    parser.add_argument("tool")
    parser.add_argument("--lead")
    parser.add_argument("--json", default="{}")
    try:
        return parser.parse_args(argv)
    except CallUsage:
        return None


def call_verdict(args, is_subagent):
    """`agent-runtime call <tool> --json '{...}'` carries the same authority as the MCP tool: gate it alike.
    Arguments the hook cannot read fail closed: denied for subagents, asked otherwise."""
    rest = [a for a in args if not a.startswith("-")]
    if rest[:1] != ["call"]:
        return None
    argv = args[args.index("call") + 1 :]
    if "-h" in argv or "--help" in argv:
        return None  # the CLI prints usage and runs nothing
    parsed = parse_call(argv)
    if parsed is None:
        if is_subagent:
            return "deny", (
                "Subagents must not run agent-runtime call with unreadable arguments: "
                "report through your return value."
            )
        return "ask", "approve to run agent-runtime call with arguments the hook cannot read"
    tool = parsed.tool
    try:
        arguments = json.loads(parsed.json)
    except ValueError:
        arguments = None
    reason = tool_verdict("mcp__agent-runtime__" + tool, is_subagent, arguments)
    if reason:
        return "deny", reason
    read_only = tool == "task" and isinstance(arguments, dict) and arguments.get("action") == "list"
    if tool in MUTATING_TOOLS and not read_only and not is_subagent:
        return "ask", "approve to run the agent-runtime %s tool from Bash" % tool
    return None


def tool_verdict(tool_name, is_subagent, arguments=None):
    match = TOOL_NAME.match(tool_name or "")
    if not is_subagent or not match:
        return None
    tool = match.group(1)
    if isinstance(arguments, str):  # some hosts serialize tool input as a JSON string
        try:
            arguments = json.loads(arguments)
        except ValueError:
            pass
    if not isinstance(arguments, dict):
        return "Subagents must not call agent-runtime tools with unreadable input: report through your return value."
    if (
        tool == "task"
        and arguments.get("action") == "list"
        and arguments.get("receipt") is None
        and arguments.get("assignment_id") is None
    ):
        return None  # read-only listing
    if tool in MUTATING_TOOLS | SESSION_TOOLS:
        return (
            "Subagents must not call the agent-runtime %s tool: report through your return value."
            % tool
        )
    if arguments.get("assignment_id") is not None:  # would pin a session or read the lead's reports
        return "Subagents must not pass assignment_id to agent-runtime tools: report through your return value."
    if (
        arguments.get("receipt") is not None
    ):  # an explicit ack mutates the lead's or teammate's inbox
        return "Subagents must not acknowledge agent-runtime receipts: report through your return value."
    return None


def main(argv=()):
    try:
        data = json.load(sys.stdin)
        name = data.get("tool_name", "Bash")
        if name in FILE_TOOLS:
            tool_input = data.get("tool_input") or {}
            path = tool_input.get("file_path") or tool_input.get("notebook_path")
            guarded = (
                isinstance(path, str)
                and mentions_receipts(path)
                and (bool(data.get("agent_id")) or spawned_worker())
            )
            result = ("deny", RECEIPTS_REASON) if guarded else None
        elif name == "Bash":
            result = verdict(
                data.get("tool_input", {}).get("command", ""), bool(data.get("agent_id"))
            )
        else:
            reason = tool_verdict(name, bool(data.get("agent_id")), data.get("tool_input"))
            result = ("deny", reason) if reason else None
        if result:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": result[0],
                            "permissionDecisionReason": result[1],
                        }
                    }
                )
            )
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
