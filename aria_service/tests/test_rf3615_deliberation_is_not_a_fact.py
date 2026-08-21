"""R-F3615 — model deliberation absorbed as knowledge, and served back as fact.

THE LIVE EVIDENCE (operator WhatsApp, 2026-08-01) — this appeared under the
heading "[ARIA KNOWLEDGE BASE — verified facts]":

    [ASSESSED] contract_intelligence:detail: ── Self-review window 1/1 ──
    We need answer audit. Need follow instructions. Need inspect window text
    and ARIA draft. Need determine if any issues in this window. We are
    auditing draft review against document window...

That is a reasoning model thinking out loud, stored and re-served as an
established fact. It got there because R-F3033 (2026-07-25) deliberately SERVED
`reasoning_content` as the answer when `content` was empty — reversed by
R-F3591 on 2026-07-31, leaving a six-day window in which any module absorbing
its own LLM output could absorb chain of thought.

R-F3608 closed the aria_chat path BY NAME. This closes the CLASS, at the two
chokepoints every module shares:
    write:  brain_hook.absorb()
    read:   knowledge._rank_knowledge_facts()

§7 IS BINDING: ARIA has infinite memory — no TTL, no prune, no eviction. The
rows already stored are QUARANTINED FROM RECALL, never deleted. Reversible;
deletion would not be.
"""
from aria_service.intel.deliberation_guard import looks_like_deliberation


# The exact strings the operator was shown. Verify the instrument on reality,
# not on invented fixtures.
_LIVE_CONTRACT_INTELLIGENCE = (
    "── Self-review window 1/1 ──\nWe need answer audit. Need follow "
    "instructions. Need inspect window text and ARIA draft. Need determine if "
    "any issues in this window. We are auditing draft review against document "
    "window. Need output format specific. If no issues, \"No issues in this "
    "window.\"? User says if no issues in this window?"
)

_LIVE_CHAT_LEAK = (
    "The user asks about the time in Portugal. I need to answer from the "
    "snippets only. Let me look at what the snippets actually contain. "
    "However, I also have CURRENT CONTEXT. But wait — can I use that? I can "
    "mark the timezone fact as CONFIRMED (W"
)


def test_the_exact_live_poisoned_rows_are_detected():
    assert looks_like_deliberation(_LIVE_CONTRACT_INTELLIGENCE)
    assert looks_like_deliberation(_LIVE_CHAT_LEAK)


def test_real_intel_prose_is_NOT_flagged():
    """The expensive error. Suppressing a genuine finding is worse than leaving
    one noisy row in recall, so the threshold must favour keeping data."""
    keep = [
        "AZURE PARKING LTD is registered at Companies House under number "
        "09876543, incorporated 2016-03-04. Two active directors.",
        "Bulgaria is an EU and NATO member state. UK citizens require no visa "
        "for short stays; entry is via the Schengen external border regime.",
        "We need to verify the UBO chain before issuing the report — the PSC "
        "filing lists a corporate parent in Cyprus.",
        "Rosoboronexport appears on the EU consolidated list. Screening "
        "returned blocked=True with a local_canonical match.",
        "The tender closes on 2026-09-01. Let me know if you need the annexes.",
        "",
        "Short.",
    ]
    for text in keep:
        assert not looks_like_deliberation(text), f"false positive on: {text[:60]}"


def test_one_weak_marker_alone_is_not_enough():
    """Density is the signal. A single analytic 'we need to' is normal prose."""
    assert not looks_like_deliberation(
        "We need to confirm the incorporation date with Companies House before "
        "the report is issued to the client this week."
    )


def test_two_distinct_markers_classify():
    assert looks_like_deliberation(
        "The user asks for the sanctions position. Let me check what the "
        "snippets contain before answering anything at all."
    )


# ── Write chokepoint ─────────────────────────────────────────────────────────


def test_absorb_refuses_deliberation():
    """FAILS BEFORE: absorb() had gates for fabricated tokens and for
    self-introspection answers, but nothing for chain of thought — so the
    contract_intelligence row above was stored with confidence=ASSESSED."""
    import asyncio
    from aria_service.intel import brain_hook as bh

    res = asyncio.run(bh.absorb(
        module="contract_intelligence",
        summary="contract self-review findings",
        detail=_LIVE_CONTRACT_INTELLIGENCE,
        confidence="ASSESSED",
    ))
    assert res.get("skipped") is True
    assert res.get("reason") == "deliberation_not_a_fact"


def test_absorb_still_accepts_a_real_finding(monkeypatch):
    """The guard must not silence the §15 pay-once-remember-forever hook."""
    from aria_service.intel import brain_hook as bh
    from aria_service.intel.deliberation_guard import looks_like_deliberation as f
    assert not f(
        "Companies House shows AZURE PARKING LTD with two active directors and "
        "an unsatisfied charge registered 2024-11-02."
    )
    # the guard is what decides; prove it does not fire on this content
    assert "deliberation_not_a_fact" not in str(
        bh.absorb.__doc__ or ""
    ), "sanity: the reason string is a runtime value, not documentation"


