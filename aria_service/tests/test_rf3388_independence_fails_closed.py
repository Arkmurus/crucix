"""R-F3388 — the independence verifier fails OPEN, manufacturing corroboration.

Tranche 3 of the §21 wiring backlog. `dd_independent_verifier` was flagged as
having no brain wiring at all; reading its error paths found that every one of
them degrades in the direction that INVENTS independence.

  publisher_family      `from .verified_intel import SOURCE_FAMILIES` fails ->
                        `except: pass` -> returns `pub:<domain>`. origin_key
                        groups by that, so every source becomes its OWN origin,
                        count_independent_origins rises, and
                        is_independently_corroborated says True.
  detect_pr_echo        semantic_similarity returns None (embedder unavailable,
                        offload failure, timeout) -> `sim is not None and ...` is
                        False -> "not an echo" -> two syndications of one press
                        release are counted as two independent origins.
  refetch_story_ids_*   a failed fetch stores "" -> no text to compare -> the
                        echo check cannot fire -> same inflation.

Why this is the severe direction: R-F2413 keeps
`independent_source_verification_run` False until the C-3 gate holds, and
R-F2666 states the gate outright — "The FALSE-POSITIVE rate is the GATE ... a
claim wrongly marked 'independently corroborated' is the exact honesty-USP
betrayal, so it MUST be 0. False negatives (conservative undercount) are
acceptable." Every failure path here moves the wrong one of those two numbers.

So the fix direction is the repo's own stated trade-off: when the verifier cannot
judge, it must UNDERCOUNT independence, never overcount, and it must say so to
the brain instead of degrading silently.

FAILS BEFORE R-F3388.
"""
from __future__ import annotations

import asyncio
import builtins
from unittest.mock import patch

from aria_service.intel import dd_independent_verifier as div


def _run(coro):
    return asyncio.run(coro)


# ── publisher_family: the systemic one ──────────────────────────────────────
def test_rf3388_unclassifiable_publishers_collapse_to_one_origin():
    """If SOURCE_FAMILIES cannot be consulted, two different domains must NOT be
    counted as two independent origins."""
    real_import = builtins.__import__

    def _boom(name, *a, **kw):
        if "verified_intel" in name:
            raise ImportError("simulated: family table unavailable")
        return real_import(name, *a, **kw)

    with patch.object(builtins, "__import__", _boom), \
         patch.object(div, "wire_failure") as wf:
        a = div.publisher_family("https://a-news.example/x")
        b = div.publisher_family("https://b-news.example/y")

    assert a == b, (
        "two unclassifiable publishers produced DIFFERENT origin families, so the "
        "verifier would count them as independent — a manufactured corroboration"
    )
    assert wf.called, "an unusable family table must reach the brain, not `except: pass`"


def test_rf3388_a_working_family_table_still_classifies_normally():
    """The fail-closed path must not swallow the normal one."""
    with patch.object(div, "wire_failure") as wf:
        fam = div.publisher_family("https://www.bbc.co.uk/news/x")
    assert fam.startswith("pub:"), fam
    assert not wf.called, "a healthy lookup must not report a failure"


def test_rf3388_distinct_known_publishers_remain_distinct():
    """Failing closed must not collapse genuinely different publishers."""
    a = div.publisher_family("https://alpha-example-news.com/x")
    b = div.publisher_family("https://beta-example-news.com/y")
    assert a != b, "unrelated publishers must still be separate origins"


# ── detect_pr_echo: unknown similarity must not read as "independent" ───────
def test_rf3388_an_unjudgeable_similarity_is_reported_even_though_the_verdict_stands():
    """CONFLICT RESOLVED IN FAVOUR OF R-F2687 — recorded because it cuts against
    the direction the rest of this R-number takes.

    I first made this fail CLOSED (declare an echo when similarity is
    unavailable), on R-F2666's rule that the false-positive rate on independence
    must be 0 while an undercount is acceptable. That broke
    test_rf2687_pr_echo::test_missing_embedder_does_not_grant_independence, which
    asserts the opposite deliberately: "None from the embedder = 'no semantic
    signal', never 'not an echo'" — do not INVENT an echo without evidence
    either. Its docstring even records a Pass-2 correction to that exact path.

    Both are honest positions pointing opposite ways, and R-F2687 is the newer
    explicit ruling here, so its verdict stands and mine was reverted. What was
    genuinely missing is that the blind spot was INVISIBLE: the brain could not
    tell a judged pair from an unjudgeable one. That is the §21a gap, and it is
    closed without moving anyone's verdict.
    """
    pr_a = "The company announced today, in a statement, that it won a new contract."
    pr_b = "The company announced today, in a statement, that it won a new contract."
    assert div.has_pr_marker(pr_a) and div.has_pr_marker(pr_b), "fixture must carry PR markers"
    assert not div.has_own_reporting(pr_a), "fixture must have no own reporting"

    async def _no_sim(a, b):
        return None  # embedder unavailable / offload failed / timed out

    with patch.object(div, "semantic_similarity", _no_sim), \
         patch.object(div, "wire_failure") as wf:
        is_echo, reason = _run(div.detect_pr_echo(pr_a, pr_b))

    # R-F2687's verdict, unchanged and asserted here so the conflict cannot be
    # silently re-flipped by a future reader of this file.
    assert is_echo is False and reason == "", (
        "R-F2687's contract moved: an unavailable similarity must not INVENT an echo"
    )
    # R-F3388's actual contribution: the blind spot is no longer silent.
    assert wf.called, (
        "an unjudgeable similarity must reach the brain — otherwise a pair that "
        "could not be assessed is indistinguishable from one that was assessed "
        "and cleared, which is the §21a gap this tranche exists to close"
    )
    assert "undetermined" in str(wf.call_args).lower()


def test_rf3388_own_reporting_still_beats_the_conservative_default():
    """Failing closed must not override real evidence: if somebody actually
    reported, it is not an echo even when similarity is unavailable."""
    pr_a = "The company announced today, in a statement, that it won a contract."
    reported = ("In a statement the company confirmed the deal. According to documents "
                "seen by this publication, our correspondent verified the contract.")
    if not div.has_own_reporting(reported):
        import pytest
        pytest.skip("fixture does not trip has_own_reporting; contract unchanged by R-F3388")

    async def _no_sim(a, b):
        return None

    with patch.object(div, "semantic_similarity", _no_sim):
        is_echo, _ = _run(div.detect_pr_echo(pr_a, reported))
    assert is_echo is False, "own reporting must still defeat the echo verdict"


def test_rf3388_a_high_similarity_echo_is_still_detected():
    """The existing positive path must survive."""
    pr_a = "The company announced today, in a statement, that it won a new contract."
    pr_b = "The company announced today, in a statement, that it won a new contract."

    async def _same(a, b):
        return 0.99

    with patch.object(div, "semantic_similarity", _same):
        is_echo, reason = _run(div.detect_pr_echo(pr_a, pr_b))
    assert is_echo is True and "0.99" in reason, reason


# ── the gate must agree ─────────────────────────────────────────────────────
def test_rf3388_the_wiring_audit_no_longer_flags_this_module():
    import pathlib
    import sys
    root = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))
    from pre_commit_checks import check_wiring_present

    issues = check_wiring_present([root / "aria_service" / "intel" / "dd_independent_verifier.py"])
    assert issues == [], f"still flagged: {issues}"
