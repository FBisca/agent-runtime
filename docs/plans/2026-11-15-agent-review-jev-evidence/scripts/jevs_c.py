"""Understanding Jev, batch C: truncation and Choice."""

import json

import probe
import score_probe as sp

Q = {
    q: {"type": "score", "instructions": i, "criteria": sp.LEVELS[q]}
    for q, p, i in sp.QUESTIONS
    if q in ("engineering.hidden-state", "contract.error-handling", "contract.test-evidence")
}


def ask(state, questions=Q):
    result = probe.post({"model": probe.MODEL, "state": state, "questions": questions})
    if result.get("status") != 200:
        return {"error": f"{result.get('status')} {str(result.get('body'))[:120]}"}
    return json.loads(result["body"])["answers"]


def main():
    full = probe.diff("7456440", "72c4da9")
    print(f"silent-idle diff: {len(full)} bytes")
    marked = {"base": "7456440", "head": "72c4da9", "diff": full[:110000] + "\n[truncated]"}
    unmarked = {"base": "7456440", "head": "72c4da9", "diff": full[:110000]}
    a, b = ask(marked), ask(unmarked)
    print("\n== truncation: a marker appended vs the same 110KB without it")
    for q in Q:
        print(
            f"  {q:<40} marked {a[q]['score']:.2f} (conf {a[q]['confidence']:.2f})   "
            f"unmarked {b[q]['score']:.2f} (conf {b[q]['confidence']:.2f})"
        )

    choice = {
        "x.kind": {
            "type": "choice",
            "instructions": "What is the dominant kind of this change?",
            "criteria": {
                "behaviour": "it changes what the system does",
                "refactor": "it restructures code with no behaviour change",
                "test": "it changes tests or evidence only",
                "docs": "it changes documentation or configuration only",
                "dependency": "it mainly bumps or adds dependencies",
            },
        }
    }
    cases = [
        (
            "rmtree (bad)",
            {"base": "429625b^", "head": "429625b", "diff": probe.diff("429625b^", "429625b")},
        ),
        (
            "review-cli (clean)",
            {"base": "d2036c8", "head": "433509a", "diff": probe.diff("d2036c8", "433509a")},
        ),
        (
            "docs-only",
            {
                "diff": "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@\n-x\n+y\n"
            },
        ),
    ]
    print("\n== Choice: P1-D3's applicability question is a Choice")
    for label, state in cases:
        answer = ask(state, choice)["x.kind"]
        probs = {k: round(v, 2) for k, v in answer["probabilities"].items()}
        print(
            f"  {label:<20} choice {answer['choice']:<10} conf {answer['confidence']:.2f}  {probs}"
        )


if __name__ == "__main__":
    main()
