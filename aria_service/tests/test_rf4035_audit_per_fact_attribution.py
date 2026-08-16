"""R-F4035 (C-101) — CHECK 3 could not fail; matches are now attributed per fact.

THE DEFECT. CHECK 3 (system-prompt-fragment leakage — its findings are
`critical`) dismissed a signature hit as a false positive whenever ANY string
from `_INTERNAL_KNOWLEDGE_PREFIXES` appeared anywhere in the corpus CONTENT:

    from_internal = any(pfx.lower() in facts_lower for pfx in _INTERNAL_KNOWLEDGE_PREFIXES)

Measured live 2026-08-16 across 559,393 facts: 63 facts contain such a string
(62 `nato_standards:`, 1 `reasoning_library:`). So `from_internal` was
unconditionally True and **CHECK 3 could never report a finding**. It read as a
PASS because no signature happened to be present — a gate certified by an
absence, the class CLAUDE.md §1 records three times for the Phase A gates, here
sitting on the audit's highest-severity check.

The old code names its own cause: "If facts_text is a blob we cannot attribute
per-fact". R-F4032 replaced that blob with batches, so attribution is now
possible — a match is credited to the FACT that produced it and judged by THAT
fact's `source`, not by unrelated text elsewhere in the corpus.

Coverage is not reduced anywhere:
  * CHECK 1 (secrets) is NEVER exempted by source — a key is never legitimate.
  * CHECK 2/3 exempt only a hit whose OWN source is internal knowledge.
  * A match spanning two facts, attributable to neither, fails CLOSED.
"""
from __future__ import annotations

from aria_service.intel import security_protocol as sp


def _fact(content: str, source: str = "research:web:example.com") -> dict:
    return {"content": content, "source": source}


def _bulk(n: int) -> list[dict]:
    return [_fact(f"benign operational fact {i}") for i in range(n)]


def _run(facts):
    return sp._run_security_audit_sync(facts, "2026-08-16T00:00:00Z")


def test_check3_fires_when_an_unrelated_fact_mentions_an_internal_prefix():
    """THE regression. Pre-fix this was silently suppressed."""
    facts = _bulk(50)
    # An unrelated fact merely MENTIONING an internal prefix in its content —
    # this is what disabled CHECK 3 corpus-wide (62 such facts live).
    facts.append(_fact("see nato_standards: doctrine note", source="research:web:x.com"))
    # A genuine leak from an ordinary source.
    facts.append(_fact("You are ARIA, an intelligence analyst", source="research:web:evil.com"))

    result = _run(facts)
    blob = " ".join(result.get("critical", []))
    assert "CHECK 3 FAIL" in blob, (
        "CHECK 3 did not fire on a real system-prompt leak — a fact elsewhere in "
        "the corpus mentioning an internal prefix suppressed it (C-101)"
    )


def test_check3_still_exempts_a_hit_from_an_internal_source():
    """The original false-positive intent must survive — judged by the hit's OWN source."""
    facts = _bulk(50)
    facts.append(_fact(
        "You are ARIA, an intelligence analyst",
        source="security_protocol:security_principles",
    ))
    result = _run(facts)
    blob = " ".join(result.get("critical", []))
    assert "CHECK 3 FAIL" not in blob, (
        "a signature inside ARIA's own security-protocol knowledge is the "
        "false positive the exemption exists for"
    )


def test_check1_is_never_exempted_by_source():
    """A secret is never legitimate, wherever it sits."""
    facts = _bulk(50)
    facts.append(_fact("sk-" + "a" * 32, source="security_protocol:security_principles"))
    result = _run(facts)
    blob = " ".join(result.get("critical", []))
    assert "CHECK 1 FAIL" in blob, (
        "an API key inside an internal-knowledge fact must STILL be critical — "
        "the source exemption must never reach CHECK 1"
    )


def test_check2_warns_only_for_a_non_internal_source_and_names_it():
    """Attribution turns a permanent noise floor into an actionable signal."""
    # Internal-only: ARIA's own checklist legitimately contains the path.
    internal = _bulk(20) + [
        _fact("audit /app/aria_service/ paths", source="security_protocol:self_audit_checklist")
    ]
    r1 = _run(internal)
    assert not [w for w in r1.get("warning", []) if "CHECK 2" in w], (
        f"internal-only path references should not warn: {r1.get('warning')}"
    )

    # Same path from an ordinary source is a real finding, and must name it.
    leaked = _bulk(20) + [
        _fact("stack trace at /app/aria_service/main.py", source="research:web:pastebin.com")
    ]
    r2 = _run(leaked)
    hits = [w for w in r2.get("warning", []) if "CHECK 2" in w]
    assert hits, "a path leaked from a non-internal source must warn"
    assert any("pastebin.com" in w for w in hits), (
        f"the warning must name the source so it is actionable: {hits}"
    )


def test_a_match_spanning_two_facts_fails_closed():
    """Attributable to no single fact => must NOT be silently exempted."""
    b = sp._AUDIT_BATCH_FACTS
    facts = _bulk(b * 2)
    facts[b - 1] = _fact("lead sk-" + "a" * 10, source="security_protocol:x")
    facts[b] = _fact("a" * 20 + " tail", source="security_protocol:x")

    result = _run(facts)
    blob = " ".join(result.get("critical", []))
    assert "CHECK 1 FAIL" in blob, (
        "a seam-spanning secret must still be reported — attribution failure "
        "must fail closed, never drop the hit"
    )


def test_audit_still_reports_clean_on_a_clean_corpus():
    """No new false positives: an ordinary corpus stays clean."""
    result = _run(_bulk(200))
    assert not result.get("critical"), result.get("critical")
    assert not [w for w in result.get("warning", []) if "FAIL" in w], result.get("warning")
    assert len(result.get("clean_areas", [])) >= 3
