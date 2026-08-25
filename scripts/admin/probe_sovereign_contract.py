#!/usr/bin/env python3
"""R-F4320 / C-268 — live proof that the sovereign endpoint accepts what we send.

The unit tests in `test_rf4320_mistral_contract.py` prove we implement what we
BELIEVE Mistral's chat-template contract to be. Only this proves the belief.

It is a script rather than a test because R-F3433 blocks live DNS across the
suite with no escape hatch, and it is right to: a blocking getaddrinfo with no
application timeout makes a stalled run look hung rather than failed.

    python scripts/admin/probe_sovereign_contract.py

Exit 0 = the contract holds as implemented. Exit 1 = it has MOVED; read the
output before changing `_mistral_contract`, because a served-model swap can
change these rules without touching a line of our code.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from aria_cli import llm as cli_llm  # noqa: E402

URL = (os.getenv("ARIA_LLM_URL") or "").strip()
MODEL = os.getenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
# A descriptive UA is REQUIRED: the RunPod proxy answers 403 to urllib's default
# (§27b — the same finding that reframed the whole tier-2 search diagnosis).
HDRS = {"Content-Type": "application/json",
        "User-Agent": "AriaIntelligence/1.0 (aria@arkmurus.com)"}

TOOLS = [{"type": "function", "function": {
    "name": "read_file", "description": "read a file",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"}},
                   "required": ["path"]}}}]


def post(messages, tools=None, max_tokens=32):
    body = {"model": MODEL, "messages": messages,
            "max_tokens": max_tokens, "temperature": 0}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(URL.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), headers=HDRS)
    with urllib.request.urlopen(req, timeout=120) as f:
        return json.load(f)


def main() -> int:
    if not URL:
        print("ARIA_LLM_URL is not set — nothing to probe.")
        return 2

    raw_loop = [
        {"role": "system", "content": "You are ARIA."},
        {"role": "user", "content": "Read a.txt"},
        {"role": "assistant", "tool_calls": [
            {"id": "call_abc12345", "type": "function",
             "function": {"name": "read_file",
                          "arguments": "{\"path\": \"a.txt\"}"}}]},
        {"role": "tool", "tool_call_id": "call_abc12345", "content": "hello"},
    ]
    bad_alt = [{"role": "system", "content": "S"},
               {"role": "user", "content": "a"},
               {"role": "user", "content": "b"}]

    failures = []

    # 1. the repaired tool loop must be ACCEPTED
    try:
        out = post(cli_llm._wire_messages(raw_loop, "aria-llm"), TOOLS)
        msg = out["choices"][0]["message"]
        print("PASS  repaired tool loop accepted -> %r"
              % ((msg.get("content") or "")[:60]))
    except urllib.error.HTTPError as e:
        failures.append("repaired tool loop REJECTED: %s" % e.read().decode()[:200])
        print("FAIL  " + failures[-1])

    # 2. the repair must be LOAD-BEARING — a fix whose absence changes nothing
    #    is not a fix, so prove the unrepaired forms really do fail.
    for name, msgs in (("unrepaired alternation", bad_alt),
                       ("unrepaired tool ids", raw_loop)):
        try:
            post(msgs, TOOLS if "tool" in name else None)
            failures.append("%s was ACCEPTED — the repair may be unnecessary, "
                            "or the served model changed" % name)
            print("FAIL  " + failures[-1])
        except urllib.error.HTTPError as e:
            if e.code == 400:
                print("PASS  %s correctly rejected (400)" % name)
            else:
                failures.append("%s failed with %s, expected 400" % (name, e.code))
                print("FAIL  " + failures[-1])

    # 3. report the served window — the other half of C-267
    try:
        req = urllib.request.Request(URL.rstrip("/") + "/models", headers=HDRS)
        with urllib.request.urlopen(req, timeout=60) as f:
            d = json.load(f)
        print("INFO  served window = %s" % d["data"][0].get("max_model_len"))
    except Exception as e:  # noqa: BLE001
        print("INFO  could not read served window: %s" % str(e)[:120])

    print("\n%s" % ("CONTRACT HOLDS" if not failures else "CONTRACT MOVED"))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
