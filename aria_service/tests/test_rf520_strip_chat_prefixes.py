"""R-F520 — strip chat_ep prefixes before local-reasoning + cache write.

Live failure 2026-05-14 ~13:00 BST (after R-F518 deployed as v843, build_rev
confirmed 'R-F518 · sha f989ef9a'): probed /chat with "Is Bumblestaff
Industries sanctioned in the UK?" → cloud_llm answered honestly, response
cached. Subsequent probe "Is ZTE Corporation sanctioned in the UK?" matched
the just-cached Bumblestaff entry at 0.955 semantic confidence and served
the Bumblestaff response.

R-F518's entity-overlap gate should have blocked this. It didn't because
the cached `normalised` field contains ~30 comprehension-prefix tokens
(comprehension/complexity/confidence/response/contract/understood/user/
message/etc.) — both queries had the same prefix tokens, so the entity-
overlap intersection was 30+ tokens. R-F518 lets matches with ≥1 overlap
through.

R-F518's capability tests used clean normalised text and didn't catch this
in code. R-F518 deployed and live, but functionally incomplete.

R-F520 fix: strip the chat_ep-added blocks BEFORE the message is passed to
reasoning_router.try_local_reasoning AND before
reasoning_router.record_cloud_llm_response. The LLM still gets the full
prefix-bearing message_for_llm — that's what the comprehension/scratchpad
clauses are designed to influence. The local-reasoning + cache layers see
only the user's actual question.

These tests pin the EXACT prefix shape live system produces, so a
regression breaking the strip logic surfaces immediately.
"""
from __future__ import annotations

from aria_service.aria_engine import _strip_chat_prefixes


# ───────────── R-F520 — production prefix shape pinned ─────────────


def _make_live_chat_message(user_question: str, *, with_scratchpad: bool = False,
                            with_group_context: bool = False,
                            with_tool_context: bool = False) -> str:
    """Build the exact shape chat_ep:6500-6509 (and adjacent appends) produce.

    Matches comprehension.build_prefix() output + chat_ep's prepend +
    optional appended blocks.
    """
    parts = []
    parts.append(
        "[COMPREHENSION PASS — Clause 21, Understand Before Act]\n"
        "Complexity: SIMPLE\n"
        "Confidence: CLEAR\n"
        "\n"
        "RESPONSE CONTRACT for this turn:\n"
        "  1. Start your reply with 'UNDERSTOOD AS: <one sentence restating "
        "what the user is asking>'. Keep it tight.\n"
        "  2. If you are filling any gap with an assumption, name it "
        "explicitly: 'Assuming <X>...'.\n"
        "  3. If the request is HIGH-STAKES and you are NOT 100% confident, "
        "state what you would need to confirm — do NOT fabricate verifiable "
        "facts (jurisdictions, financials, sanctions status) to fill the gap.\n"
        "[END COMPREHENSION PASS]\n"
        "\n"
        f"USER MESSAGE FOLLOWS:\n{user_question}"
    )
    if with_group_context:
        parts.append(
            "\n\n[GROUP CONTEXT — recent messages in this chat, for "
            "situational awareness only. The user's actual question "
            "is the line above. Do NOT respond to messages in this "
            "block...]\n"
            "Alice: how's the deal going?\n"
            "Bob: still waiting on the OFSI check"
        )
    if with_tool_context:
        parts.append(
            "\n\n[I have already run the appropriate tool on your request. "
            "Use the data below...]\n"
            "[TOOL: sanctions_screen] hit=0 variants_tried=4"
        )
    if with_scratchpad:
        parts.append(
            "\n\n[CLAUSE 22 — Think Before Speak]\n"
            "Before your user-facing response, produce a private reasoning "
            "block wrapped in exactly these tags:\n"
            "  <scratchpad>\n"
            "  1. What is the actual question being asked?\n"
            "  ...\n"
            "  </scratchpad>"
        )
    return "".join(parts)


def test_rf520_strips_comprehension_prefix():
    """The live failure repro shape: comprehension prefix only."""
    raw = _make_live_chat_message("Is Bumblestaff Industries sanctioned in the UK?")
    assert _strip_chat_prefixes(raw) == "Is Bumblestaff Industries sanctioned in the UK?"


