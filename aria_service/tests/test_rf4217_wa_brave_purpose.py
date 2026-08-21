"""R-F4217 / C-197: ARIA WA is a first-class Brave purpose, and it must STAY one.

OPERATOR DIRECTIVE, 2026-08-21: "include aria wa on the brave api also, that was
requested and done a while back but keeps breaking".

WHY IT KEPT BREAKING — and it is not a code add/remove cycle. `git log -S` shows
`_DD_BRAVE_PURPOSES` was introduced exactly ONCE (R-F3946, 2026-08-13). The
reverting mechanism is the DOCUMENT: CLAUDE.md §17 states RULE ONE as "Brave is
for DD reports **and nothing else**", and §26/§20 make that file the first thing
every session reads. So each session dutifully re-enforced DD-only and stripped
WA again. The floor told every agent to remove the capability the operator had
asked for. CLAUDE.md §17 records this exact failure shape for the Anthropic half
("the floor every session reads first, telling each of them to preserve the
broken state") — this is the same shape, one clause over.

So the durable fix is THREE parts, and the code is only one of them:
  1. code   — "wa" is an allowed purpose at the ONE decision point
  2. doc    — CLAUDE.md §17 records the amendment, with date and quote
  3. test   — this file, so a future "tidy back to DD-only" fails CI loudly

RULE ONE IS NOT WEAKENED ANYWHERE ELSE. The Anthropic half is untouched. Every
non-WA, non-DD purpose — chat, explore, student, research, "" — is still
refused, and R-F3946's own parametrised test still pins that.
"""

from __future__ import annotations

import pytest

from aria_service.intel import web_search as ws


@pytest.fixture(autouse=True)
def _brave_key_present(monkeypatch):
    monkeypatch.setattr(ws, "BRAVE_API_KEY", "test-key-not-real", raising=False)
    monkeypatch.setattr(ws, "_BRAVE_GLOBALLY_OFF", False, raising=False)
    ws.reset_brave_usage_counters()


# ── the grant the operator asked for ─────────────────────────────────────────

def test_wa_purpose_gets_brave():
    token = ws.enable_brave_for_scope(True, purpose="wa")
    try:
        assert ws.brave_is_enabled() is True, (
            "OPERATOR DIRECTIVE 2026-08-21: ARIA WA is on the Brave API. Do NOT "
            "'fix' this by restoring DD-only — that is the revert that has "
            "happened repeatedly. See CLAUDE.md §17 RULE ONE (amended)."
        )
    finally:
        ws.reset_brave_scope(token)


def test_dd_purpose_still_gets_brave():
    token = ws.enable_brave_for_scope(True, purpose="dd")
    try:
        assert ws.brave_is_enabled() is True
    finally:
        ws.reset_brave_scope(token)


def test_wa_is_declared_in_the_allow_list():
    st = ws.brave_policy_status()
    assert "wa" in st["allowed_purposes"], st
    assert "dd" in st["allowed_purposes"], st


# ── RULE ONE otherwise intact — this is the half that must still bite ────────

@pytest.mark.parametrize("purpose", ["chat", "explore", "student", "research", "", "web", "api"])
def test_every_other_purpose_is_still_refused(purpose):
    token = ws.enable_brave_for_scope(True, purpose=purpose)
    try:
        assert ws.brave_is_enabled() is False, (
            f"purpose {purpose!r} reached the paid Brave key. RULE ONE confines "
            f"Brave to DD and ARIA WA only."
        )
    finally:
        ws.reset_brave_scope(token)


def test_grants_are_counted_per_purpose_so_wa_spend_is_visible():
    """WA on Brave is real spend; it must be attributable, not merged into DD."""
    for p in ("wa", "wa", "dd"):
        token = ws.enable_brave_for_scope(True, purpose=p)
        try:
            ws.brave_is_enabled()
        finally:
            ws.reset_brave_scope(token)
    st = ws.brave_policy_status()
    assert st["grants_by_purpose"]["wa"] == 2, st
    assert st["grants_by_purpose"]["dd"] == 1, st
    assert st["unauthorised_grants"] == 0, st


def test_unauthorised_grant_is_still_a_live_breach():
    """The falsifiable half (R-F3858): the gate must be able to report a breach."""
    from aria_service.llm import fallback as fb
    import aria_service.intel.web_search as _ws
    original = _ws.brave_policy_status
    try:
        _ws.brave_policy_status = lambda: {          # type: ignore[assignment]
            "confined_to_allowed": True, "confined_to_dd": True,
            "allowed_purposes": ["dd", "wa"],
            "grants_by_purpose": {"dd": 0, "wa": 0},
            "unauthorised_grants": 7, "non_dd_grants": 7,
            "non_dd_scope_refused": 0, "dd_grants": 0,
            "key_present": True, "globally_disabled": False,
        }
        st = fb.rule_one_status()
        assert st["brave_confined_to_dd"] is False, (
            "a grant to a purpose that is NOT on the allow-list must still read "
            "as a live breach")
        assert st["breached"] is True
    finally:
        _ws.brave_policy_status = original           # type: ignore[assignment]


# ── the client must not be able to elevate itself ────────────────────────────

def test_a_client_declared_channel_cannot_claim_dd():
    """`channel` is client-supplied. It must map to a purpose, never BE one.

    Otherwise anything that can POST /chat gets the paid DD key by sending
    {"channel": "dd"} — a caller-controlled string selecting a paid backend.
    """
    from aria_service.routes.aria import _channel_brave_purpose
    assert _channel_brave_purpose("wa") == "wa"
    assert _channel_brave_purpose("dd") == ""
    assert _channel_brave_purpose("web") == ""
    assert _channel_brave_purpose("") == ""
    assert _channel_brave_purpose(None) == ""


def test_chat_request_carries_a_channel_field():
    from aria_service.routes.aria import ChatRequest
    req = ChatRequest(message="hi", channel="wa")
    assert req.channel == "wa"
    assert ChatRequest(message="hi").channel == ""


# ── the predicate itself, called directly (§3c) ──────────────────────────────

@pytest.mark.parametrize("purpose", ["dd", "wa", "DD", "WA", " wa ", "Wa"])
def test_is_allowed_brave_purpose_accepts_the_allow_list(purpose):
    """One predicate, one policy — case- and whitespace-insensitive."""
    assert ws.is_allowed_brave_purpose(purpose) is True


@pytest.mark.parametrize("purpose", ["chat", "explore", "student", "research",
                                     "web", "api", "", "   ", None, "dd ops"])
def test_is_allowed_brave_purpose_refuses_everything_else(purpose):
    assert ws.is_allowed_brave_purpose(purpose) is False


def test_is_dd_brave_purpose_alias_delegates_to_the_one_predicate():
    """The historical name is kept for existing callers; it must not fork the policy.

    Two predicates would be the forked-measure shape R-F2639 forbids — the exact
    way a policy starts disagreeing with itself.
    """
    for p in ("dd", "wa", "chat", "", None):
        assert ws.is_dd_brave_purpose(p) is ws.is_allowed_brave_purpose(p)
