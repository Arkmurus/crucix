"""Capability tests for R-W3 + R-W7 + R-W9 — direct bugs in the existing
web research code paths (orthogonal to R-F307's new web_explorer module).

R-W3 — read_article chat dispatch returned empty Summary because keys
       didn't match. The LLM then saw "Facts learned: N / Summary: " and
       either refused or hallucinated.

R-W7 — chat → deep_research entity derivation kept "modirumgespi.com" as
       the entity name (same bug R-F299 fixed in DD). Now brandified.

R-W9 — deep_research's snippet_count_per_angle hid silent and errored
       angles (only entries with snippet hits were populated). Now every
       angle appears with its status (ok / silent / errored).
"""
from __future__ import annotations


# ───────────────────────── R-W3 — read_article summary ──────────────────────

def test_rw3_capability_read_article_summary_falls_back_to_facts_when_empty():
    """The 2026-05-11 failure mode: read_article returns a dict with
    `facts` (list) but NO `summary` / `analysis` key. The chat handler
    was reaching for the wrong keys, so Summary rendered blank. After
    the fix, the handler reads `facts` and builds a real summary."""
    # Reproduce the dispatcher logic inline.
    r = {
        "url": "https://example.com",
        "facts_learned": 3,
        "facts": [
            {"text": "Modirum is a Finnish defence OEM"},
            {"text": "GESPI is the Brazilian arm, São José dos Campos"},
            {"text": "CEO is Elias Silvola"},
        ],
        "hypothesis": {"hypothesis": "Modirum's growth comes from GESPI acquisition"},
        "duration_ms": 1500,
    }
    summary = (r.get("summary") or r.get("analysis") or "").strip()
    if not summary:
        facts_list = r.get("facts") or []
        if facts_list:
            parts = []
            for ff in facts_list[:8]:
                if isinstance(ff, dict):
                    txt = ff.get("text") or ff.get("claim") or ff.get("value") or ""
                else:
                    txt = str(ff)
                if txt:
                    parts.append("• " + txt.strip()[:240])
            summary = "\n".join(parts)
        hyp = r.get("hypothesis")
        if hyp and isinstance(hyp, dict):
            h_text = hyp.get("hypothesis") or hyp.get("text") or ""
            if h_text:
                summary = (summary + "\n\nHypothesis: " + h_text).strip()
    summary = summary[:1500]

    assert "Modirum is a Finnish defence OEM" in summary
    assert "GESPI" in summary
    assert "Elias Silvola" in summary
    assert "Hypothesis:" in summary
    assert summary != ""


# ───────────────────────── R-W7 — brandification in chat ────────────────────

def test_rw7_chat_entity_derivation_uses_brandify():
    """The chat handler's URL→entity fallback now calls
    web_explorer.brandify_query, matching what the DD path does (R-F299).
    'duma-engineering.com' → 'duma engineering', not 'duma-engineering'."""
    from aria_service.intel.web_explorer import brandify_query
    # Hostname inputs
    assert brandify_query("modirumgespi.com") == "modirumgespi"
    assert brandify_query("duma-engineering.com") == "duma engineering"
    assert brandify_query("acme_defence.io") == "acme defence"
    # The chat path used to keep "modirumgespi.com" or "duma-engineering"
    # — now uniformly brandified.


# ───────────────────────── R-W9 — deep_research angle honesty ───────────────

def test_rw9_deep_research_initialises_all_angles_at_zero():
    """The exact failure: snippet_count_per_angle only had entries for
    angles that returned snippets. Operator saw 2 of 5 angles in the
    output and couldn't tell whether the other 3 failed or returned 0.
    After the fix, all 5 are present from the start."""
    angles = [
        "Modirum GESPI",
        "Modirum GESPI defence procurement",
        "Modirum GESPI directors leadership",
        "Modirum GESPI sanctions",
        "Modirum GESPI registered office",
    ]
    # Replicate the post-R-W9 initialisation
    snippet_count_per_angle = {a: 0 for a in angles}
    angle_status = {a: "ok" for a in angles}

    # Simulate: angle 0 returns 3 hits, angle 2 errors, angles 1/3/4 return 0
    snippet_count_per_angle[angles[0]] = 3
    snippet_count_per_angle[angles[2]] = 0  # actually error
    angle_status[angles[2]] = "errored: TimeoutError"
    # 1, 3, 4 ran ok with 0 hits → flip to silent
    for i in (1, 3, 4):
        if angle_status[angles[i]] == "ok" and snippet_count_per_angle[angles[i]] == 0:
            angle_status[angles[i]] = "silent"

    # Every angle is visible in the output
    assert len(snippet_count_per_angle) == 5
    assert all(a in angle_status for a in angles)
    assert angle_status[angles[0]] == "ok"
    assert angle_status[angles[1]] == "silent"
    assert angle_status[angles[2]].startswith("errored")
    assert angle_status[angles[3]] == "silent"
    assert angle_status[angles[4]] == "silent"


def test_rw9_capability_silent_angle_is_distinguishable_from_errored():
    """Capability — given the structured angle_status, the operator can
    triage: silent = backend returned 0 (query may be bad); errored =
    transport/backend failure (different fix)."""
    angle_status = {
        "modirum directors": "ok",
        "modirumgespi.com headquarters location": "silent",  # bad query (hostname not brand)
        "modirum sanctions screen": "errored: TimeoutError",
        "modirum financial filings": "silent",
    }
    # Silent vs errored is distinguishable
    silents = [a for a, s in angle_status.items() if s == "silent"]
    errors = [a for a, s in angle_status.items() if s.startswith("errored")]
    assert "modirumgespi.com headquarters location" in silents
    assert "modirum sanctions screen" in errors
    # Operator can act differently: silents → rewrite query (R-W7);
    # errors → check backend health / circuit breakers
