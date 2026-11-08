#!/usr/bin/env python3
"""The profile launcher: `agent-runtime-profile <profile> <config> -- <program> [args...]`.

The runtime lives in the `agent-runtime` member (`packages/agent-runtime/src/agent_runtime`).
This file keeps the documented path working: the installer's `agent-runtime-profile` shim execs it.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))

from agent_runtime.profiles import ProfileError, launch  # noqa: E402

if __name__ == "__main__":
    try:
        launch(sys.argv[1:], os.environ)
    except ProfileError as error:
        print("agent-runtime-profile: %s" % error, file=sys.stderr)
        sys.exit(1)
