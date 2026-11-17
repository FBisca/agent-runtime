"""Probe: can Jev (TypeSafe System One) screen a real diff with the packaged agent-review bank?

Not part of the repository. Answers four questions before any plan is written:
  B3  does one probability per boolean question carry enough signal?
  B4  does the response name the model/version the provider actually served?
  B6  does a real 2028-line diff fit the 64k window, and at what latency?
  B?  is `criteria` mandatory on a `noul` question?

The key comes from TYPESAFE_API_KEY in the environment (sourced by zsh); it is never printed.
"""

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path("/Users/fbisca/developer/agent-runtime")
OUT = Path(__file__).with_name("runs")
URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

# Criteria per bank id (D49: criteria are authored, not generated). true = the question is satisfied.
CRITERIA = {
    "contract.requirement-traceability": (
        "every behaviour the change alters is named by a requirement, a cause or a test in the change",
        "a behaviour is altered with nothing in the change naming its requirement, cause or test",
    ),
    "contract.error-handling": (
        "a new failure at a boundary raises an error the caller can act on",
        "a failure is swallowed, silently defaulted, or crashes with no actionable error",
    ),
    "contract.public-surface-compatibility": (
        "flags, exit codes, file formats and schemas keep the meaning existing callers rely on",
        "a caller-visible surface changes meaning with no new name and no migration",
    ),
    "contract.test-evidence": (
        "each behaviour the change alters is covered by a test that would fail without the change",
        "a behaviour is altered and no test in the change would fail without it",
    ),
    "contract.missing-context": (
        "the change states the context it could not see rather than assuming it",
        "an unknown is treated as satisfied",
    ),
    "engineering.survival-priority": (
        "the running system stays available and correct through the change's own failure modes",
        "a failure mode takes the system down, corrupts state, or reports success wrongly",
    ),
    "engineering.traceable-cause": (
        "each new behaviour can be traced to its cause without guessing",
        "a behaviour's cause is not visible, so a reader must guess why it happens",
    ),
    "engineering.durable-intent": (
        "the reason the behaviour exists is recorded where it outlives the author",
        "the behaviour's reason exists only in the author's head",
    ),
    "engineering.external-legibility": (
        "the change's internal state is observable from outside through a signal",
        "a state transition happens with no event, field or record a reader can see",
    ),
    "engineering.type-contracts": (
        "declared types constrain what the code accepts and returns",
        "a type is an annotation that would accept anything",
    ),
    "engineering.implicit-named": (
        "the assumptions the change depends on are named",
        "an assumption the change depends on is left implicit",
    ),
    "engineering.understand-before-compressing": (
        "the logic is written at its own level and can be read in order",
        "behaviour is compressed into an expression whose result must be unpacked",
    ),
    "engineering.hard-decisions": (
        "the unavoidable hard decision is made in the change",
        "the decision is deferred to a caller or to a later phase",
    ),
    "engineering.elegance": (
        "the change is shaped like the problem it solves, without incidental complexity",
        "incidental complexity is added that the problem does not require",
    ),
    "engineering.new-concepts": (
        "every concept the change introduces is needed by it",
        "a concept the change does not need is introduced",
    ),
    "engineering.hidden-state": (
        "state is explicit, and no logic branches on three or more booleans",
        "state accumulates implicitly, or logic branches on three or more booleans",
    ),
    "engineering.names-meaning": (
        "new names say what the thing means",
        "a name says how the thing is implemented rather than what it means",
    ),
    "engineering.abstraction-levels": (
        "each unit of the change keeps to one level of abstraction",
        "levels of abstraction are mixed inside one unit",
    ),
    "engineering.composition": (
        "the change composes what already exists",
        "another mechanism is created for something that already exists",
    ),
    "engineering.consequence-not-achievement": (
        "a one-liner is present because it follows from the problem",
        "a one-liner is present as an achievement in itself",
    ),
    "engineering.threshold": (
        "the change states the threshold it is good enough for",
        "better is treated as a direction with no threshold",
    ),
}


def bank():
    """The packaged bank, read through the package itself so the probe cannot drift from it."""
    import sys

    sys.path.insert(0, str(REPO / "packages/agent-review/src"))
    from agent_review.questions import baseline

    return baseline()


def diff(base, head):
    done = subprocess.run(
        ["git", "-C", str(REPO), "diff", "--no-ext-diff", "--no-textconv", f"{base}..{head}"],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise SystemExit(f"git diff failed: {done.stderr.strip()}")
    return done.stdout


def post(payload, timeout=180.0, headers=None):
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "TYPESAFE_API_KEY is not in the environment; run this under zsh with the secrets sourced"
        )
    request = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "content-type": "application/json",
            **(headers or {}),
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        status = error.code
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"status": None, "elapsed": time.monotonic() - started, "error": str(error)[:400]}
    return {
        "status": status,
        "elapsed": round(time.monotonic() - started, 2),
        "body": body,
    }


def questions(bank_questions, include_criteria=True):
    asked = {}
    for question in bank_questions:
        if include_criteria:
            true, false = CRITERIA[question.id]
            asked[question.id] = {
                "type": "noul",
                "instructions": question.ask,
                "criteria": {"true": true, "false": false},
            }
        else:
            asked[question.id] = {"type": "noul", "instructions": question.ask}
    return asked


