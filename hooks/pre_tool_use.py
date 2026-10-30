#!/usr/bin/env python3
"""PreToolUse guard: raw hc mutations are denied for every agent; subagents may not call the mutating
agent-runtime tools. `--cli codex` selects the Codex payload (Bash only, no agent_id)."""

import json
import os
import re
import shlex
import sys

HC_INFO_FLAGS = {"--help", "-h", "--version"}

SEPARATORS = ";&|()\n"
KEYWORDS = {"then", "do", "else", "elif", "if", "while", "until", "{", "!", "time", "builtin",
            "command", "exec", "nohup", "env", "sudo"}
# wrapper -> options that consume a separate value; `positional` wrappers take one fixed arg
VALUE_FLAGS = {"timeout": {"-s", "-k", "--signal", "--kill-after"}, "nice": {"-n", "--adjustment"},
               "stdbuf": {"-i", "-o", "-e"}, "watch": {"-n", "--interval"},
               "xargs": {"-n", "-I", "-P", "-L", "-d", "-s", "-E", "-a"}, "env": {"-u", "-C"},
               "sudo": {"-u", "-g", "-C", "-h", "-p"}}
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
        found.append(command[i + 2:j - 1 if depth == 0 else j])
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
        program, args = os.path.basename(words[i]), words[i + 1:]
        yield program, args
        if program == "eval" and depth < 3:
            yield from invocations(" ".join(args), depth + 1)
        if program == "hc" and depth < 3:
            sub = hc_subcommand(args)
            if sub == "exec":
                argv = [a for a in args[args.index("exec") + 1:]]
                while argv and argv[0].startswith("-") and argv[0] != "--":
                    argv.pop(0)
                if argv[:1] == ["--"]:
                    argv.pop(0)
                if argv:
                    yield from invocations(shlex.join(argv), depth + 1)
        if program in SHELLS and depth < 3:
            for j, arg in enumerate(args):
                if arg.startswith("-") and not arg.startswith("--") and "c" in arg and j + 1 < len(args):
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
    rest = [a for a in args[args.index(sub) + 1:] if a != "--json"]
    return bool(rest) and rest[0] in HC_INFO_FLAGS | {"read", "help"}


HC_DENIED = {"run", "send", "kill", "rm"}
HC_ASKED = {"keys", "interrupt"}
MUTATING_TOOLS = {"spawn", "message", "resume", "stop", "report"}
TOOL_PREFIX = "mcp__plugin_agent-runtime_agent-runtime__"


def verdict(command, is_subagent):
    """(decision, reason) or None."""
    if not isinstance(command, str):
        command = json.dumps(command)
        if re.search(r"\bhc\b", command):
            return "deny", "Cannot parse this command and it mentions hc: use the agent-runtime tools."
        return None
    try:
        list(segments(command))
    except ValueError:
        if re.search(r"\bhc\b", command):
            return "deny", "Cannot parse this command and it mentions hc: use the agent-runtime tools."
    result = None
    for program, args in invocations(command):
        if program != "hc":
            continue
        sub = hc_subcommand(args)
        if sub in HC_ASKED and not is_subagent:
            result = result or ("ask", "approve to press keys in a worker")
        elif sub in HC_DENIED | HC_ASKED or (sub == "file" and not file_read_only(args, sub)):
            return "deny", "Do not run 'hc %s' in Bash: use the agent-runtime tools." % sub
    return result


def tool_verdict(tool_name, is_subagent):
    if is_subagent and tool_name.startswith(TOOL_PREFIX) and tool_name[len(TOOL_PREFIX):] in MUTATING_TOOLS:
        return ("Subagents must not call the agent-runtime %s tool: report through your return value."
                % tool_name[len(TOOL_PREFIX):])
    return None


def main(argv=()):
    try:
        data = json.load(sys.stdin)
        name = data.get("tool_name", "Bash")
        if name == "Bash":
            result = verdict(data.get("tool_input", {}).get("command", ""), bool(data.get("agent_id")))
        elif "codex" in argv:
            return 0
        else:
            reason = tool_verdict(name, bool(data.get("agent_id")))
            result = ("deny", reason) if reason else None
        if result:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                     "permissionDecision": result[0],
                                                     "permissionDecisionReason": result[1]}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
