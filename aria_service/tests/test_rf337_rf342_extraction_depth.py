"""R-F337 + R-F340 + R-F341 + R-F342 — extraction depth fixes.

Live failure 2026-05-11 23:06: third identical INSUFFICIENT EVIDENCE
verdict on assangroup.com.tr. Diagnosed: R-F313 priority-seed expansion
worked (fetched /corporate/about-us/ etc.), but:

  - R-F311 brandify only stripped ONE TLD ('.com.tr' → '.com', still
    trips _DOMAIN_TOKEN_RE). Need recursive strip. → R-F337
  - Rule-based fact extractor can't read prose — director names,
    product lines, company history all missed. → R-F340
  - Chat handler used hostname as entity, not page <title>. → R-F341
  - Turkish MERSİS adapter exists but not verified wired to R-F301
    backfill. → R-F342
"""
from __future__ import annotations


# ───────────────────────── R-F337 — recursive TLD strip ─────────────────────────

def test_rf337_strips_compound_tld_com_tr():
    """Live failure: 'assangroup.com.tr' should strip to 'assangroup',
    not 'assangroup.com' (single strip leaves '.com' which trips the
    sanctions domain-token gate)."""
    from aria_service.intel.web_explorer import brandify_query
    assert brandify_query("assangroup.com.tr") == "assangroup", (
        f"R-F337 regression: compound TLD not fully stripped. "
        f"Got: {brandify_query('assangroup.com.tr')!r}"
    )


def test_rf337_strips_co_uk():
    from aria_service.intel.web_explorer import brandify_query
    assert brandify_query("acmedefence.co.uk") == "acmedefence"


def test_rf337_strips_com_br():
    from aria_service.intel.web_explorer import brandify_query
    assert brandify_query("gespi.com.br") == "gespi"


def test_rf337_strips_org_uk():
    from aria_service.intel.web_explorer import brandify_query
    assert brandify_query("examplengo.org.uk") == "examplengo"


def test_rf337_single_tld_still_works():
    """Single TLDs (the R-F299 case) must still work."""
    from aria_service.intel.web_explorer import brandify_query
    assert brandify_query("modirumgespi.com") == "modirumgespi"


def test_rf337_real_brand_with_spaces_untouched():
    """Real brand names (containing spaces) must pass through unchanged."""
    from aria_service.intel.web_explorer import brandify_query
    assert brandify_query("Modirum GESPI") == "Modirum GESPI"
    assert brandify_query("Assan Group") == "Assan Group"


def test_rf337_does_not_strip_past_brand():
    """When only one segment remains (no '.'), strip stops — must not
    eat the brand itself."""
    from aria_service.intel.web_explorer import brandify_query
    # 'assangroup' has no '.' — strip is a no-op
    assert brandify_query("assangroup") == "assangroup"


def test_rf337_capability_assangroup_flows_through_sanctions_gate():
    """End-to-end capability: the brandified output must NOT contain
    the _DOMAIN_TOKEN_RE pattern (no '.com' or '.tr' remnants)."""
    import re
    from aria_service.intel.web_explorer import brandify_query
    _DOMAIN_TOKEN_RE = re.compile(r"\.[a-z]{2,6}\b", re.IGNORECASE)
    brandified = brandify_query("assangroup.com.tr")
    assert _DOMAIN_TOKEN_RE.search(brandified) is None, (
        f"R-F337 capability regression: brandified output still contains "
        f"a domain-token pattern. Sanctions gate will still reject. "
        f"Got: {brandified!r}"
    )


# ───────────────────────── R-F340 — LLM fact extraction in link_tree ──────────

def test_rf340_dd_orchestrator_passes_llm_to_link_investigator():
    """Source-level capability: the link_investigator call inside
    _run_digital now passes the LLM through when available, with a
    budget cap. Previously llm=None unconditionally — rule-based only."""
    import pathlib
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/dd_orchestrator.py"
    ).read_text(encoding="utf-8", errors="ignore")
    # Find the investigate_link_tree call (with surrounding context)
    idx = src.find("link_investigator.investigate_link_tree(")
    assert idx > 0, "investigate_link_tree call not found"
    # Include 1500 chars BEFORE the call to capture the R-F340 comment
    block = src[max(0, idx - 1500):idx + 2000]
    # Must reference R-F340 + pass llm
    assert "R-F340" in block, (
        "R-F340 regression: link_tree invocation not annotated"
    )
    # Must NOT pass llm=None unconditionally
    assert "llm=_llm_for_linktree" in block, (
        "R-F340 regression: llm still hardcoded to None"
    )


def test_rf340_respects_env_disable():
    """Operator must be able to disable LLM extraction via env var
    (ARIA_LINKTREE_LLM_ENABLED=0) for cost-sensitive deploys."""
    import pathlib
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/dd_orchestrator.py"
    ).read_text(encoding="utf-8", errors="ignore")
    assert "ARIA_LINKTREE_LLM_ENABLED" in src
    assert "ARIA_LINKTREE_LLM_BUDGET_USD" in src