def test_rf520_strips_prefix_plus_group_context():
    """WhatsApp shape: prefix + appended group context."""
    raw = _make_live_chat_message(
        "Is ZTE Corporation sanctioned in the UK?",
        with_group_context=True,
    )
    assert _strip_chat_prefixes(raw) == "Is ZTE Corporation sanctioned in the UK?"


def test_rf520_strips_prefix_plus_tool_context():
    """Tool-execution shape: prefix + appended tool result."""
    raw = _make_live_chat_message(
        "Is Hikvision sanctioned in the UK?",
        with_tool_context=True,
    )
    assert _strip_chat_prefixes(raw) == "Is Hikvision sanctioned in the UK?"


def test_rf520_strips_prefix_plus_scratchpad():
    """Clause 22 scratchpad-appended shape."""
    raw = _make_live_chat_message(
        "Is Sinovac Biotech sanctioned in the UK?",
        with_scratchpad=True,
    )
    assert _strip_chat_prefixes(raw) == "Is Sinovac Biotech sanctioned in the UK?"


def test_rf520_strips_all_blocks_together():
    """Full WhatsApp-with-tool-with-scratchpad shape — the maximal-prefix case."""
    raw = _make_live_chat_message(
        "Is Acme Steel Holdings sanctioned in the UK?",
        with_group_context=True,
        with_tool_context=True,
        with_scratchpad=True,
    )
    assert _strip_chat_prefixes(raw) == "Is Acme Steel Holdings sanctioned in the UK?"


def test_rf520_idempotent_on_clean_input():
    """No prefix → no strip. Important: must not corrupt unprefixed messages
    (e.g. /chat invoked directly via curl without comprehension pass)."""
    clean = "Is Acme Corp on the OFSI list?"
    assert _strip_chat_prefixes(clean) == clean


def test_rf520_handles_empty_and_none():
    """Defensive: graceful on empty/None input (never raises)."""
    assert _strip_chat_prefixes("") == ""
    assert _strip_chat_prefixes(None) is None


# ───────────── R-F520 — entity-overlap downstream check ─────────────


def test_rf520_clean_normalised_has_no_prefix_jargon():
    """After R-F520 strip, _normalise_question over the result must
    NOT contain comprehension-prefix tokens that defeated R-F518's gate.
    This is the bridge test linking R-F520 to R-F518."""
    from aria_service.intel.reasoning_library import _normalise_question, _entity_tokens

    raw = _make_live_chat_message("Is Bumblestaff Industries sanctioned in the UK?")
    stripped = _strip_chat_prefixes(raw)
    normalised = _normalise_question(stripped)

    # Should NOT contain any of the comprehension-prefix tokens that
    # were leaking through before R-F520.
    leaky_tokens = {
        "comprehension", "complexity", "confidence", "response",
        "contract", "understood", "user", "message", "follows",
        "clause", "request", "stakes", "fabricate", "ambiguity",
    }
    normalised_tokens = set(normalised.split())
    assert not (leaky_tokens & normalised_tokens), (
        f"Comprehension-prefix tokens leaked into normalised: "
        f"{leaky_tokens & normalised_tokens}"
    )

    # And the entity tokens should be just Bumblestaff/Industries
    entities = _entity_tokens(normalised)
    assert entities == {"bumblestaff", "industries"}, (
        f"Expected clean entity tokens, got: {entities}"
    )


def test_rf520_zte_vs_bumblestaff_no_entity_overlap_after_strip():
    """The live failure pin: ZTE vs Bumblestaff cached entry. With R-F520
    strip on both sides, R-F518's entity-gate correctly rejects the match."""
    from aria_service.intel.reasoning_library import _normalise_question, _entity_tokens

    bumble_raw = _make_live_chat_message("Is Bumblestaff Industries sanctioned in the UK?")
    zte_raw = _make_live_chat_message("Is ZTE Corporation sanctioned in the UK?")

    bumble_entities = _entity_tokens(_normalise_question(_strip_chat_prefixes(bumble_raw)))
    zte_entities = _entity_tokens(_normalise_question(_strip_chat_prefixes(zte_raw)))

    assert not (bumble_entities & zte_entities), (
        f"Entity overlap between Bumblestaff and ZTE after strip — "
        f"R-F520 not fully blocking. Bumble={bumble_entities}, ZTE={zte_entities}"
    )
