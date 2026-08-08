"""R-F2365 + R-F2366 — DD coverage honesty (from the Modirum Gespi review).

R-F2365: the UBO-walk coverage-gap said "no officer-registry adapter for
jurisdiction=BR; ARIA covers GB via Companies House today" — FALSE. The registry
adapter supports 24 jurisdictions incl. BR; BR is merely CNPJ-gated. Now the gap
states the accurate reason (supported-but-ID-gated vs genuinely-no-adapter).

R-F2366: press relevance was by passing MENTION (token anywhere in
title+snippet+url), so a RocketReach contact page whose bio mentioned the entity
counted as "press coverage" (the "Nordic Nano" hit). Now relevance is aboutness
(token in title OR url), and the structured view leads with verified-vs-unverified
instead of a raw "14 press items" count.

Drives the REAL functions (walk_ubo_chain / _press_hit_is_relevant /
structured_view) per CLAUDE.md §3c.
"""
from __future__ import annotations

import json
import pytest

from aria_service.intel.dd_orchestrator import (
    _press_hit_is_relevant, _entity_distinctive_tokens,
)
from aria_service.intel.dd_schema import structured_view

# R-F3786/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


# ─────────────────────────── R-F2365 UBO-walk honesty ──────────────────────────

@pytest.mark.asyncio
async def test_rf2365_br_ubo_gap_is_honest_not_gb_only(monkeypatch):
    """A BR entity with no officers found must NOT claim 'ARIA covers GB only' —
    BR is supported but ID-gated (needs a CNPJ)."""
    import aria_service.intel.network_walker as nw

    async def _empty(*a, **k):
        return ([], "", "")
    monkeypatch.setattr(nw, "_fetch_officers_with_provenance", _empty)

    out = await nw.walk_ubo_chain(
        "Modirum Gespi", jurisdiction_iso2="BR",
        sanctions_screen_every_node=False, max_hops=1,
    )
    gaps = out.get("coverage_gaps") or []
    assert gaps, "expected a coverage gap when no officers found"
    reason = " ".join(g.get("reason", "") for g in gaps)
    assert "covers GB via Companies House today" not in reason, (
        f"R-F2365: false GB-only claim still present: {reason}"
    )
    assert "BR" in reason, f"gap should name the jurisdiction: {reason}"
    assert ("CNPJ" in reason or "registration number" in reason or "ID-gated" in reason), (
        f"R-F2365: should surface the ID-gate unlock (CNPJ): {reason}"
    )


@pytest.mark.asyncio
async def test_rf2365_unsupported_jurisdiction_still_honest(monkeypatch):
    """A genuinely-unsupported jurisdiction says so — without the GB-only lie."""
    import aria_service.intel.network_walker as nw

    async def _empty(*a, **k):
        return ([], "", "")
    monkeypatch.setattr(nw, "_fetch_officers_with_provenance", _empty)

    out = await nw.walk_ubo_chain(
        "Some Co", jurisdiction_iso2="ZZ",  # not in _SUPPORTED_JURISDICTIONS
        sanctions_screen_every_node=False, max_hops=1,
    )
    reason = " ".join(g.get("reason", "") for g in (out.get("coverage_gaps") or []))
    assert "no registry coverage" in reason
    assert "covers GB via Companies House today" not in reason


# ─────────────────────────── R-F2366 press relevance/count ──────────────────────

def test_rf2366_snippet_only_mention_is_dropped():
    """Aboutness: a token only in the SNIPPET (passing mention) is no longer
    treated as coverage — this is the 'Nordic Nano' class of noise."""
    toks = _entity_distinctive_tokens("Modirum Gespi")
    assert not _press_hit_is_relevant(
        "Mattipekka Kronqvist — Nordic Nano Group",   # title: not about the entity
        "bio mentions the modirum gespi ecosystem",   # only a snippet mention
        "https://rocketreach.co/mattipekka",          # host: unrelated
        toks,
    ), "snippet-only mention should be dropped (aboutness)"


def test_rf2366_title_or_host_mention_is_kept():
    """A real reference — token in the TITLE or the URL slug — is kept."""
    toks = _entity_distinctive_tokens("Modirum Gespi")
    assert _press_hit_is_relevant("Modirum acquires Gespi", "", "https://x/news", toks)
    assert _press_hit_is_relevant("Defence roundup 2025", "", "https://di.eu/modirum-gespi-deal", toks)


def test_rf2366_structured_view_leads_with_verified_unverified():
    """The digital section shows 'N verified / M unverified', not a raw count."""
    r = {
        "digital": {
            "press_coverage": [{"url": f"https://x/{i}"} for i in range(14)],
            "source_tier_breakdown": {"UNVERIFIED": 12, "T2": 2},
        },
    }
    sv = structured_view(r)
    txt = json.dumps(sv)
    assert "2 verified / 12 unverified" in txt, f"press split not surfaced: {txt[:400]}"
    assert '"Press items"' not in txt, "the raw-count 'Press items' label should be gone"


def _press_metric(tb: dict) -> str:
    n = sum(tb.values())
    sv = structured_view({"digital": {
        "press_coverage": [{"url": f"u{i}"} for i in range(n)],
        "source_tier_breakdown": tb,
    }})
    dig = [s for s in sv["sections"] if s.get("key") == "digital"][0]
    return next(h["value"] for h in dig["highlights"] if h["label"] == "Press coverage")


def test_rf2366_memory_only_not_counted_as_verified():
    """BLOCKER guard (Pass-2): a RAG/memory-only DD (live web = 0) must NOT read
    as 'verified' coverage — that was the honesty failure worse than the raw
    count. MEMORY_ONLY shows as 'from memory', 0 verified."""
    v = _press_metric({"MEMORY_ONLY": 5})
    assert v.startswith("0 verified"), v
    assert "from memory" in v, v


def test_rf2366_entity_site_not_counted_as_verified():
    """A DD backed only by the entity's OWN website is self-reported, not
    independently verified."""
    v = _press_metric({"ENTITY_SITE": 3})
    assert v.startswith("0 verified"), v
    assert "own-site" in v, v


@pytest.mark.asyncio
async def test_rf2365_gb_no_officers_names_companies_house(monkeypatch):
    """BLOCKER guard (Pass-2): GB is NOT in _SUPPORTED_JURISDICTIONS (its coverage
    is a separate Companies House path). A GB entity with no officers must NOT get
    the false 'no registry coverage for GB' — it must name Companies House."""
    import aria_service.intel.network_walker as nw

    async def _empty(*a, **k):
        return ([], "", "")
    monkeypatch.setattr(nw, "_fetch_officers_with_provenance", _empty)

    out = await nw.walk_ubo_chain(
        "SomeCo Ltd", jurisdiction_iso2="GB", registration_number="12345678",
        sanctions_screen_every_node=False, max_hops=1,
    )
    reason = " ".join(g.get("reason", "") for g in (out.get("coverage_gaps") or []))
    assert "Companies House" in reason, reason
    assert "no registry coverage for jurisdiction=GB" not in reason, (
        f"R-F2365: false 'no coverage for GB' claim: {reason}"
    )


def test_rf2365_identity_registry_gap_not_gb_only():
    """The identity-layer registry-gap text no longer falsely says 'GB only'."""
    import aria_service.intel.dd_orchestrator as ddo
    import inspect
    src = module_source(ddo)
    assert "Companies House coverage for GB only" not in src, (
        "R-F2365: the false 'Companies House coverage for GB only' data_gap survives"
    )
