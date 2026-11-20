"""Recompute the method-cost benchmarks from the frozen data and check them against the stored records.

`data/` holds the raw evidence, anonymized: assistant usage lines of the measured sessions (timestamp,
model, message id, tokens; subagent lines carry an opaque label), the orchestrated run's attempt totals
and reviewer snapshots, and the Rust repo runs' worker/reviewer totals. Each record holds the windows, the
ticket stats and the results. Together they reproduce on any machine:

    python3 evals/method-cost/extract.py              # recompute from data/ and check every record
    python3 evals/method-cost/extract.py --print      # show the recomputed tables
    python3 evals/method-cost/extract.py --freeze     # rebuild data/ from the live logs (measuring machine)

`--freeze` needs the private sources file (`data.local_sources` in the records): repo keys, session ids
and the label mappings that never enter this repository. The MCP record cuts every usage line at
`manual.as_of`, because that Lead session was still live when measured.

Costs use each record's rates (USD per M input tokens, solved from Claude Code's own cost-state
snapshots): cost = (in + 5*out + 0.1*cache_read + 1.25*cache_write) * rate / 1e6. Pi sessions carry their
recorded cost (token-plan models bill 0 marginal). Lines are deduped by message id. Stdlib only.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEYS = ("in", "out", "cr", "cw")
MODELS = {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "haiku": "claude-haiku-5-5"}


def blank():
    return {k: 0 for k in KEYS}


def add(dst, usage):
    dst["in"] += usage.get("input_tokens", 0)
    dst["out"] += usage.get("output_tokens", 0)
    dst["cr"] += usage.get("cache_read_input_tokens", 0)
    dst["cw"] += usage.get("cache_creation_input_tokens", 0)


def cost(tokens, rates):
    return sum(
        (t["in"] + 5 * t["out"] + 0.1 * t["cr"] + 1.25 * t["cw"]) * rates.get(model, 1.73) / 1e6
        for model, t in tokens.items()
    )


def totals(lines, lo=None, hi=None):
    """Per-model token totals of (at, model, id, usage) lines, deduped by id, windowed by [lo, hi)."""
    seen = set()
    tot = defaultdict(blank)
    for at, model, mid, usage in lines:
        if (lo is not None or hi is not None) and not ((lo or "") <= at < (hi or "9999")):
            continue
        if mid in seen:
            continue
        seen.add(mid)
        add(tot[model], usage)
    return dict(tot)


def minutes(a, b):
    def parse(s):
        return datetime.strptime(s[:19] + "Z", "%Y-%m-%dT%H:%M:%SZ")

    return round((parse(b) - parse(a)).total_seconds() / 60)


def load_data(rec):
    names = rec["data"]["frozen"]
    lead = [
        tuple(json.loads(line)[k] for k in ("at", "model", "id", "usage"))
        for line in open(HERE / names[0])
    ]
    subs = defaultdict(list)
    for line in open(HERE / names[1]):
        r = json.loads(line)
        subs[r["label"]].append((r["at"], r["model"], r["id"], r["usage"]))
    orch = json.load(open(HERE / names[2]))
    return lead, dict(subs), orch


def usage_lines(path):
    """(timestamp, model, message id, usage) of every assistant message in a Claude transcript."""
    for line in open(path):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("type") != "assistant":
            continue
        msg = row.get("message", {})
        if msg.get("usage"):
            yield row.get("timestamp", ""), msg.get("model", "?"), msg.get("id"), msg["usage"]


def pi_usage_lines(path):
    """(timestamp, model, message id, normalized usage, recorded cost) of a pi session transcript."""
    for line in open(path):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("type") != "message":
            continue
        msg = row.get("message", {})
        u = msg.get("usage")
        if msg.get("role") != "assistant" or not u:
            continue
        c = u.get("cost") or {}
        cost_usd = c.get("total") or (
            c.get("input", 0) + c.get("output", 0) + c.get("cacheRead", 0) + c.get("cacheWrite", 0)
        )
        usage = {
            "input_tokens": u.get("input", 0),
            "output_tokens": u.get("output", 0),
            "cache_read_input_tokens": u.get("cacheRead", 0),
            "cache_creation_input_tokens": u.get("cacheWrite", 0),
        }
        yield row.get("timestamp", ""), msg.get("model", "?"), row.get("id"), usage, cost_usd


def pi_totals(lines, lo=None, hi=None):
    """(per-model token totals, recorded cost) of pi usage lines, deduped by id, windowed."""
    seen = set()
    tot = defaultdict(blank)
    cost_usd = 0.0
    for at, model, mid, usage, c in lines:
        if (lo is not None or hi is not None) and not ((lo or "") <= at < (hi or "9999")):
            continue
        if mid in seen:
            continue
        seen.add(mid)
        add(tot[model], usage)
        cost_usd += c
    return dict(tot), cost_usd


def freeze(rec):
    local = json.load(open(Path(rec["data"]["local_sources"]).expanduser()))
    src, labels = local["sources"], local["labels"]
    as_of = rec["manual"]["as_of"]
    data = HERE / "data"
    data.mkdir(exist_ok=True)
    lead_path = (
        Path(src["claude_projects"]).expanduser()
        / src["lead_project"]
        / (src["lead_session"] + ".jsonl")
    )
    with open(data / "lead-usage.jsonl", "w") as out:
        for at, model, mid, usage in usage_lines(lead_path):
            if at < as_of:
                out.write(json.dumps({"at": at, "model": model, "id": mid, "usage": usage}) + "\n")
    base = lead_path.parent / src["lead_session"] / "subagents"
    n = 0
    with open(data / "subagents-usage.jsonl", "w") as out:
        for meta_path in sorted(base.glob("*.meta.json")):
            meta = json.load(open(meta_path))
            label = labels.get(meta.get("description"))
            if label is None:
                continue
            jf = meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))
            for at, model, mid, usage in usage_lines(jf):
                if at < as_of:
                    out.write(
                        json.dumps(
                            {"label": label, "at": at, "model": model, "id": mid, "usage": usage}
                        )
                        + "\n"
                    )
                    n += 1
    run = Path(src["state_dir"]).expanduser() / src["repo_key"] / local["plan"]
    attempts = []
    for line in open(run / "attempts.jsonl"):
        r = json.loads(line)
        t = r["tokens"]
        attempts.append(
            {
                "task": r["task_id"],
                "provider": r["provider"],
                "elapsed_s": r["elapsed_seconds"],
                "in": t["input"],
                "out": t["output"],
                "cr": t["cached"],
                "cw": t["cache_write"],
            }
        )
    agents, unit_of, last = {}, {}, {}
    for line in open(run / "events.jsonl"):
        r = json.loads(line)
        if r["kind"] == "agent":
            agents[r.get("assignment_id")] = r.get("role")
            if r.get("role") == "reviewer":
                unit_of[r["assignment_id"]] = (
                    r.get("name", "").split("-review")[0].replace(local["plan"] + "-", "")
                )
        elif r["kind"] == "usage":
            last[r["assignment_id"]] = r["usage"]
    snaps = [
        {
            "unit": unit_of.get(a),
            "model": "claude-sonnet-5-5",
            "in": u["input_tokens"],
            "out": u["output_tokens"],
            "cr": u["cached_input_tokens"],
            "cw": u["cache_write_tokens"],
        }
        for a, u in last.items()
        if agents.get(a) == "reviewer"
    ]
    json.dump(
        {
            "run_start": rec["orchestrated"]["run_start"],
            "run_finish": rec["orchestrated"]["run_finish"],
            "worker_attempts": attempts,
            "reviewer_snapshots": snaps,
        },
        open(data / "orchestrated-inputs.json", "w"),
        indent=1,
    )
    print(f"frozen at {as_of}: lead + {n} subagent usage lines + orchestrated inputs into {data}")


def run_totals(run):
    """(workers, reviewers) token totals of one orchestrated run: attempts + last reviewer snapshots."""
    w = blank()
    for line in open(run / "attempts.jsonl"):
        t = json.loads(line).get("tokens") or {}
        w["in"] += t.get("input") or 0
        w["out"] += t.get("output") or 0
        w["cr"] += t.get("cached") or 0
        w["cw"] += t.get("cache_write") or 0
    agents, last = {}, {}
    for line in open(run / "events.jsonl"):
        row = json.loads(line)
        if row["kind"] == "agent":
            agents[row.get("assignment_id")] = row.get("role")
        elif row["kind"] == "usage":
            last[row["assignment_id"]] = row["usage"]
    rv = blank()
    for a, u in last.items():
        if agents.get(a) == "reviewer":
            rv["in"] += u.get("input_tokens") or 0
            rv["out"] += u.get("output_tokens") or 0
            rv["cr"] += u.get("cached_input_tokens") or 0
            rv["cw"] += u.get("cache_write_tokens") or 0
    return w, rv


def freeze_runs(rec, local):
    r = local["runs"]
    data = HERE / "data"
    cbase = Path(local["sources"]["claude_projects"]).expanduser() / r["claude_project"]
    with open(data / "runs-claude-usage.jsonl", "w") as out:
        for sid, label in r["claude_sessions"].items():
            for f in cbase.glob(sid + "*.jsonl"):
                for at, model, mid, usage in usage_lines(f):
                    out.write(
                        json.dumps(
                            {"session": label, "at": at, "model": model, "id": mid, "usage": usage}
                        )
                        + "\n"
                    )
    pbase = Path(r["pi_dir"]).expanduser()
    with open(data / "runs-pi-usage.jsonl", "w") as out:
        for prefix, label in r["pi_sessions"].items():
            for f in pbase.glob(prefix + "*.jsonl"):
                for at, model, mid, usage, c in pi_usage_lines(f):
                    out.write(
                        json.dumps(
                            {
                                "session": label,
                                "at": at,
                                "model": model,
                                "id": mid,
                                "usage": usage,
                                "cost_usd": c,
                            }
                        )
                        + "\n"
                    )
    runs = {}
    for slug, label in r["plans"].items():
        run = Path(local["sources"]["state_dir"]).expanduser() / r["state_repo_key"] / slug
        w, rv = run_totals(run)
        runs[label] = {"workers": w, "reviewers": rv}
    json.dump(runs, open(data / "runs-totals.json", "w"), indent=1)
    print(f"frozen runs: claude + pi usage lines + {len(runs)} run totals into {data}")


def orchestrated(rec, rates, lead, orch):
    workers = defaultdict(blank)
    for a in orch["worker_attempts"]:
        w = workers[MODELS.get(a["provider"], a["provider"])]
        for k in KEYS:
            w[k] += a[k]
    reviewers = blank()
    for s in orch["reviewer_snapshots"]:
        for k in KEYS:
            reviewers[k] += s[k]
    lw = rec["orchestrated"]["lead_window"]
    worker_cost = cost(workers, rates)
    review_cost = cost({"claude-sonnet-5-5": reviewers}, rates)
    lead_cost = cost(totals(lead, lw["lo"], lw["hi"]), rates)
    total = worker_cost + review_cost + lead_cost
    tickets = rec["orchestrated"]["tickets"]
    adds = sum(t["additions"] for t in tickets)
    return {
        "tickets": len(tickets),
        "merged": sum(1 for t in tickets if t["merged"]),
        "cost_usd": round(total, 2),
        "workers_usd": round(worker_cost, 2),
        "reviewers_usd": round(review_cost, 2),
        "lead_usd": round(lead_cost, 2),
        "wall_min": minutes(orch["run_start"], orch["run_finish"]),
        "additions": adds,
        "deletions": sum(t["deletions"] for t in tickets),
        "cost_per_ticket": round(total / len(tickets), 2),
        "cost_per_added_line": round(total / adds, 4),
    }


def manual(rec, rates, lead, subs):
    as_of = rec["manual"].get("as_of")

    def cap(hi):
        return min(hi, as_of) if as_of else hi

    total = review_total = 0.0
    active = 0
    entries = [(t["impl"], t["review"], t["lead_window"]) for t in rec["manual"]["tickets"]]
    rw = rec["manual"]["rework"]
    entries.append((rw["impl"], rw["review"], rw["lead_window"]))
    for impl, review, window in entries:
        ic = cost(totals(subs.get(impl["label"], []), hi=as_of), rates)
        rc = cost(totals(subs.get(review["label"], []), hi=as_of), rates)
        lc = cost(totals(lead, window["lo"], cap(window["hi"])), rates)
        total += ic + rc + lc
        review_total += rc
        active += minutes(impl["spawn"] + ":00Z", max(impl["end"], review["end"]) + ":00Z")
    tickets = rec["manual"]["tickets"]
    adds = sum(t["additions"] for t in tickets)
    rework = (
        cost(totals(subs.get(rw["impl"]["label"], []), hi=as_of), rates)
        + cost(totals(subs.get(rw["review"]["label"], []), hi=as_of), rates)
        + cost(totals(lead, rw["lead_window"]["lo"], cap(rw["lead_window"]["hi"])), rates)
    )
    return {
        "tickets": len(tickets),
        "merged": sum(1 for t in tickets if t["merged"]),
        "cost_usd": round(total, 2),
        "wall_elapsed_min": minutes(
            tickets[0]["impl"]["spawn"] + ":00Z", rw["impl"]["end"] + ":00Z"
        ),
        "wall_active_min": active,
        "additions": adds,
        "deletions": sum(t["deletions"] for t in tickets),
        "cost_per_ticket": round(total / len(tickets), 2),
        "cost_per_added_line": round(total / adds, 4),
        "review_total_usd": round(review_total, 2),
        "rework_usd": round(rework, 2),
    }


def runs_results(rec, rates):
    claude = defaultdict(list)
    for line in open(HERE / rec["data"]["frozen"][0]):
        r = json.loads(line)
        claude[r["session"]].append((r["at"], r["model"], r["id"], r["usage"]))
    pi = defaultdict(list)
    for line in open(HERE / rec["data"]["frozen"][1]):
        r = json.loads(line)
        pi[r["session"]].append((r["at"], r["model"], r["id"], r["usage"], r["cost_usd"]))
    runs = json.load(open(HERE / rec["data"]["frozen"][2]))
    sessions = []
    for s in rec["sessions"]:
        w = s.get("window") or {}
        if s["harness"] == "claude":
            c = cost(totals(claude.get(s["label"], []), w.get("lo"), w.get("hi")), rates)
        else:
            _, c = pi_totals(pi.get(s["label"], []), w.get("lo"), w.get("hi"))
        sessions.append({"label": s["label"], "role": s["role"], "cost_usd": round(c, 2)})
    by = {(s["label"], s["role"]): s["cost_usd"] for s in sessions}
    run_cost = {
        p: round(
            cost({"claude-sonnet-5-5": r["workers"]}, rates)
            + cost({"claude-sonnet-5-5": r["reviewers"]}, rates),
            2,
        )
        for p, r in runs.items()
    }
    lead3_tokens, _ = pi_totals(pi.get("Pi-L3", []))
    lead3_opus = round(
        sum(t["in"] + 5 * t["out"] + 0.1 * t["cr"] + 1.25 * t["cw"] for t in lead3_tokens.values())
        * rates["claude-opus-5-5"]
        / 1e6,
        2,
    )
    p1_plan = by[("C-P1", "planning P1")]
    p2_plan = by[("C-G2", "gather P2")]
    return {
        "sessions": sessions,
        "plans": {
            "P1": {
                "planning_usd": p1_plan,
                "preplan_usd": round(
                    by[("Pi-P1a", "pre-plan P1")] + by[("Pi-P1b", "pre-plan P1 (second attempt)")],
                    2,
                ),
                "execution_usd": run_cost["P1"],
                "lead_usd": by[("C-P1", "lead P1")],
            },
            "P2": {
                "planning_usd": p2_plan,
                "execution_usd": run_cost["P2"],
                "lead_usd": by[("C-P2", "lead P2 + amend")],
            },
            "P3": {
                "execution_usd": run_cost["P3"],
                "lead_usd": by[("Pi-L3", "lead P3")],
                "lead_at_opus_rates_usd": lead3_opus,
            },
            "P4": {
                "planning_opus_usd": by[("C-P1", "planning P4")],
                "planning_pi_usd": by[("Pi-P4", "planning P4 (metered glm)")],
            },
        },
        "old_loop": {
            "lead_opus_usd": by[("C-P0", "lead old loop")],
            "lead_pi_usd": by[("Pi-L0", "lead old loop (deepseek-flash)")],
        },
        "sonnet_planning_attempt_usd": by[
            ("C-try", "sonnet planning attempt (no plan file produced)")
        ],
        "planning_share_of_execution": {
            "P1": round(p1_plan / run_cost["P1"], 3),
            "P2": round(p2_plan / run_cost["P2"], 3),
        },
    }


def flatten(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from flatten(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from flatten(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", help="check one record; default: every record in this directory")
    ap.add_argument("--freeze", action="store_true", help="rebuild data/ from the live logs")
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args()
    paths = [Path(args.record)] if args.record else sorted(HERE.glob("*.json"))
    local = None
    failed = False
    for path in paths:
        rec = json.load(open(path))
        if args.freeze:
            local = local or json.load(open(Path(rec["data"]["local_sources"]).expanduser()))
            if rec["id"].endswith("mcp-writes"):
                freeze(rec)
            else:
                freeze_runs(rec, local)
            continue
        rates = {k: v for k, v in rec["rates"].items() if k.startswith("claude-")}
        if rec["id"].endswith("mcp-writes"):
            lead, subs, orch = load_data(rec)
            got = {
                "orchestrated": orchestrated(rec, rates, lead, orch),
                "manual": manual(rec, rates, lead, subs),
            }
        else:
            got = runs_results(rec, rates)
        if args.show:
            print(json.dumps(got, indent=1))
        bad = []
        for key, value in flatten(got):
            want = dict(flatten(rec["results"])).get(key)
            if want is None or not isinstance(value, (int, float)):
                continue
            tol = 0.06 if ("usd" in key or "cost" in key) else 0.01 if "share" in key else 2
            if abs(value - want) > tol:
                bad.append(f"{key}: recomputed {value}, record {want}")
        if bad:
            print(f"{rec['id']} MISMATCH:\n" + "\n".join(bad))
            failed = True
        else:
            print(f"record {rec['id']}: every stored figure reproduced from the frozen data")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
