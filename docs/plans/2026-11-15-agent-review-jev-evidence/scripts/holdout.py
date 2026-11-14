"""Hold-out run: the Score instrument on 13 cases that took no part in authoring or calibration.

Thresholds are frozen from the calibration set (max severity >= S, or confidence < C). Nothing here is
tuned: the grid is printed afterwards only to show how sensitive the frozen choice is.
"""

import json
import statistics as st
from pathlib import Path

import compare
import score_probe as sp

OUT = Path(__file__).with_name("runs") / "holdout"

CASES = [
    # bad: a real acceptance check failed on this exact change (verify-sourced)
    ("plan-amend/t4r2", "bad", "ba52bb6fd807d1933fede19c87fcc67e05786c74", "1620b70bff09cbeed4dab86b1918d21e62565cee"),
    ("review-gate/t2r2", "bad", "f9537736d807fcd403b8e1cab543a2a7563f1bdc", "de6328e77e0ba0a61dee81602e2987626c1fe3c2"),
    ("yaml-eec/t8r3", "bad", "a16104a82df522ccefd583dbd16505ae1748ff8c", "7d0bf594c945d23f8c53ef12ae9caa91852a6c7a"),
    # clean: COMPLETE, no blocking finding ever raised, touches src/
    ("review-cli/t1", "clean", "4025d6938e6f7904c1ae0fd1dfb23ebf67d20f2a", "bf1bf9cda075cc1eeb49b792d1600fbd9b526be7"),
    ("label-evidence/t1", "clean", "9deb8f2ef140bcf05434815f9257ad1e75dc58a2", "87ffb48736b8ce24b81f291838bd42a3fa840101"),
    ("workbench/t1", "clean", "14254e5b9b807bb35692b857cc51484a0cada77b", "bcc8502d7cbc9518855fbefee1e0c685b0c3b091"),
    ("check-tiers/t1o1", "clean", "f14b74a2695fb036053220cfabac3945905909e1", "3bf5c3a6a7aef8cac48d2da6155824a0984e08b4"),
    ("check-tiers/t3", "clean", "e9bd1ad4fb18db4696a0713b250f8fc3f185e013", "dc5ed4d0ad58df9dca15486e47a1265b3986fc3a"),
    ("archive/t2", "clean", "ce4246f97980d87ac32c3f1946455f21bdddb152", "919b61f8b95faa2bd6ab09fa9761191d1f0b16c9"),
    ("gaps/t1", "clean", "cc8ba9bf377c099f41c2ef3cebd64158a923ed30", "55cf6c172df5f52962d25c98a0adebbfb208c96c"),
    ("gaps/t2", "clean", "cb7f727b3d758e38ba878c928c462818ff6cc6b0", "81069c1747177a3d3b3f832ed46379f92038a5ba"),
    ("gaps/t3", "clean", "cb7f727b3d758e38ba878c928c462818ff6cc6b0", "d57217452542234b30bc2fd381c7acde02729aeb"),
    ("gaps/t4", "clean", "cb7f727b3d758e38ba878c928c462818ff6cc6b0", "672ea7cf9572a32f1e3548e954c96c4ecdf4deb1"),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, kind, base, head in CASES:
        parts = compare.chunks(base, head)
        for repeat in (1, 2):
            answers = {}
            for chunk in parts:
                result = sp.ask(chunk)
                if result.get("status") != 200:
                    print(f"{name} r{repeat}: {result.get('status')} {str(result.get('body'))[:120]}")
                    continue
                for qid, answer in json.loads(result["body"])["answers"].items():
                    previous = answers.get(qid) or {"score": -1, "confidence": 1.0}
                    if answer.get("score", 0) > previous["score"]:
                        answers[qid] = {"score": answer.get("score"), "confidence": answer.get("confidence")}
            (OUT / f"{name.replace(chr(47), chr(45))}.r{repeat}.json").write_text(
                json.dumps({"case": name, "kind": kind, "answers": answers}, indent=1))
            worst = max((a["score"] or 0) for a in answers.values()) if answers else 0
            conf = st.mean(a["confidence"] or 0 for a in answers.values()) if answers else 0
            print(f"{name:<20}{kind:<6}r{repeat}  {len(answers)}/6  max severity {worst:.2f}  confidence {conf:.2f}")


if __name__ == "__main__":
    main()
