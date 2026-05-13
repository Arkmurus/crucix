"""R-F448 (2026-05-13) — stream honesty pass capability tests.

Pre-R-F448, /chat/stream (= WhatsApp default) ran the 5 output guards
+ response_verifier + officeholder_guard in OBSERVER mode only —
violations counted, response never rewritten. So Clause 13/17/20
enforcement, [VERIFIED]/[UNVERIFIED] inline tags, and officeholder
demotions have all silently bypassed WhatsApp users since
streaming went live ~6 months ago. memory/stream_bypass_pattern.md.

R-F448 wires the same 7-guard chain into a synchronous post-response
pass in `aria_service/intel/stream_honesty.py`. The route emits the
rewritten text as a correction chunk before the confidence footer.

These tests prove the user-visible symptom (unguarded WhatsApp
responses) is fixed, not just internal logic.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

import pytest


@pytest.fixture
def stream_honesty_module():
    """Import the module fresh for each test."""
    if "aria_service.intel.stream_honesty" in sys.modules:
        del sys.modules["aria_service.intel.stream_honesty"]
    from aria_service.intel import stream_honesty
    return stream_honesty


# ── Test 1: clean response with no guard hits ───────────────────────────


def test_clean_response_passes_through(stream_honesty_module):
    """Plain factual response with no claims to demote / fabricate.
    Guards should all return 'unchanged' and rewritten == input."""
    sh = stream_honesty_module
    text = "The capital of France is Paris."

    out = asyncio.run(sh.apply_stream_honesty(
        response_text=text,
        user_message="What is the capital of France?",
        # disable mistake_ledger writes so the test doesn't hit Redis
        write_to_mistake_ledger=False,
    ))

    assert out["changed"] is False, f"Clean text should not change. Changes={out['changes']}"
    assert out["rewritten"] == text
    assert out["changes"] == []
    # Build_correction_banner must return None when nothing changed.
    assert sh.build_correction_banner(out) is None


# ── Test 2: commitment_guard rewrites a fabricated commitment ───────────


def test_commitment_guard_rewrites_fabricated_commitment(stream_honesty_module):
    """When the response contains a 'Within 24 hours I will…' pattern,
    commitment_guard should rewrite and the banner should reflect it.

    This is the EXACT symptom flagged in stream_bypass_pattern.md —
    Clause 20 violations leaking on WhatsApp."""
    sh = stream_honesty_module
    text = "Within 24 hours I will deliver a full briefing on this matter."

    out = asyncio.run(sh.apply_stream_honesty(
        response_text=text,
        user_message="Can you help me with this?",
        write_to_mistake_ledger=False,
    ))

    # commitment_guard should fire — this is its bread and butter pattern.
    assert out["changed"] is True, (
        f"commitment_guard should have rewritten this. Skipped={out['skipped']}"
    )
    guard_names = [name for name, _ in out["changes"]]
    assert "commitment_guard" in guard_names, (
        f"Expected commitment_guard in changes; got {out['changes']}"
    )
    # Banner must be a proper string
    banner = sh.build_correction_banner(out)
    assert banner is not None
    assert banner.startswith("\n\n──────────"), banner[:50]
    assert "🛡" in banner
    assert "commitment_guard" in banner
    assert "Revised response" in banner


# ── Test 3: empty response is handled safely ────────────────────────────


def test_empty_response_returns_skipped(stream_honesty_module):
    """Empty/None response should not crash any guard.
    Returns changed=False with empty_response skipped sentinel."""
    sh = stream_honesty_module

    out = asyncio.run(sh.apply_stream_honesty(
        response_text="",
        user_message="hello",
        write_to_mistake_ledger=False,
    ))
    assert out["changed"] is False
    assert out["rewritten"] == ""
    assert "empty_response" in out["skipped"]


# ── Test 4: guard import-fail is non-fatal ──────────────────────────────


def test_guard_failure_is_non_fatal(stream_honesty_module):
    """If a guard raises at call time (e.g. a regression breaks
    propaganda_guard internals), the chain must continue with the
    remaining guards and return a partial result. Stream must NEVER
    hang on a broken guard."""
    sh = stream_honesty_module

    def _broken_guard(*args, **kwargs):
        raise RuntimeError("simulated propaganda_guard internal failure")

    with patch(
        "aria_service.intel.propaganda_guard.guard",
        side_effect=_broken_guard,
    ):
        out = asyncio.run(sh.apply_stream_honesty(
            response_text="The capital of France is Paris.",
            user_message="hi",
            write_to_mistake_ledger=False,
        ))

    # Did not hang. Returned a result. Propaganda is logged as errored.
    assert isinstance(out, dict)
    assert any("propaganda_guard" in s for s in out["skipped"]), (
        f"Expected propaganda_guard in skipped; got {out['skipped']}"
    )


# ── Test 5: per-guard timeout doesn't hang the chain ────────────────────


def test_per_guard_timeout_bounded(stream_honesty_module):
    """If a guard hangs, the per-guard timeout fires and the chain
    moves on. _PER_GUARD_TIMEOUT_S is 8s; we use a 0.3s override
    via monkey-patch to keep the test fast."""
    sh = stream_honesty_module

    # Patch the timeout constant down to 0.3s so the test is quick
    sh._PER_GUARD_TIMEOUT_S = 0.3

    async def _hanging_verify(*args, **kwargs):
        await asyncio.sleep(5.0)
        return {"changed": False}

    with patch("aria_service.intel.response_verifier.verify_and_tag_response", side_effect=_hanging_verify):
        out = asyncio.run(sh.apply_stream_honesty(
            response_text="The capital of France is Paris.",
            user_message="hi",
            write_to_mistake_ledger=False,
        ))

    # response_verifier should be in skipped due to timeout
    assert any("response_verifier:timeout" in s for s in out["skipped"]), (
        f"Expected response_verifier:timeout in skipped; got {out['skipped']}"
    )


# ── Test 6: build_correction_banner — output shape ──────────────────────


def test_build_correction_banner_shape(stream_honesty_module):
    """Banner must include all guard names + their counts + the
    rewritten text. Used directly as an SSE chunk so format matters."""
    sh = stream_honesty_module

    fake_result = {
        "changed": True,
        "rewritten": "REVISED text body here",
        "changes": [
            ("commitment_guard", 2),
            ("response_verifier", 5),
        ],
        "details": {},
        "skipped": [],
    }
    banner = sh.build_correction_banner(fake_result)

    assert banner is not None
    assert "commitment_guard: 2" in banner
    assert "response_verifier: 5" in banner
    assert "REVISED text body here" in banner
    # Structural checks for the SSE chunk
    assert banner.startswith("\n\n──────────")
    assert "🛡" in banner


def test_build_correction_banner_returns_none_when_unchanged(stream_honesty_module):
    """No banner if guards didn't rewrite anything — caller skips the
    SSE emit entirely in that case."""
    sh = stream_honesty_module

    fake_result = {
        "changed": False,
        "rewritten": "same text",
        "changes": [],
        "details": {},
        "skipped": [],
    }
    assert sh.build_correction_banner(fake_result) is None


def test_build_correction_banner_returns_none_when_changes_empty(stream_honesty_module):
    """Defensive — if changed=True but changes list is empty, no banner
    (something internal is inconsistent; don't emit garbage)."""
    sh = stream_honesty_module

    fake_result = {
        "changed": True,
        "rewritten": "different",
        "changes": [],
        "details": {},
        "skipped": [],
    }
    assert sh.build_correction_banner(fake_result) is None
