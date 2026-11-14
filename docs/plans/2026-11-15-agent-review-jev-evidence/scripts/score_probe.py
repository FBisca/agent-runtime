"""Score-instrument run: the 6 surviving questions as Severity scores (0-4) with confidence.

Same 12 cases, same chunking, same state as the noul comparison. Jev returns, per question, a score,
a confidence, and the probability of each level — the second axis the noul answers do not have.

CAVEAT: the level descriptions were authored after seeing the noul results, so this is a new instrument
version (D26: a new version is a new run). The 12 cases are now both authoring and measuring input, so
treat the numbers as a design check, not a measurement.
"""

import json
import statistics as st
from pathlib import Path

import compare
import probe

OUT = Path(__file__).with_name("runs") / "scores"

# severity ladder shared by every question: 0 = the question holds, 4 = the worst class of violation
LEVELS = {
    "contract.error-handling": [
        "no new failure path at a boundary, or each one raises an actionable error",
        "a new failure path's error is vague but a caller can still act on it",
        "a new failure is handled by a caller-visible fallback with no signal of what failed",
        "a new failure at a boundary is swallowed or replaced by a silent default",
        "a new failure crashes, or reports success while the operation failed",
    ],
    "contract.requirement-traceability": [
        "every altered behaviour is named by its requirement or its cause in the change",
        "one altered behaviour is traceable only by inference",
        "an altered behaviour has no requirement or cause, though the change is self-evident",
        "an altered behaviour silently contradicts or replaces a stated requirement",
        "the change alters a documented contract with nothing naming the requirement it breaks",
    ],
    "contract.test-evidence": [
        "each altered behaviour has a test that would fail without the change",
        "most altered behaviours are covered; one branch or assertion is untested",
        "a behaviour is altered and only covered indirectly",
        "an altered behaviour has no test that would fail without the change",
        "a fix or behaviour change ships with no test, so the regression it addresses can return unseen",
    ],
    "engineering.abstraction-levels": [
        "each unit reads at one level",
        "a helper mixes one low-level detail into an otherwise high-level unit",
        "a function mixes policy and mechanism in a way a reader must untangle",
        "levels are mixed so the unit cannot be read as one thing",
        "behaviour is spread across unrelated layers with no discernible level",
    ],
    "engineering.hidden-state": [
        "state is explicit and no logic branches on three or more booleans",
        "one state flag is implicit but locally contained",
        "state is carried implicitly across a boundary",
        "logic branches on three or more booleans, or state accumulates across units",
        "hidden state is shared across units and its transitions are unobservable",
    ],
    "engineering.survival-priority": [
        "failure modes leave the system available and correct",
        "a failure mode is handled but degrades one feature",
        "a failure mode leaves state inconsistent until someone intervenes",
        "a failure mode takes down a unit or corrupts state silently",
        "a failure mode destroys data or takes the whole system down",
    ],
}

QUESTIONS = [
    (
        "contract.error-handling",
        "critical",
        "How severely does the change violate: every new failure at a boundary surfaces an error a caller can act on",
    ),
    (
        "contract.requirement-traceability",
        "critical",
        "How severely does the change violate: every behaviour it alters traces to a stated requirement or a stated cause",
    ),
    (
        "contract.test-evidence",
        "high",
        "How severely does the change violate: a test would fail without the change, for each behaviour it alters",
    ),
    (
        "engineering.abstraction-levels",
        "advisory",
        "How severely does the change violate: each unit keeps to one level of abstraction",
    ),
    (
        "engineering.hidden-state",
        "high",
        "How severely does the change violate: state stays explicit, and no logic branches on three or more booleans",
    ),
    (
        "engineering.survival-priority",
        "critical",
        "How severely does the change violate: the running system stays available and correct through its own failure modes",
    ),
]

WEIGHT = {"critical": 3, "high": 2, "advisory": 1}


def ask(chunk):
    payload = {
        "model": probe.MODEL,
        "state": {"base": "case", "head": "case", "diff": chunk},
        "questions": {
            qid: {"type": "score", "instructions": instructions, "criteria": LEVELS[qid]}
            for qid, _priority, instructions in QUESTIONS
        },
    }
    return probe.post(payload)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--cases", default="")
    args = parser.parse_args()
    wanted = [c for c in compare.CASES if not args.cases or c[0] in args.cases.split(",")]
    OUT.mkdir(parents=True, exist_ok=True)
    for case in wanted:
        name, kind, base, head, note = case
        parts = compare.chunks(base, head)
        for repeat in range(1, args.repeats + 1):
            answers, usage, errors = {}, {"input_tokens": 0, "output_tokens": 0}, []
            elapsed = 0.0
            for chunk in parts:
                result = ask(chunk)
                elapsed += result.get("elapsed") or 0
                if result.get("status") != 200:
                    errors.append(f"{result.get('status')} {str(result.get('body'))[:150]}")
                    continue
                body = json.loads(result["body"])
                u = body.get("usage") or {}
                usage["input_tokens"] += u.get("input_tokens") or 0
                usage["output_tokens"] += u.get("output_tokens") or 0
                for qid, answer in body["answers"].items():
                    previous = answers.get(qid) or {
                        "score": -1,
                        "confidence": 1.0,
                        "probabilities": [],
                    }
                    # a question answered in several chunks takes its worst score
                    if answer.get("score", 0) > previous["score"]:
                        answers[qid] = {
                            "score": answer.get("score"),
                            "confidence": answer.get("confidence"),
                            "probabilities": answer.get("probabilities"),
                        }
            (OUT / f"{name.replace(chr(47), chr(45))}.r{repeat}.json").write_text(
                json.dumps(
                    {
                        "case": name,
                        "kind": kind,
                        "note": note,
                        "answers": answers,
                        "usage": usage,
                        "elapsed": round(elapsed, 2),
                        "errors": errors,
                    },
                    indent=1,
                )
            )
            low = sorted(((a.get("score") or 0), q) for q, a in answers.items())[-3:]
            conf = st.mean([a.get("confidence") or 0 for a in answers.values()]) if answers else 0
            print(
                f"{name:<20}{kind:<6}r{repeat}  {len(answers)}/6  conf {conf:.2f}  "
                f"top severity: {', '.join(f'{q.split(chr(46))[1]}={s:.2f}' for s, q in reversed(low))}"
                f"  {' '.join(errors)}"
            )


if __name__ == "__main__":
    main()