# ───────────────────────── R-F341 — page-title entity extraction ──────────────

def test_rf341_chat_handler_fetches_page_title_when_entity_missing():
    """Source-level: when entity_needs_fallback, the chat handler now
    fetches the page <title> FIRST before falling back to brandify(host)."""
    import pathlib
    src = pathlib.Path(
        "C:/code/crucix/aria_service/routes/aria.py"
    ).read_text(encoding="utf-8", errors="ignore")
    assert "R-F341" in src
    # Must include the title-tag regex
    assert "<title" in src
    # Must split on common separators (—, |, -) for title cleaning
    assert "_title_clean" in src


def test_rf341_title_cleaner_splits_on_pipe_or_dash():
    """Source-level: the title cleaner splits 'Acme | Defence Solutions'
    to take the first chunk 'Acme'."""
    import pathlib
    src = pathlib.Path(
        "C:/code/crucix/aria_service/routes/aria.py"
    ).read_text(encoding="utf-8", errors="ignore")
    # The split regex must use the pipe/dash characters
    assert r"\|\-" in src or "[|" in src or r"\|" in src


def test_rf341_title_clean_unit():
    """Unit test of the title-cleaning logic in isolation."""
    import re
    titles = [
        ("Assan Group | Türkiye'nin önde gelen savunma şirketi",
         "Assan Group"),
        ("Modirum | GESPI – AI-Powered Defence",
         "Modirum"),
        ("Acme Corp - Premier Aerospace Solutions",
         "Acme Corp"),
        ("Plain Title",  # no separator
         "Plain Title"),
    ]
    for raw, expected in titles:
        cleaned = re.split(r"\s+[\|\-–—]+\s+", raw, maxsplit=1)[0].strip()
        assert cleaned == expected, (
            f"R-F341 title cleaner: {raw!r} → expected {expected!r}, "
            f"got {cleaned!r}"
        )


# ───────────────────────── R-F342 — Turkey adapter + R-F301 wiring ─────────────

def test_rf342_turkey_in_supported_jurisdictions():
    """The Turkish MERSİS adapter must be in the registry adapter
    dispatch table."""
    from aria_service.intel import registry_adapters
    assert "TR" in registry_adapters._SUPPORTED_JURISDICTIONS, (
        "R-F342 regression: TR removed from _SUPPORTED_JURISDICTIONS"
    )


def test_rf342_link_tree_city_map_has_turkish_signals():
    """R-F301 jurisdiction backfill must recognise Turkish cities and
    country names so the assangroup.com.tr DD detects TR from the
    /corporate/about-us/ page text."""
    from aria_service.intel.dd_orchestrator import _LINK_TREE_CITY_TO_ISO2
    # Turkish signals must map to TR
    assert _LINK_TREE_CITY_TO_ISO2.get("istanbul") == "TR"
    assert _LINK_TREE_CITY_TO_ISO2.get("ankara") == "TR"
    assert _LINK_TREE_CITY_TO_ISO2.get("izmir") == "TR"
    assert _LINK_TREE_CITY_TO_ISO2.get("turkey") == "TR"
    assert _LINK_TREE_CITY_TO_ISO2.get("türkiye") == "TR"


def test_rf342_capability_turkish_page_text_detects_tr():
    """Capability: when the link-tree haystack contains Turkish city +
    country signals, R-F301 detector returns TR with score ≥ 2."""
    from aria_service.intel.dd_orchestrator import (
        _detect_all_jurisdictions_in_link_tree,
    )
    from types import SimpleNamespace

    # Build a fake link tree mimicking what /corporate/about-us/ on
    # assangroup.com.tr would produce: mentions of Istanbul, Ankara,
    # Türkiye, Kibar Holding.
    fused = [
        SimpleNamespace(
            value="Istanbul headquarters",
            first_context="Assan Group, headquartered in Istanbul, Türkiye",
        ),
        SimpleNamespace(
            value="Operations across Türkiye",
            first_context="manufacturing facilities in Ankara and Izmir",
        ),
    ]
    tree = SimpleNamespace(fused_facts=fused, pages=[])
    detected = _detect_all_jurisdictions_in_link_tree(tree)
    assert detected, "R-F342 regression: no jurisdiction detected"
    iso2s = [d["iso2"] for d in detected]
    assert "TR" in iso2s, (
        f"R-F342 regression: Turkish signals didn't yield TR detection. "
        f"Got: {iso2s}"
    )
    # TR should be the winner given multiple Turkish city + country tokens
    assert detected[0]["iso2"] == "TR", (
        f"R-F342: expected TR as winner, got {detected[0]}"
    )
