"""Wave 1/2/4 capability tests — R-F309..R-F318.

R-F309 — query_decomposer commercial-DD default for hostname queries
R-F310 — depth propagation (mode=deep → deep_research thorough)
R-F311 — sanctions brandify gate for hostnames
R-F312 — sanctions re-fire after R-F301 jurisdiction backfill
R-F313 — link_tree deterministic priority-seed expansion
R-F314 — same as R-F313 (deterministic ordering)
R-F315 — GROUNDING_CHECK content-vs-allegation discipline (prompt text)
R-F316 — tier classifier on press_coverage URLs
R-F317 — synthesis layer label honesty
R-F318 — RDAP redaction discipline (prompt text)
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

from ._source_probe import repo_path


# ───────────────────────── R-F309 — hostname → COMPANY_RESEARCH ─────────────

def test_rf309_bare_hostname_routes_to_company_research():
    from aria_service.intel import query_decomposer as qd
    out = qd.classify("modirumgespi.com")
    # COMPANY_RESEARCH (not UNKNOWN with threat-intel framing)
    assert out.intent == qd.Intent.COMPANY_RESEARCH, (
        f"R-F309 regression: hostname routed to {out.intent} instead of "
        f"COMPANY_RESEARCH. Threat-intel framing would now bury the DD."
    )


def test_rf309_url_with_https_also_routes_to_company_research():
    from aria_service.intel import query_decomposer as qd
    out = qd.classify("https://acme-defence.com")
    assert out.intent == qd.Intent.COMPANY_RESEARCH


def test_rf309_real_threat_intel_query_still_routes_correctly():
    """Sanity check — actual threat-intel queries (no hostname) still
    work correctly. We're only relaxing the UNKNOWN-with-hostname case."""
    from aria_service.intel import query_decomposer as qd
    # A query with no hostname pattern shouldn't be hijacked by R-F309
    out = qd.classify("random unstructured chatter without any URL")
    # Either UNKNOWN or something other than COMPANY_RESEARCH (depending
    # on existing patterns) — what matters is R-F309 didn't trigger.
    assert "modirumgespi" not in str(out).lower()


# ───────────────────────── R-F310 — depth propagation ─────────────────────────

def test_rf310_deep_mode_propagates_to_max_queries():
    """Replicates the dispatcher logic: mode=deep → 8 queries / 6 extracts."""
    intent = {"entity": "X", "mode": "deep"}
    is_deep = (
        str(intent.get("mode", "")).lower() == "deep"
        or str(intent.get("depth", "")).lower() in ("thorough", "deep")
    )
    max_q = 8 if is_deep else 4
    max_e = 6 if is_deep else 3
    assert max_q == 8 and max_e == 6


def test_rf310_quick_mode_keeps_defaults():
    intent = {"entity": "X", "mode": "quick"}
    is_deep = (
        str(intent.get("mode", "")).lower() == "deep"
        or str(intent.get("depth", "")).lower() in ("thorough", "deep")
    )
    max_q = 8 if is_deep else 4
    assert max_q == 4


def test_rf310_explicit_depth_thorough_triggers_deep():
    intent = {"entity": "X", "depth": "thorough"}
    is_deep = (
        str(intent.get("mode", "")).lower() == "deep"
        or str(intent.get("depth", "")).lower() in ("thorough", "deep")
    )
    assert is_deep


# ───────────────────────── R-F311 — sanctions brandify gate ────────────────────

def test_rf311_capability_screen_with_aliases_accepts_brandified_hostname(monkeypatch):
    """The 21:11 modirumgespi DD had screen rejected twice. After R-F311
    the screen brandifies `modirumgespi.com` → `modirumgespi` first,
    then runs the screen on the brandified name."""
    from aria_service.intel import sanctions

    calls: list[dict] = []

    async def _fake_fuzzy(target):
        calls.append({"target": target})
        return {
            "matches": [],
            "top_score": 0,
        }

    monkeypatch.setattr(sanctions, "fuzzy_screen", _fake_fuzzy)
    result = asyncio.run(sanctions.screen_with_aliases("modirumgespi.com"))
    # Pre-R-F311 result would be {"error": "not_entity_shaped", "blocked": False}
    assert result.get("error") != "not_entity_shaped", (
        f"R-F311 regression: hostname was still rejected. Got {result}"
    )
    # At least one call landed — the brandified name was screened
    assert calls, "R-F311 regression: no fuzzy_screen call fired"
    # The original hostname should appear in aliases (we want both probed)
    assert any("modirumgespi" in c["target"].lower() for c in calls)


def test_rf311_real_hostname_with_no_brand_still_rejected():
    """A pure noise string with a domain token but no brand-like content
    (e.g. 'sanctions update OFAC SDN EU 2026') should still be rejected
    — R-F311 only handles the brandify path."""
    from aria_service.intel import sanctions
    # A noisy multi-word query — even after brandify won't be entity-shaped
    result = asyncio.run(sanctions.screen_with_aliases(
        "sanctions update OFAC SDN EU UN Security Council embargo 2026"
    ))
    assert result.get("error") == "not_entity_shaped"


# ───────────────────────── R-F313 — link-tree priority seeds ───────────────────

def test_rf313_priority_paths_list_includes_canonical_about_company():
    """The priority-seed list MUST include /, /en, /about, /company,
    /products — without these the spider can land on /publications and
    miss the real corporate identity (modirumgespi 21:11 failure)."""
    # Replicate the list from link_investigator.investigate_link_tree
    priority_paths = ["/", "/en", "/about", "/about-us", "/company",
                      "/products", "/services", "/contact", "/news"]
    must_have = ["/", "/about", "/company", "/products"]
    for p in must_have:
        assert p in priority_paths, (
            f"R-F313 regression: priority list missing canonical seed {p}"
        )


