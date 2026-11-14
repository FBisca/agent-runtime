"""Understanding Jev, batch A: does the scale respond to the state at all, and where is its floor?

Four states, the same 6 severity questions and the same 6 as nouls:
  empty   {}                       — nothing to judge
  blank   {diff: ""}               — a diff that is empty
  benign  a 3-line comment change  — a change that violates nothing
  rmtree  a known-bad change       — the unguarded delete, fixed by 109c98b

If empty/blank/benign do not go to ~0, every score is anchored above zero and the scale is measuring
"something is always wrong", not "how wrong is this change".
"""

import json
import statistics as st
from pathlib import Path

import probe
import score_probe as sp

OUT = Path(__file__).with_name("runs") / "understanding"
BENIGN = """diff --git a/packages/agent-review/src/agent_review/questions.py b/packages/agent-review/src/agent_review/questions.py
--- a/packages/agent-review/src/agent_review/questions.py
+++ b/packages/agent-review/src/agent_review/questions.py
@@ -1,3 +1,3 @@
-\"\"\"The configured question bank: one model and the curated, stable questions it asks.\"\"\"
+\"\"\"The configured question bank: the model and the curated, stable questions it asks.\"\"\"
 \n
 import re
"""

SCORE_QUESTIONS = {
    qid: {"type": "score", "instructions": instructions, "criteria": sp.LEVELS[qid]}
    for qid, _p, instructions in sp.QUESTIONS
}
NOUL_QUESTIONS = {
    qid: {"type": "noul",
          "instructions": "Does the change satisfy: " + instructions.split("violate: ")[-1],
          "criteria": {"true": "the change satisfies it", "false": "the change violates it"}}
    for qid, _p, instructions in sp.QUESTIONS
}


def ask(state, questions):
    result = probe.post({"model": probe.MODEL, "state": state, "questions": questions})
    if result.get("status") != 200:
        return {"error": f"{result.get('status')} {str(result.get('body'))[:160]}"}
    body = json.loads(result["body"])
    return {"answers": body["answers"], "usage": body.get("usage"), "elapsed": result.get("elapsed")}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rmtree = probe.diff("429625b^", "429625b")
    states = {
        "empty": {},
        "blank": {"diff": ""},
        "benign": {"diff": BENIGN},
        "rmtree": {"base": "429625b^", "head": "429625b", "diff": rmtree},
    }
    summary = {}
    for name, state in states.items():
        for kind, questions in (("score", SCORE_QUESTIONS), ("noul", NOUL_QUESTIONS)):
            result = ask(state, questions)
            (OUT / f"floor.{name}.{kind}.json").write_text(json.dumps(result, indent=1))
            if "error" in result:
                print(f"{name:<8}{kind:<6}ERROR {result['error']}")
                continue
            if kind == "score":
                values = {q: a["score"] for q, a in result["answers"].items()}
                conf = st.mean(a["confidence"] for a in result["answers"].values())
                print(f"{name:<8}{kind:<6}mean severity {st.mean(values.values()):.2f}  "
                      f"max {max(values.values()):.2f}  conf {conf:.2f}  "
                      f"usage {result['usage']['input_tokens']} in")
            else:
                values = {q: a["noul"] for q, a in result["answers"].items()}
                print(f"{name:<8}{kind:<6}mean P(true) {st.mean(values.values()):.2f}  "
                      f"min {min(values.values()):.2f}  usage {result['usage']['input_tokens']} in")
            summary[f"{name}.{kind}"] = values
    (OUT / "floor-summary.json").write_text(json.dumps(summary, indent=1))
    print("\nper question (severity):")
    names = list(states)
    print(f"{'question':<45}" + "".join(f"{n:>9}" for n in names))
    for q in SCORE_QUESTIONS:
        row = [summary.get(f"{n}.score", {}).get(q) for n in names]
        print(f"{q:<45}" + "".join(f"{v:>9.2f}" if isinstance(v, (int, float)) else f"{'-':>9}" for v in row))


if __name__ == "__main__":
    main()
