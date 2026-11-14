import json
import pathlib
import time
import urllib.error
import urllib.request

import probe

key = json.loads((pathlib.Path.home() / ".codex/auth.json").read_text()).get("OPENAI_API_KEY") or ""
print("key present:", bool(key), "len:", len(key))
change = probe.diff("0bde508^", "0bde508")
state = {"base": "0bde508", "head": "0bde508", "diff": change}
questions = [
    {
        "type": "predicate",
        "name": "contract.error-handling",
        "instructions": "Does every new failure at a boundary surface an error a caller can act on\n\nTrue means: a new failure at a boundary raises an error the caller can act on\nFalse means: a failure is swallowed, silently defaulted, or crashes with no actionable error",
    }
]
for model in ("gpt-6-luna", "gpt-6.1-luna"):
    body = {"model": model, "input": json.dumps(state), "questions": questions}
    req = urllib.request.Request(
        "https://api.openai.com/v1/decisions",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode())
            status = r.status
    except urllib.error.HTTPError as e:
        data = e.read().decode()[:300]
        status = e.code
    except Exception as e:
        data = str(e)[:200]
        status = None
    print(model, "->", status, round(time.monotonic() - t0, 2), "s")
    print("   ", json.dumps(data)[:400])
