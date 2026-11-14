"""Per-question calibration: what each question does on verified-clean changes, and on bad ones.

Two instruments, two scales:
  noul   21 questions, 0-1, higher = the change satisfies the question (12 cases: 7 bad, 5 clean)
  score   6 questions, 0-4 severity, higher = worse (25 cases: 10 bad, 15 clean)

A question earns a threshold only if it separates. The rules below are provisional and are stated so the
next session can argue with them rather than guess at them.

Emitted: calibration.json (machine-readable), CALIBRATION.md (the table).
"""

import json
import pathlib
import statistics as st

import probe
import score_probe as sp

OUT = pathlib.Path(__file__).with_name("calibration.json")
N = 2  # repeats per case


def case_means(folder, questions, field):
    """{case: (kind, {question: mean over repeats})} for one instrument's run folder."""
    runs = {}
    for f in sorted(pathlib.Path(folder).glob("*.json")):
        d = json.loads(f.read_text())
        runs.setdefault(d["case"], {"kind": d["kind"], "values": {}})
        for q in questions:
            if q in d["answers"]:
                value = d["answers"][q][field]
                runs[d["case"]]["values"].setdefault(q, []).append(value)
    return {
        case: (data["kind"], {q: st.mean(v) for q, v in data["values"].items() if len(v) == N})
        for case, data in runs.items()
    }


def summarize(rows, questions, scale, higher_is_worse):
    out = {}
    for q in questions:
        clean = [v[q] for kind, v in rows.values() if kind == "clean" and q in v]
        bad = [v[q] for kind, v in rows.values() if kind == "bad" and q in v]
        if not clean or not bad:
            continue
        mu_c, sd_c, mu_b = st.mean(clean), st.pstdev(clean), st.mean(bad)
        if higher_is_worse:
            gap = mu_b - mu_c
            dead = mu_c > scale * 0.62 or mu_c < scale * 0.03
            unstable = sd_c > scale * 0.18
            keep = gap >= scale * 0.10 and not unstable
            weak = not keep and gap >= scale * 0.05 and not unstable
        else:
            gap = mu_b - mu_c
            dead = mu_c < 0.35
            unstable = sd_c > 0.18
            keep = gap <= -0.10 and not unstable
            weak = not keep and gap <= -0.05 and not unstable
        verdict = ("dead" if dead else "unstable" if unstable else
                   "keep" if keep else "weak" if weak else "no signal")
        out[q] = {
            "clean": {"n": len(clean), "mean": round(mu_c, 3), "sd": round(sd_c, 3)},
            "bad": {"n": len(bad), "mean": round(mu_b, 3)},
            "gap": round(gap, 3),
            "verdict": verdict,
        }
    return out


def main():
    noul_questions = [q.id for q in probe.bank().questions]
    score_questions = [q for q, _p, _i in sp.QUESTIONS]

    noul_rows = {}
    for f in sorted(pathlib.Path("runs/compare").glob("*.jev.r*.json")):
        d = json.loads(f.read_text())
        noul_rows.setdefault(d["case"], {"kind": d["kind"], "values": {}})
        for q, v in d["answers"].items():
            noul_rows[d["case"]]["values"].setdefault(q, []).append(v)
    noul = {c: (d["kind"], {q: st.mean(v) for q, v in d["values"].items() if len(v) == N})
            for c, d in noul_rows.items()}

    score_rows = {}
    for folder in ("runs/scores", "runs/holdout"):
        for f in sorted(pathlib.Path(folder).glob("*.json")):
            d = json.loads(f.read_text())
            score_rows.setdefault(d["case"], {"kind": d["kind"], "scores": {}, "conf": {}})
            for q, a in d["answers"].items():
                score_rows[d["case"]]["scores"].setdefault(q, []).append(a["score"])
                score_rows[d["case"]]["conf"].setdefault(q, []).append(a["confidence"])
    score = {c: (d["kind"], {q: st.mean(v) for q, v in d["scores"].items() if len(v) == N})
             for c, d in score_rows.items()}
    conf = {c: (d["kind"], {q: st.mean(v) for q, v in d["conf"].items() if len(v) == N})
            for c, d in score_rows.items()}

    cal = {
        "provisional": True,
        "note": ("Rules: noul keep needs gap <= -0.10 and sd <= 0.18; score keep needs gap >= 1.0 on the "
                 "0-4 scale and sd_clean <= 0.72. Dead means it never moves (a constant). Provisional "
                 "until the reference set has ~15 verified-clean changes per instrument."),
        "reference_set": {
            "noul": {"clean": sorted(c for c, (k, _) in noul.items() if k == "clean")},
            "score": {"clean": sorted(c for c, (k, _) in score.items() if k == "clean")},
        },
        "noul": summarize(noul, noul_questions, 1.0, higher_is_worse=False),
        "score": summarize(score, score_questions, 4.0, higher_is_worse=True),
        "confidence": {
            q: {
                "clean": {"mean": round(st.mean([v[q] for k, v in conf.values() if k == "clean" and q in v]), 3),
                          "sd": round(st.pstdev([v[q] for k, v in conf.values() if k == "clean" and q in v]), 3)},
                "bad": {"mean": round(st.mean([v[q] for k, v in conf.values() if k == "bad" and q in v]), 3)},
            }
            for q in score_questions
        },
    }
    OUT.write_text(json.dumps(cal, indent=1) + "\n")
    (pathlib.Path(__file__).with_name("CALIBRATION.md")).write_text(render(cal))
    print(render(cal))


def render(cal):
    lines = ["# Per-question calibration", "", "Provisional. Reference set:", ""]
    for instrument in ("noul", "score"):
        lines.append(f"- {instrument}: {len(cal['reference_set'][instrument]['clean'])} clean changes — "
                     + ", ".join(f"`{c}`" for c in cal["reference_set"][instrument]["clean"]))
    lines += ["", "## noul bank (0-1, higher = the change satisfies the question)", "",
              "| question | clean μ | clean σ | bad μ | gap | verdict |", "|---|---|---|---|---|---|"]
    for q, row in sorted(cal["noul"].items(), key=lambda kv: kv[1]["gap"]):
        lines.append(f"| `{q}` | {row['clean']['mean']:.2f} | {row['clean']['sd']:.2f} | "
                     f"{row['bad']['mean']:.2f} | {row['gap']:+.2f} | {row['verdict']} |")
    lines += ["", "## score bank (0-4 severity, higher = worse)", "",
              "| question | clean μ | clean σ | bad μ | gap | confidence clean / bad | verdict |",
              "|---|---|---|---|---|---|---|"]
    for q, row in sorted(cal["score"].items(), key=lambda kv: -kv[1]["gap"]):
        c = cal["confidence"][q]
        lines.append(f"| `{q}` | {row['clean']['mean']:.2f} | {row['clean']['sd']:.2f} | "
                     f"{row['bad']['mean']:.2f} | {row['gap']:+.2f} | "
                     f"{c['clean']['mean']:.2f} / {c['bad']['mean']:.2f} | {row['verdict']} |")
    kept = [q for q, r in cal["noul"].items() if r["verdict"] == "keep"]
    kept_s = [q for q, r in cal["score"].items() if r["verdict"] == "keep"]
    lines += ["", f"**noul keeps {len(kept)} of {len(cal['noul'])}**: " + ", ".join(f"`{q}`" for q in kept),
              "", f"**score keeps {len(kept_s)} of {len(cal['score'])}**: " + ", ".join(f"`{q}`" for q in kept_s), ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
