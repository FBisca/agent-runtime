#!/usr/bin/env python3
"""PreToolUse(Bash) guard: subagents may run only read-only hc subcommands; nobody may run teammux-launch."""

import json
import os
import shlex
import subprocess
import sys

HC_ALLOWED = {"get", "ls", "read", "wait", "templates", "events", "file", "exec", "schema"}
HC_INFO_FLAGS = {"--help", "-h", "--version"}
SEPARATORS = ";&|()\n"
KEYWORDS = {"then", "do", "else", "elif", "if", "while", "until", "{", "!", "time", "builtin",
            "command", "exec", "nohup", "env", "sudo"}
# wrapper -> options that consume a separate value; `positional` wrappers take one fixed arg
VALUE_FLAGS = {"timeout": {"-s", "-k", "--signal", "--kill-after"}, "nice": {"-n", "--adjustment"},
               "stdbuf": {"-i", "-o", "-e"}, "watch": {"-n", "--interval"},
               "xargs": {"-n", "-I", "-P", "-L", "-d", "-s", "-E", "-a"}, "env": {"-u", "-C"},
               "sudo": {"-u", "-g", "-C", "-h", "-p"}}
HC_PARENT_SENSITIVE = {"keys", "interrupt", "kill"}
HC_VALUE_OPTIONS = {"--if", "--timeout"}
HC_KNOWN_FLAGS = {"--json", "--force", "--wait", "-h", "--help"}
HC_TIMEOUT_SECONDS = 2
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


def invocations(command, depth=0):
    """Yield (program basename, args) for each simple command, descending into sh -c."""
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


def help_only(args, sub):
    rest = args[args.index(sub) + 1:]
    return bool(rest) and rest[0] in HC_INFO_FLAGS


def sensitive_target(args):
    """(target, tokens, uncertain) of a parent-sensitive hc call (keys/interrupt/kill/send --force).

    None when the call is not sensitive. `target` is "" when none is named. `uncertain` is True
    when an option hc does not document precedes the target, so the target may be an option value;
    `tokens` are then all words after the subcommand."""
    sub = hc_subcommand(args)
    if sub is None:
        return None
    rest = args[args.index(sub) + 1:]
    if sub == "send":
        if "--force" not in rest:
            return None
    elif sub not in HC_PARENT_SENSITIVE:
        return None
    skip = False
    uncertain = False
    for arg in rest:
        if skip:
            skip = False
        elif arg in HC_VALUE_OPTIONS:
            skip = True
        elif arg.startswith("-"):
            if arg not in HC_KNOWN_FLAGS:
                uncertain = True
        elif not uncertain:
            return arg, rest, False
        else:
            return arg, rest, True
    return "", rest, uncertain


def hc_get(terminal):
    try:
        result = subprocess.run(["hc", "get", terminal], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=HC_TIMEOUT_SECONDS)
        reply = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return reply if isinstance(reply, dict) else None


def is_worker():
    own = os.environ.get("HC_TERMINAL_ID")
    me = hc_get(own) if own else None
    return bool(me) and isinstance(me.get("parent"), str) and bool(me["parent"])


def targets_parent(target, tokens=(), uncertain=False):
    """True when this hc worker's parent is the target (fails open when unresolvable).

    When the target is uncertain, deny only if the parent's address or name appears in tokens."""
    own = os.environ.get("HC_TERMINAL_ID")
    if not own or not (target or uncertain):
        return False
    me = hc_get(own)
    parent = me.get("parent") if me else None
    if not isinstance(parent, str) or not parent:
        return False
    if uncertain:
        parent_info = hc_get(parent) or {}
        names = {parent, parent_info.get("name")} - {None}
        return any(token in names for token in tokens)
    if target == parent:
        return True
    reply = hc_get(target)
    return bool(reply) and reply.get("address") == parent


def verdict(command, is_subagent):
    sensitive = []
    for program, args in invocations(command):
        if program == "herdmates" and args[:1] == ["teammux-launch"]:
            return "herdmates teammux-launch is not allowed inside an agent session."
        if program != "hc":
            continue
        if is_subagent:
            sub = hc_subcommand(args)
            if sub == "file" and not file_read_only(args, sub):
                return ("Subagents may only run 'hc file read': file write and edit change the "
                        "lead's files. Report through your return value or SendMessage.")
            if sub is not None and sub not in HC_ALLOWED and not help_only(args, sub):
                return ("Subagents must not run 'hc %s': it could act on the lead's terminal. "
                        "Only read-only hc commands (%s) are allowed; report through your "
                        "return value or SendMessage." % (sub, ", ".join(sorted(HC_ALLOWED))))
        else:
            call = sensitive_target(args)
            if call is not None:
                sensitive.append(call)
    for target, tokens, uncertain in sensitive:
        if is_worker() and any(c in target for c in "$`"):
            return ("Workers must give hc keys, interrupt, kill and send --force a literal target: "
                    "the target must be a literal address or name, not a shell expansion.")
        if targets_parent(target, tokens, uncertain):
            return ("Workers must not run keys, interrupt, kill or send --force at their parent: "
                    "it can answer the parent's dialog. Use 'hc send <parent> <result>' to report.")
    return None


def main(argv=()):
    """`--cli codex` selects the Codex payload: same tool_input.command and deny JSON as Claude,
    but only the Bash tool is inspected and there is no agent_id marker for subagents."""
    try:
        data = json.load(sys.stdin)
        if "codex" in argv and data.get("tool_name", "Bash") != "Bash":
            return 0
        command = data.get("tool_input", {}).get("command", "")
        reason = verdict(command, bool(data.get("agent_id")))
        if reason:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                     "permissionDecision": "deny",
                                                     "permissionDecisionReason": reason}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
