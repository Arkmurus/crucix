"""R-F763 — extend R-F741 to exempt 'no <action-verb/noun> ... <tool>'.

LIVE BUG captured by other-agent transcript review 2026-05-20:

    [R-F401 SELF-CLAIM GUARD — possible hallucination detected]
      BLOCK: rf604_capability_denial: "No search performed on Turkish MERSIS"

The phrase is a NEGATIVE FINDING ("I didn't run the search yet"), not a
capability denial. R-F741's post-filter scanned the TAIL after the
matched span for result-words but the action verb here lives BETWEEN
"no" and the tool name (and there's no result-word tail), so R-F741
missed it.

R-F763 extends the post-filter:
  - Action-noun set: search / scan / query / lookup / check / probe /
    screen / crawl / fetch / pull / hit (the noun form of an OSINT
    action)
  - Action-verb past-participle set: performed / run / ran / executed /
    queried / attempted / triggered / called / fired / invoked / tried
    / completed / conducted / undertaken (the verb form of "did the
    action")

Either set present, inside the matched span OR the post-match tail
within the same sentence, suppresses the rf604 hit — BUT ONLY on the
"no ..." branch. Branch 1 ("I cannot query OFSI") legitimately
contains an action verb as part of the denial wording; we mustn't
suppress those (regression risk verified by re-running R-F741's
test_rf741_i_cannot_query_ofsi_still_fires).
"""
from __future__ import annotations


def _hits(text):
    from aria_service.intel.self_claim_guard import scan_response
    return [v.pattern_id for v in scan_response(text, self_introspect_ran=False)]


# ── R-F763 exemptions (must NOT fire) ────────────────────────────────


def test_rf763_no_search_performed_does_not_fire():
    """The exact LIVE BUG from the 2026-05-20 audit transcript."""
    txt = "No search performed on Turkish MERSIS yet."
    assert "rf604_capability_denial" not in _hits(txt), (
        "R-F763 regression: 'No search performed on Turkish MERSIS' "
        "is a negative finding ('not yet done'), not a capability "
        "denial. Guard must not flag it."
    )


def test_rf763_no_query_executed_does_not_fire():
    txt = "No query executed against Companies House for this entity."
    assert "rf604_capability_denial" not in _hits(txt)


def test_rf763_no_scan_run_does_not_fire():
    txt = "No scan run against OFAC for the canonical name variants."
    assert "rf604_capability_denial" not in _hits(txt)


def test_rf763_no_lookup_attempted_does_not_fire():
    txt = "No lookup attempted on OFSI for the trading-name aliases."
    assert "rf604_capability_denial" not in _hits(txt)


def test_rf763_no_mersis_query_performed_does_not_fire():
    """Variant: noun-form action AFTER the tool keyword."""
    txt = "No Turkish MERSIS query performed for this trading name."
    assert "rf604_capability_denial" not in _hits(txt)


def test_rf763_no_ofsi_lookup_attempted_yet_does_not_fire():
    """Variant: 'no <tool> <noun> <past-participle> yet' — tail check."""
    txt = "Note: no OFSI lookup attempted yet on the secondary alias."
    assert "rf604_capability_denial" not in _hits(txt)


# ── R-F763 must preserve real capability denials (must STILL fire) ───


def test_rf763_i_cannot_query_ofsi_still_fires():
    """Carve-out: branch 1 ('I cannot query OFSI') legitimately
    contains 'query' as part of the denial wording — must not be
    suppressed. Verified by the existing R-F741 test, re-asserted
    here so a future R-F763 edit can't undo it."""
    txt = "I cannot query OFSI directly from this turn."
    assert "rf604_capability_denial" in _hits(txt), (
        "R-F763 regression: branch-1 'I cannot query <tool>' denials "
        "must still fire. The action-noun/verb suppression applies "
        "ONLY to the 'no ...' branch."
    )


def test_rf763_no_ofsi_adapter_still_fires():
    """No action noun/verb in the matched span — real adapter denial."""
    txt = "UK list not queried — tool has no OFSI adapter."
    assert "rf604_capability_denial" in _hits(txt)


def test_rf763_bare_no_ofsi_still_fires():
    """No action noun/verb anywhere — bare 'no <tool>' must still fire."""
    txt = "Compliance posture: no OFSI on this case."
    assert "rf604_capability_denial" in _hits(txt)


def test_rf763_no_ofsi_access_still_fires():
    """Explicit capability tail — must still fire even though 'access'
    is not in the action-verb/noun set."""
    txt = "we have no OFSI access at the moment"
    assert "rf604_capability_denial" in _hits(txt)


def test_rf763_no_outbound_email_capability_still_fires():
    """Explicit capability tail — real denial."""
    txt = "I have no outbound email capability in this build."
    assert "rf604_capability_denial" in _hits(txt)


# ── R-F763 mixed cases ───────────────────────────────────────────────


def test_rf763_action_noun_findings_alongside_real_denial():
    """Mixed transcript: one negative finding + one real denial; only
    the denial should fire."""
    txt = (
        "Sanctions screen: no OFAC scan attempted on the secondary "
        "alias. Compliance gap: tool has no OFSI adapter wired."
    )
    hits = _hits(txt)
    n = hits.count("rf604_capability_denial")
    assert n == 1, (
        f"R-F763: expected exactly 1 rf604 hit (the 'no OFSI adapter' "
        f"one); got {n}. All hits: {hits}"
    )


def test_rf763_no_search_yet_with_result_tail_still_suppressed():
    """Combo of R-F741 (result tail) AND R-F763 (action verb in span):
    must still suppress — over-belt-and-braces, not double-fire."""
    txt = "No search performed on Companies House yet — no match found."
    assert "rf604_capability_denial" not in _hits(txt), (
        "R-F763: phrase contains BOTH the action-verb pattern AND "
        "the result-word tail; either should suppress, and the "
        "combination must not double-suppress into a re-fire."
    )