def report(label, result, payload, ids):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{label}.payload.json").write_text(json.dumps(payload, indent=1))
    lines = [
        f"== {label}",
        f"sent: {len(json.dumps(payload))} bytes, questions: {len(ids)}",
        f"status: {result.get('status')}  elapsed: {result.get('elapsed')}s",
    ]
    if "error" in result:
        lines.append(f"transport error: {result['error']}")
        print("\n".join(lines))
        return None
    try:
        body = json.loads(result["body"])
    except json.JSONDecodeError:
        print("\n".join(lines) + f"\nnot JSON: {result['body'][:600]}")
        return None
    (OUT / f"{label}.response.json").write_text(json.dumps(body, indent=1))
    lines.append(f"response keys: {sorted(body)}")
    answers = body.get("answers") or {}
    if isinstance(answers, dict):
        got = set(answers)
        lines.append(
            f"answered: {len(got)}/{len(ids)}"
            + (f"  missing: {sorted(set(ids) - got)}" if set(ids) - got else "")
        )
        for name, answer in answers.items():
            lines.append(f"  {name}: {json.dumps(answer)}")
    lines.append(f"usage: {json.dumps(body.get('usage'))}")
    if result.get("status") != 200:
        lines.append(f"body: {result['body'][:800]}")
    print("\n".join(lines))
    return body


def main():  # noqa: PLR0912, PLR0915
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=["tiny", "nocriteria", "full", "explicit", "limits", "nodiff", "chunks"]
    )
    args = parser.parse_args()
    bank_questions = bank().questions

    if args.mode == "tiny":
        ids = ["contract.error-handling", "engineering.traceable-cause"]
        chosen = [q for q in bank_questions if q.id in ids]
        payload = {
            "model": MODEL,
            "state": {"base": "0bde508", "head": "0bde508^", "diff": diff("0bde508^", "0bde508")},
            "questions": questions(chosen),
        }
        print(f"change: 0bde508^..0bde508  diff bytes: {len(payload['state']['diff'])}")
        report("tiny", post(payload), payload, ids)

    elif args.mode == "nocriteria":
        ids = ["contract.error-handling"]
        chosen = [q for q in bank_questions if q.id in ids]
        payload = {
            "model": MODEL,
            "state": {"base": "0bde508", "head": "0bde508^", "diff": diff("0bde508^", "0bde508")},
            "questions": questions(chosen, include_criteria=False),
        }
        report("nocriteria", post(payload), payload, ids)

    elif args.mode == "nodiff":
        ids = [q.id for q in bank_questions]
        payload = {
            "model": MODEL,
            "state": {"base": "7456440", "head": "72c4da9"},
            "questions": questions(bank_questions),
        }
        print("no diff: the question overhead alone")
        report("nodiff", post(payload), payload, ids)

    elif args.mode == "chunks":
        base, head = "7456440", "72c4da9"
        pieces = ["diff --git " + part for part in diff(base, head).split("diff --git ")[1:]]
        caps, current = [], ""
        for piece in pieces:
            if current and len(current) + len(piece) > 100_000:
                caps.append(current)
                current = ""
            current += piece
        caps.append(current)
        ids = [q.id for q in bank_questions]
        print(
            f"change {base}..{head}: {len(pieces)} files -> {len(caps)} chunks "
            f"({[len(c) for c in caps]} bytes)"
        )
        results = []
        for index, chunk in enumerate(caps, start=1):
            payload = {
                "model": MODEL,
                "state": {"base": base, "head": head, "diff": chunk},
                "questions": questions(bank_questions),
            }
            body = report(f"chunk{index}", post(payload), payload, ids)
            if body is None:
                return
            results.append({name: answer.get("noul") for name, answer in body["answers"].items()})
        print("== per-question, min across chunks (low = the change fails the question)")
        for name in ids:
            values = [r.get(name) for r in results]
            print(f"  {min(v for v in values if v is not None):.3f}  {name}  chunks={values}")
        return

    elif args.mode == "limits":
        base, head = "7456440", "72c4da9"
        change = diff(base, head)
        ids = [q.id for q in bank_questions]
        for cap in (130_000, 110_000, 90_000, 70_000, 60_000, 50_000, 40_000, 30_000):
            payload = {
                "model": MODEL,
                "state": {"base": base, "head": head, "diff": change[:cap] + "\n[truncated]"},
                "questions": questions(bank_questions),
            }
            result = post(payload)
            if result.get("status") == 200:
                body = json.loads(result["body"])
                usage = body.get("usage") or {}
                print(
                    f"cap {cap:>7} bytes -> 200  elapsed {result['elapsed']}s  input_tokens {usage.get('input_tokens')}"
                )
            else:
                print(
                    f"cap {cap:>7} bytes -> {result.get('status')}  {str(result.get('body'))[:120]}"
                )
        return

    else:
        base, head = "7456440", "72c4da9"
        change = diff(base, head)
        ids = [q.id for q in bank_questions]
        payload = {
            "model": MODEL,
            "state": {"base": base, "head": head, "diff": change},
            "questions": questions(bank_questions),
        }
        print(f"change: {base}..{head}  diff bytes: {len(change)}")
        report(args.mode, post(payload), payload, ids)


if __name__ == "__main__":
    main()
