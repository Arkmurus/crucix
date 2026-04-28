"""Regression guards for F55 + F61 + F62 (2026-04-28 batch 3).

F55 — brain_hook circuit-breaker tripped twice during boot phase
(09:11:26 risk_indices p95=4403ms, 09:14:01 sanctions p95=2720ms)
because the rolling window had only 2-3 samples; with cold sentence-
transformer load, p95 == max == cold-start outlier. Each trip filed a
HIGH/operator_action pending action that auto-resolved on cooldown
— pure noise. Fix: require ≥ N samples before evaluating trip.

F61 — neural_memory remapped 'policy' → 'general' for "FIFA Human
Rights Policy", losing the semantic tier. Fix: add 'policy' to
VALID_CATEGORIES (same pattern as F25 'concept' / 'doctrine').

F62 — malware_pattern false-positive on defensa.com event listing
(/espana/vi-congreso-ingenieria-espacial-...). Cause: document.write
regex matched any synchronous AdSense / GTM script injection. Fix:
require dangerous src scheme or in-line obfuscation indicator.
"""
from __future__ import annotations

import importlib


def test_breaker_does_not_trip_below_min_samples():
    """The breaker must not trip until enough samples accumulate."""
    from aria_service.intel import brain_hook
    importlib.reload(brain_hook)

    # Inject a few cold-start outliers — the boot pattern.
    brain_hook._recent_latencies_ms.clear()
    brain_hook._breaker_state["open"] = False
    brain_hook._breaker_state["consecutive_high"] = 0

    for ms in [3000, 4400, 3200]:  # 3 cold-load p95-busting samples
        brain_hook._record_latency(ms)
        brain_hook._maybe_trip_breaker(reason="absorb(boot_test)")

    assert brain_hook._breaker_state["open"] is False, (
        "breaker tripped during boot with only 3 samples — the "
        "min-samples grace period is not effective"
    )


def test_breaker_still_trips_after_min_samples_with_real_high_p95():
    """Once the window has enough samples, sustained high p95 must
    still trip the breaker. Fix must not break the steady-state path."""
    from aria_service.intel import brain_hook
    importlib.reload(brain_hook)

    brain_hook._recent_latencies_ms.clear()
    brain_hook._breaker_state["open"] = False
    brain_hook._breaker_state["consecutive_high"] = 0

    # Fill the window with high latencies above the trip threshold so
    # p95 = max threshold-breach. Need ≥ MIN_SAMPLES_BEFORE_TRIP samples
    # AND _TRIP_CONSECUTIVE consecutive high p95 readings.
    for _ in range(brain_hook._MIN_SAMPLES_BEFORE_TRIP):
        brain_hook._record_latency(brain_hook._LATENCY_TRIP_MS + 500)

    # Trigger trip checks — _TRIP_CONSECUTIVE in a row to flip OPEN.
    for _ in range(brain_hook._TRIP_CONSECUTIVE):
        brain_hook._maybe_trip_breaker(reason="absorb(steady_state_test)")

    assert brain_hook._breaker_state["open"] is True, (
        "breaker failed to trip even after MIN_SAMPLES + TRIP_CONSECUTIVE "
        "consecutive high p95 readings — steady-state path is broken"
    )


# ── F61: neural_memory category vocab ────────────────────────────────────


def test_policy_is_a_valid_neural_category():
    """'FIFA Human Rights Policy' produced category='policy' from the
    LLM. After F61 it must be accepted, not remapped to 'general'."""
    from aria_service.intel.neural_memory import VALID_CATEGORIES

    assert "policy" in VALID_CATEGORIES, (
        "'policy' must be in VALID_CATEGORIES so legitimate institutional "
        "positions ('FIFA Human Rights Policy', similar) keep their tier"
    )


# ── F62: malware_pattern false-positive on AdSense ───────────────────────


def test_adsense_document_write_no_longer_flagged():
    """Synchronous AdSense / Google Tag Manager injection — common on
    defensa.com, zona-militar, etc. — must NOT trip the malware scanner.
    Real shapes from production WordPress + ad-network sites."""
    from aria_service.intel.security import scan_content

    benign_html = """
    <html><head>
    <script>
      (function() {
        var s = document.createElement('script');
        s.src = "https://www.googletagmanager.com/gtm.js?id=GTM-AAAAAA";
        s.async = true;
        document.head.appendChild(s);
      })();
      document.write('<script src="https://pagead2.googlesyndication.com/pagead/show_ads.js" type="text/javascript"></scr' + 'ipt>');
    </script>
    </head><body>VI Congreso Ingeniería Espacial — Madrid 2026</body></html>
    """  # noqa: E501
    result = scan_content(benign_html, source="defensa.com")
    threats = [t for t in result["threats"] if t["type"] == "malware_pattern"]
    assert threats == [], (
        f"AdSense / GTM document.write was incorrectly flagged as "
        f"malware: {threats}"
    )


def test_real_xss_still_flagged():
    """Conversely, document.write with a data: URI script src must
    still be caught. The fix must not over-relax."""
    from aria_service.intel.security import scan_content

    malicious = (
        "<script>document.write('"
        "<script src=\"data:text/javascript;base64,YWxlcnQoMSk=\"></script>"
        "');</script>"
    )
    result = scan_content(malicious, source="malicious.example")
    threats = [t for t in result["threats"] if t["type"] == "malware_pattern"]
    assert threats, (
        "document.write with data: URI script src must still trip the "
        "malware scanner — the F62 tightening over-relaxed"
    )


def test_inline_eval_in_document_write_still_flagged():
    """document.write of a script tag whose body contains eval/atob
    must still trip — that's the obfuscation indicator branch."""
    from aria_service.intel.security import scan_content

    malicious = (
        "<script>document.write('"
        "<script>eval(atob(\"YWxlcnQoMSk=\"))</script>"
        "');</script>"
    )
    result = scan_content(malicious, source="malicious2.example")
    threats = [t for t in result["threats"] if t["type"] == "malware_pattern"]
    assert threats, (
        "document.write of <script> with eval/atob must still trip"
    )


def test_eval_atob_obfuscation_still_flagged():
    """The other unrelated malware pattern (eval(atob(...))) must keep
    working — F62 should only touch document.write."""
    from aria_service.intel.security import scan_content

    obfuscated = '<script>eval(atob("YWxlcnQoMSk="))</script>'
    result = scan_content(obfuscated, source="obfuscated.example")
    threats = [t for t in result["threats"] if t["type"] == "malware_pattern"]
    assert threats, "eval(atob(...)) must still be flagged"