# ── Read chokepoint — quarantine, not deletion ──────────────────────────────


def test_recall_filters_already_stored_deliberation(monkeypatch):
    """The rows are ALREADY in the store — the write guard cannot reach them.
    Recall must not serve them as verified facts."""
    from aria_service.intel import knowledge as kn

    poisoned = {"id": "p1", "topic": "contract_intelligence:detail",
                "content": _LIVE_CONTRACT_INTELLIGENCE,
                "confidence": "ASSESSED", "accessCount": 5}
    clean = {"id": "c1", "topic": "bulgaria:travel",
             "content": "Bulgaria is an EU and NATO member state; no visa is "
                        "required for UK citizens on short stays.",
             "confidence": "CONFIRMED", "accessCount": 5}

    # VERIFY THE INSTRUMENT. The ranker reads `_cache["facts"]` directly
    # (knowledge.py:1789). An earlier draft patched a `_load_facts_cached` that
    # does not exist: the ranker then saw an empty cache, returned [], and the
    # test PASSED for the wrong reason. Asserting the clean row IS returned is
    # what makes this a real test rather than a vacuous one.
    monkeypatch.setattr(kn, "_cache", {"facts": [poisoned, clean]}, raising=False)
    monkeypatch.setattr(kn, "_search_lc_facts_id", None, raising=False)

    out = kn._rank_knowledge_facts("bulgaria window audit", 10)
    ids = [f.get("id") for f in out]

    assert "c1" in ids, (
        "instrument check: the clean fact must come back, or this test proves "
        "nothing about filtering"
    )
    assert "p1" not in ids, "deliberation must not be served as a verified fact"


def test_the_quarantine_does_not_delete(monkeypatch):
    """§7 — infinite memory. The filter runs at READ time; nothing in the
    quarantine path may remove, expire, or rewrite a stored row."""
    from aria_service.intel import knowledge as kn
    from ._source_probe import function_source

    # ── R-F4226 / C-206 — this guard had gone blind, and the code is fine. ────
    # It read `inspect.getsource(kn._rank_knowledge_facts)` and looked for the
    # R-F3615 marker. That function was later split into a thin wrapper that
    # delegates to `_rank_knowledge_facts_inner`, where the quarantine actually
    # lives — so the marker was no longer in the source the test read, and it
    # died on `ValueError: substring not found`. The §7 no-delete property it
    # protects was never at risk; the test simply stopped being able to see it.
    #
    # Two changes, both deliberate:
    #   1. read the function that HOLDS the block, and
    #   2. via function_source, not inspect.getsource — §16/R-F3597: getsource
    #      slices at the line numbers captured AT IMPORT, so on a shared tree it
    #      can silently return a DIFFERENT function's body.
    src = function_source(kn, "_rank_knowledge_facts_inner")
    assert "R-F3615" in src, (
        "the recall quarantine is no longer in _rank_knowledge_facts_inner. If it "
        "MOVED, point this guard at its new home; if it was REMOVED, deliberation "
        "text is being served as verified fact again (R-F3615)."
    )
    i = src.index("R-F3615")
    # STRIP COMMENTS FIRST. An earlier draft scanned the raw block and matched
    # the word "write" inside the phrase "write-side guard" in a comment — the
    # same substring-matched-a-COMMENT failure that once made a dead-code gate
    # report 0 orphans while 61 existed. Scan CODE.
    code = "\n".join(
        line.split("#", 1)[0]
        for line in src[i:].splitlines()
        if not line.strip().startswith("#")
    )
    for destructive in ("delete", ".pop(", "remove(", "save_facts", "write"):
        assert destructive not in code, (
            f"the recall quarantine must not {destructive} — §7 forbids deletion"
        )
    assert "looks_like_deliberation" in code, "instrument check: the filter is here"


def test_the_wrapper_still_reaches_the_quarantine():
    """R-F4226 — the split that blinded the guard above must stay honest.

    `_rank_knowledge_facts` is now a wrapper. If it ever stops delegating to
    `_rank_knowledge_facts_inner`, the quarantine is unreachable at recall time
    and the guard above would still pass, because it reads the inner function in
    isolation. Pin the edge, not just the node.
    """
    from aria_service.intel import knowledge as kn
    from ._source_probe import function_source

    outer = function_source(kn, "_rank_knowledge_facts")
    assert "_rank_knowledge_facts_inner(" in outer, (
        "_rank_knowledge_facts no longer calls _rank_knowledge_facts_inner — the "
        "R-F3615 recall quarantine is no longer on the recall path"
    )


def test_both_recall_consumers_are_covered():
    """search_knowledge() renders the block; search_fact_records() feeds
    programmatic callers. Filtering only the renderer would leave the other
    serving deliberation — the producer/consumer split behind several of this
    session's defects."""
    import inspect
    from aria_service.intel import knowledge as kn
    for fn in (kn.search_knowledge, kn.search_fact_records):
        assert "_rank_knowledge_facts" in inspect.getsource(fn), (
            f"{fn.__name__} must rank through the filtered chokepoint"
        )
