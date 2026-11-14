"""Fair comparison: Jev (System One classifier) vs Codex (gpt-6.1-sol chat CLI).

Same input to both: the same 21 questions with the same true/false meanings, the same diff, cut
by the same deterministic chunking. Same output contract: one probability per question.

Not part of the repository. Artifacts under runs/compare/.
"""

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import probe

OUT = Path(__file__).with_name("runs") / "compare"
CAP = 100_000  # bytes per chunk: below Jev's measured ~112KB admission limit
CODEX_MODEL = "gpt-6.1-sol"
LUNA_MODEL = "gpt-6-luna"
DECISIONS = "https://api.openai.com/v1/decisions"

# kind: bad = a later fix commit names a defect this change introduced; clean = no fix names it.
CASES = [
    ("429625b-rmtree", "bad", "429625b^", "429625b", "fixed by 109c98b: rmtree ran outside plans.dir"),
    ("89bba2f-flaky", "bad", "89bba2f^", "89bba2f", "fixed by 3afe564: flaky rule matched by tree only"),
    ("e9bd1ad-checks", "bad", "e9bd1ad^", "e9bd1ad", "fixed by f953773: duplicate check, required command forbidden"),
    ("silent-idle", "bad", "7456440", "72c4da9", "agent-judge FAIL 0.70: missing event, missing test, idle answerer left working"),
    ("c239004-brief", "clean", "c239004^", "c239004", "no fix commit names it"),
    ("74addfe-rtk", "clean", "74addfe^", "74addfe", "no fix commit names it"),
    # widened: independent ground truth from the orchestrator's own run records
    ("engine-gaps/t4r1", "bad", "cb7f727b3d758e38ba878c928c462818ff6cc6b0",
     "d15a4f21637b2b420134bffa758cce8abaecb037", "a real check failed on this change: evidence S8"),
    ("plan-layout/t4r1", "bad", "100a6a2e2f0748092446b5e8d7b7ecc0faab00ad",
     "dece9630398b0476fb990a033884af9a47be6671", "a real check failed on this change: evidence S10"),
    ("stale-team/t1r1", "bad", "a8b4e01b4112713f2eddaec0674f53a457d15bc0",
     "dc9331d228ad10509647d7fa74f0b90d2eac8628", "a real check failed on this change: evidence S7"),
    ("check-tiers/t2", "clean", "fefcbb39847e42b01f36f32b4d70c30fcb506d7c",
     "98368ecc324e0b5734d7b2ddae1186034b8277ec", "COMPLETE, no blocking finding ever"),
    ("named-capture/t1", "clean", "5ba70fa8e7832d3543992351ede8a05e3a75145c",
     "ce3a0e7338b7e0c965145e1b1d6c73acc060a918", "COMPLETE, no blocking finding ever"),
    ("review-cli/t2", "clean", "d2036c8e4edcda9b72def5ad994b64b2d7f9c049",
     "433509a59a1ae9f37b428931bf7c1a67d41cd5b3", "COMPLETE, no blocking finding ever"),
]


def chunks(base, head):
    change = probe.diff(base, head)
    pieces = ["diff --git " + part for part in change.split("diff --git ")[1:]]
    packed, current = [], ""
    for piece in pieces:
        if current and len(current) + len(piece) > CAP:
            packed.append(current)
            current = ""
        current += piece
    packed.append(current)
    return packed


def canonical_question(question):
    true, false = probe.CRITERIA[question.id]
    return (
        f"- {question.id} ({question.priority}): {question.ask}\n"
        f"    True means: {true}\n"
        f"    False means: {false}"
    )


def prompt(bank, chunk, index, total):
    lines = [
        "You are a code-review classifier. For each question below, answer with the probability "
        "between 0 and 1 that the change satisfies it. Judge only from the change shown. Do not "
        "explain, do not use tools, do not write files.",
        "",
        'Output exactly one JSON object and nothing else: {"answers": {"<question id>": <probability>}}',
        "",
        f"## Questions ({len(bank)})",
    ]
    lines += [canonical_question(q) for q in bank]
    lines += ["", f"## The change, part {index} of {total} (git diff)", "```diff", chunk.rstrip("\n"), "```", ""]
    return "\n".join(lines)


