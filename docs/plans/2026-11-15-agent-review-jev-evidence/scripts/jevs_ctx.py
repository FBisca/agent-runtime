"""Long-context mode: what it costs in accuracy, measured against the chunked default."""

import json
import statistics as st

import probe
import score_probe as sp

LONG = {"TypeSafe-Danger-Zone-Long-Context": "true"}
Q = {
    qid: {"type": "score", "instructions": i, "criteria": sp.LEVELS[qid]}
    for qid, p, i in sp.QUESTIONS
}


def ask(diff, questions, headers=None):
    payload = {
        "model": probe.MODEL,
        "state": {"base": "7456440", "head": "72c4da9", "diff": diff},
        "questions": questions,
    }
    r = probe.post(payload, headers=headers)
    if r.get("status") != 200:
        return {
            "error": f"{r.get('status')} {str(r.get('body'))[:120]}",
            "elapsed": r.get("elapsed"),
        }
    body = json.loads(r["body"])
    return {"answers": body["answers"], "usage": body.get("usage"), "elapsed": r.get("elapsed")}


full = probe.diff("7456440", "72c4da9")
print(f"silent-idle full diff: {len(full)} bytes")
for label, headers in (("no header", None), ("long-context", LONG)):
    out = ask(full, Q, headers)
    if "error" in out:
        print(f"  {label:<13} {out['error']}")
    else:
        sev = {q: a["score"] for q, a in out["answers"].items()}
        conf = st.mean(a["confidence"] for a in out["answers"].values())
        print(
            f"  {label:<13} ok  {out['elapsed']}s  input_tokens {out['usage']['input_tokens']}  "
            f"mean severity {st.mean(sev.values()):.2f}  conf {conf:.2f}"
        )
        print("      " + ", ".join(f"{q.split('.')[-1]}={v:.2f}" for q, v in sev.items()))

print("\n== the same 21-question noul bank, one long request")
noul = {q.id: {"type": "noul", "instructions": q.ask} for q in probe.bank().questions}
out = ask(full, noul, LONG)
if "error" in out:
    print("  ", out["error"])
else:
    print(
        f"  ok  {out['elapsed']}s  input_tokens {out['usage']['input_tokens']}  answered {len(out['answers'])}/21  "
        f"mean P(true) {st.mean(a['noul'] for a in out['answers'].values()):.2f}"
    )

# how far can the header take us? a synthetic 400KB, 800KB state
for size in (400_000, 800_000):
    out = ask(
        full[:size] if size < len(full) else full + "\n" * (size - len(full)),
        {"x.h": Q["engineering.hidden-state"]},
        LONG,
    )
    if "error" in out:
        print(f"  {size // 1000}KB -> {out['error']}")
    else:
        print(f"  {size // 1000}KB -> ok  input_tokens {out['usage']['input_tokens']}")
