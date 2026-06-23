"""R-F1808 — IAST-lite runner: install sink sensors, run the security-relevant
slice of the QA suite under instrumentation, dump the runtime sink-hit log.

Usage: python scripts/iast/run_iast.py [extra pytest args]
Output: scripts/iast/_iast_sinks.jsonl  (one record per unique app-code sink call site)
"""
import glob
import os
import sys

sys.path.insert(0, os.getcwd())

# Install BEFORE pytest imports app code, so every sink hit during collection+run
# is captured.
from scripts.iast import sensor  # noqa: E402
sensor.install()

import pytest  # noqa: E402

# Security-relevant test selection — paths where user input can reach a sink.
PATTERNS = (
    "routes", "compliance", "sanctions", "polyglot", "upload", "doc", "read_document",
    "security", "auth", "injection", "dd_", "dd", "search", "url", "extract",
    "document", "command", "screen", "tool",
)
all_tests = sorted(glob.glob("aria_service/tests/test_*.py"))
sel = [t for t in all_tests if any(p in os.path.basename(t).lower() for p in PATTERNS)]
print(f"[IAST] {len(all_tests)} total test files; instrumenting {len(sel)} security-relevant ones", flush=True)

extra = sys.argv[1:]
# Per-test timeout bounds the known hangs (pytest.ini configures pytest-timeout).
code = pytest.main(["-q", "-p", "no:cacheprovider", "--timeout=25"] + extra + sel)

out = "scripts/iast/_iast_sinks.jsonl"
n = sensor.dump(out)
print(f"\n[IAST] pytest exit={code}; recorded {n} unique app-code sink call-sites -> {out}", flush=True)
# Print a quick by-category tally for the console.
from collections import Counter  # noqa: E402
cats = Counter(r["category"] for r in sensor.records())
for c, k in cats.most_common():
    print(f"[IAST]   {c}: {k}", flush=True)