def jev(bank, chunk, base, head):
    payload = {
        "model": probe.MODEL,
        "state": {"base": base, "head": head, "diff": chunk},
        "questions": {
            q.id: {
                "type": "noul",
                "instructions": f"{q.ask}\n\nTrue means: {probe.CRITERIA[q.id][0]}\nFalse means: {probe.CRITERIA[q.id][1]}",
            }
            for q in bank
        },
    }
    started = time.monotonic()
    result = probe.post(payload)
    if result.get("status") != 200:
        return {"error": f"{result.get('status')} {str(result.get('body'))[:200]}", "elapsed": result.get("elapsed")}
    body = json.loads(result["body"])
    return {
        "answers": {k: v.get("noul") for k, v in body["answers"].items()},
        "model": body.get("model"),
        "usage": body.get("usage"),
        "elapsed": result["elapsed"],
    }


def luna(bank, chunk, base, head, index=1, total=1):
    """gpt-6-luna through the headless Codex CLI: no OpenAI API key needed."""
    return codex(prompt(bank, chunk, index, total), model=LUNA_MODEL)


def codex(text, model=CODEX_MODEL):
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as cwd:
        try:
            done = subprocess.run(
                ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "-m", model, "-"],
                input=text,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            return {"error": "timeout", "elapsed": round(time.monotonic() - started, 1)}
    elapsed = round(time.monotonic() - started, 1)
    found = None
    for line in reversed(done.stdout.splitlines()):
        line = line.strip()
        if line.startswith('{"answers"'):
            try:
                found = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if found is None:
        return {"error": "no JSON object in stdout", "elapsed": elapsed, "tail": done.stdout[-300:]}
    answers = found.get("answers") or {}
    return {
        "answers": {k: v for k, v in answers.items() if isinstance(v, (int, float))},
        "elapsed": elapsed,
        "tokens": next((l for l in done.stdout.splitlines() if "tokens used" in l), None),
    }


def run(case, arm, repeat):
    name, kind, base, head, note = case
    bank = probe.bank().questions
    parts = chunks(base, head)
    per_chunk = []
    for index, chunk in enumerate(parts, start=1):
        text = prompt(bank, chunk, index, len(parts))
        result = {"jev": lambda: jev(bank, chunk, base, head),
                  "luna": lambda: luna(bank, chunk, base, head, index, len(parts)),
                  "codex": lambda: codex(text)}[arm]()
        result["chunk"] = index
        result["prompt_bytes"] = len(text)
        per_chunk.append(result)
        if "error" in result:
            print(f"  {name} {arm} r{repeat} chunk{index}: ERROR {result['error']}")
    merged = {}
    for result in per_chunk:
        for question, value in (result.get("answers") or {}).items():
            merged[question] = min(value, merged.get(question, 1.0))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name.replace(chr(47), chr(45))}.{arm}.r{repeat}.json").write_text(
        json.dumps({"case": name, "kind": kind, "base": base, "head": head, "note": note,
                    "arm": arm, "repeat": repeat, "chunks": per_chunk, "answers": merged}, indent=1)
    )
    return merged, per_chunk


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", default="jev,codex")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--cases", default="")
    args = parser.parse_args()
    arms = args.arms.split(",")
    wanted = [c for c in CASES if not args.cases or c[0] in args.cases.split(",")]
    for case in wanted:
        for arm in arms:
            for repeat in range(1, args.repeats + 1):
                merged, per_chunk = run(case, arm, repeat)
                answered = len(merged)
                elapsed = sum(c.get("elapsed") or 0 for c in per_chunk)
                low = sorted((v, k) for k, v in merged.items())[:3]
                print(f"{case[0]:<18} {case[1]:<6} {arm:<6} r{repeat}  {answered}/21  {elapsed}s  "
                      f"lowest: {', '.join(f'{k}={v:.2f}' for v, k in low)}")


if __name__ == "__main__":
    main()
