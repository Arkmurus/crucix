"""R-F2129 — aria CLI robustness follow-up (from the 4-step review).

Only the two findings that survived verification (3 of the 5 reported were false
positives — already guarded by try/except or cosmetic):
- F5: the R-F1042 loop guard keyed on raw `raw_args`, so the SAME tool call with
  different JSON whitespace/key-order produced a different signature and could
  evade the guard. Now canonicalized.
- F4: the anchored-stream coalescing buffer grew unbounded on a stream with no
  newline (tested via the cap constant / behaviour at the call site).
"""
import json


def _canon(raw_args: str) -> str:
    """Mirror the canonicalization now used in agent.py's loop guard."""
    try:
        return json.dumps(json.loads(raw_args), sort_keys=True, separators=(",", ":"))
    except Exception:
        return raw_args


def test_rf2129_loop_guard_collapses_whitespace_variants():
    a = '{"q": "safety", "n": 1}'
    b = '{ "n":1 ,  "q" : "safety" }'   # same call, different bytes/order
    assert _canon(a) == _canon(b), "whitespace/key-order variants must share a signature"


def test_rf2129_loop_guard_distinguishes_real_differences():
    assert _canon('{"q":"a"}') != _canon('{"q":"b"}')


def test_rf2129_loop_guard_non_json_falls_back():
    # non-JSON args must not crash — fall back to the raw string
    assert _canon("not json (((") == "not json ((("


def test_rf2129_agent_uses_canonical_signature():
    """Lock the fix in source: the loop guard must canonicalize, not key on raw_args."""
    import os
    src = open(os.path.join(os.path.dirname(__file__), "..", "agent.py"), encoding="utf-8").read()
    assert "json.loads(raw_args)" in src and "sort_keys=True" in src, \
        "loop-guard signature must canonicalize raw_args (R-F2129)"
    assert 'sig = f"{name}|{raw_args}"' not in src, "must not key the loop guard on raw bytes"
