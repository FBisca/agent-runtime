"""Understanding Jev, batch B: framing, criteria, batch effects, repeat noise.

B framing    the same principle as a positive noul and as a negative score, 3 repeats each
C criteria   the same score question with and without criteria, 3 repeats each
D batch      the 6 questions in one request vs each alone, on the same state
E noise      5 identical requests, per-question spread
"""

import json
import statistics as st
from pathlib import Path

import probe
import score_probe as sp

OUT = Path(__file__).with_name("runs") / "understanding"
RMTREE = ("429625b^", "429625b")
CHECKS = ("e9bd1ad^", "e9bd1ad")


def state(base, head):
    return {"base": base, "head": head, "diff": probe.diff(base, head)}


def ask(questions, st_):
    result = probe.post({"model": probe.MODEL, "state": st_, "questions": questions})
    if result.get("status") != 200:
        return {"error": f"{result.get('status')} {str(result.get('body'))[:150]}"}
    return json.loads(result["body"])["answers"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rmtree, checks = state(*RMTREE), state(*CHECKS)

    print(
        "== B framing: 'does the change satisfy X' (noul) vs 'how severely does it violate X' (score)"
    )
    principle = "each unit of the change keeps to one level of abstraction"
    noul = {
        "x.abstraction": {"type": "noul", "instructions": f"Does the change satisfy: {principle}"}
    }
    score = {
        "x.abstraction": {
            "type": "score",
            "instructions": f"How severely does the change violate: {principle}",
            "criteria": sp.LEVELS["engineering.abstraction-levels"],
        }
    }
    for label, st_ in (("rmtree", rmtree), ("checks", checks)):
        n = [ask(noul, st_)["x.abstraction"]["noul"] for _ in range(3)]
        s = [ask(score, st_)["x.abstraction"]["score"] for _ in range(3)]
        print(
            f"  {label:<8} noul P(true) {st.mean(n):.2f} (sd {st.pstdev(n):.2f})   "
            f"score {st.mean(s):.2f}/4 (sd {st.pstdev(s):.2f})   implied severity from noul {(1 - st.mean(n)) * 4:.2f}"
        )

    print("\n== C criteria richness: terse ladder vs the authored 5 levels (checks change)")
    terse = {
        "x.hidden": {
            "type": "score",
            "instructions": "How severely does the change violate: state stays explicit, and no logic branches on three or more booleans",
            "criteria": ["none", "minor", "moderate", "major", "severe"],
        }
    }
    rich = {
        "x.hidden": {
            "type": "score",
            "instructions": "How severely does the change violate: state stays explicit, and no logic branches on three or more booleans",
            "criteria": sp.LEVELS["engineering.hidden-state"],
        }
    }
    a = [ask(terse, checks)["x.hidden"]["score"] for _ in range(3)]
    b = [ask(rich, checks)["x.hidden"]["score"] for _ in range(3)]
    print(
        f"  terse ladder {st.mean(a):.2f}/4 (sd {st.pstdev(a):.2f})   authored levels {st.mean(b):.2f}/4 (sd {st.pstdev(b):.2f})"
    )

    print("\n== D batch: 6 questions in one request vs each alone (rmtree)")
    all_q = {
        qid: {"type": "score", "instructions": i, "criteria": sp.LEVELS[qid]}
        for qid, _p, i in sp.QUESTIONS
    }
    batched = ask(all_q, rmtree)
    print(f"  {'question':<45}{'batched':>8}{'alone':>7}{'delta':>7}")
    for qid in all_q:
        alone = ask({qid: all_q[qid]}, rmtree)[qid]["score"]
        print(
            f"  {qid:<45}{batched[qid]['score']:>8.2f}{alone:>7.2f}{alone - batched[qid]['score']:>+7.2f}"
        )

    print("\n== E noise: 5 identical requests (rmtree), per-question spread")
    runs = [ask(all_q, rmtree) for _ in range(5)]
    print(f"  {'question':<45}{'mean':>7}{'sd':>6}{'range':>9}")
    spreads = []
    for qid in all_q:
        values = [r[qid]["score"] for r in runs]
        spreads.append(st.pstdev(values))
        print(
            f"  {qid:<45}{st.mean(values):>7.2f}{st.pstdev(values):>6.2f}{max(values) - min(values):>9.2f}"
        )
    confs = [st.mean(v["confidence"] for v in r.values()) for r in runs]
    print(
        f"  mean confidence across the 5 requests: {st.mean(confs):.2f} (sd {st.pstdev(confs):.2f})"
    )
    print(
        f"  mean per-question sd: {st.mean(spreads):.2f} on a 0-4 scale ({(st.mean(spreads) / 4) * 100:.0f}% of the scale)"
    )


if __name__ == "__main__":
    main()