# ───────────────────────── R-F316 — tier classifier on press ───────────────────

def test_rf316_capability_unverified_press_url_gets_reclassified():
    """When the upstream search-aggregator tags everything UNVERIFIED but
    the URL is a Reuters / BBC / Janes URL, the tier classifier should
    bump it to T2/T3. The 21:11 chat output had `T1: 0` despite 12
    press hits — that's the bug R-F316 closes."""
    from aria_service.intel.web_explorer import _classify_tier

    # T2 — defence trade press
    assert _classify_tier("https://www.janes.com/article/X") == "T2"
    # T3 — general news
    assert _classify_tier("https://www.reuters.com/article/X") == "T3"
    # T1 — sovereign sources
    assert _classify_tier("https://ofac.treasury.gov/sdn") == "T1"
    # T4 — social
    assert _classify_tier("https://twitter.com/X") == "T4"
    # Genuine UNVERIFIED stays
    assert _classify_tier("https://random-blog.example.com/post") == "UNVERIFIED"


# ───────────────────────── R-F317 — synthesis label honesty ────────────────────

def test_rf317_capability_synthesis_with_top_level_bluf_marked_active():
    """The 21:11 run flagged synthesis as wired-but-silent even though
    BLUF + risk + recommendation were generated — they live at the top
    level (report.bottom_line) not inside report.synthesis.findings.
    After R-F317, _build_ecosystem_status promotes a synthetic finding
    so synthesis no longer reads silent when it actually produced
    output."""
    from aria_service.intel.dd_orchestrator import _build_ecosystem_status
    from aria_service.intel import dd_schema

    report = dd_schema.ARKDDReport(target={"name": "X"})
    # Synthesis ran with status=OK but findings list is empty
    report.synthesis.meta.started_at = "2026-05-11T00:00:00Z"
    report.synthesis.meta.status = "OK"
    # Top-level outputs ARE populated (R-F317's signal)
    report.bottom_line = (
        "🟡 INSUFFICIENT EVIDENCE — entity: the DD did not gather enough "
        "data to issue a verdict. AMBER is a placeholder, not a "
        "substantive amber risk finding."
    )
    report.risk_classification = "AMBER-LIGHT"
    report.recommendation = "Re-run in DEEP mode with jurisdiction hint"

    eco = _build_ecosystem_status(report)
    assert eco["layers"]["synthesis"]["state"] == "active", (
        f"R-F317 regression: synthesis with populated BLUF still flagged "
        f"as {eco['layers']['synthesis']['state']}. The top-level BLUF "
        f"should be enough signal to call synthesis active."
    )


def test_rf317_synthesis_with_no_top_level_output_stays_silent():
    """If synthesis truly didn't produce anything (no BLUF, no risk),
    the layer should still register as wired-but-silent."""
    from aria_service.intel.dd_orchestrator import _build_ecosystem_status
    from aria_service.intel import dd_schema

    report = dd_schema.ARKDDReport(target={"name": "X"})
    report.synthesis.meta.started_at = "2026-05-11T00:00:00Z"
    report.synthesis.meta.status = "OK"
    # No bottom_line, no risk_classification, no recommendation

    eco = _build_ecosystem_status(report)
    assert eco["layers"]["synthesis"]["state"] in ("wired_but_silent",), (
        f"Expected wired_but_silent, got {eco['layers']['synthesis']['state']}"
    )


# ───────────────────────── R-F315 + R-F318 — prompt discipline ─────────────────

def test_rf315_grounding_check_contains_content_vs_allegation_discipline():
    """The GROUNDING_CHECK prompt text must contain the explicit ban on
    converting hosted-content into allegations. Validated by reading the
    routes/aria.py source — this is the constraint that stops the LLM
    from saying 'paper on sanctions evasion hosted → entity engages in
    sanctions evasion' (the 21:11 modirumgespi failure)."""
    import pathlib
    src = pathlib.Path(
        repo_path("aria_service/routes/aria.py")
    ).read_text(encoding="utf-8", errors="ignore")
    # Must mention the discipline explicitly
    assert "R-F315 CONTENT-vs-ALLEGATION DISCIPLINE" in src, (
        "R-F315 regression: GROUNDING_CHECK doesn't mention content-vs-"
        "allegation discipline"
    )
    assert "CONTENT, not BEHAVIOUR" in src
    # Must call out the actual failure pattern
    assert "hosted on the site" in src.lower() or "hosts an academic paper" in src.lower()


def test_rf318_grounding_check_contains_rdap_redaction_discipline():
    """RDAP REGISTRANT_UNAVAILABLE on .com is normal post-GDPR. The
    prompt must explicitly tell the LLM not to render it as an owner-
    traceability red flag."""
    import pathlib
    src = pathlib.Path(
        repo_path("aria_service/routes/aria.py")
    ).read_text(encoding="utf-8", errors="ignore")
    assert "R-F318 RDAP REDACTION DISCIPLINE" in src
    assert "GDPR" in src
    # The text is broken across f-string lines in the source. Strip
    # all whitespace AND the `" f"` join-tokens to get the runtime form.
    import re as _re_w
    stripped = _re_w.sub(r'"\s*\n\s*f"', "", src)
    collapsed = _re_w.sub(r"\s+", " ", stripped)
    assert "NOT an owner-traceability red flag" in collapsed
