"""R-F3352 — /ecosystem/coverage must declare which services it does NOT scan.

The map declares three services (aria-intel, aria-web, aria-wa) but scan_modules()
globs `aria_service/**/*.py` only, and every organ in _ORGANS is hardcoded to
aria-intel. So aria-web and aria-wa are T0 cards with ZERO modules beneath them —
and that is the tier carrying auth, billing, Stripe, the UI and the WhatsApp limb
(server.mjs alone is ~8.5k lines, plus ~118 lib/*.mjs).

Meanwhile the coverage payload asserted `pct_mapped: 100.0` and the dashboard
tooltip read "100% by construction: node set == filesystem". Both are true of the
scanned tree and both read as the ecosystem. CLAUDE.md §21b is explicit that
observability is not Python-only.

The corroborating evidence that this is a real blind spot rather than a wording
quibble: the Delivery & Surfaces organ declares the keywords "whatsapp",
"telegram", "notify", "briefing" and "proprioception", and every one of them
matches ZERO Python modules — because those limbs live in the Node tier.

This is DERIVED from the organ table, not hardcoded, so adding Node organs later
retires the warning automatically instead of leaving a stale claim behind.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import ecosystem_map as em


def test_rf3352_coverage_names_the_services_it_cannot_see():
    cov = asyncio.run(em.get_coverage())
    svc = cov.get("services")
    assert svc, "coverage must report service scope"

    assert set(svc["declared"]) == set(em._SERVICES), "declared services must be the full T0 set"
    assert svc["mapped"] == ["aria-intel"], (
        f"every organ is currently aria-intel; got mapped={svc['mapped']}"
    )
    assert set(svc["unmapped"]) == {"aria-web", "aria-wa"}, (
        f"the Node tiers must be declared unmapped, got {svc['unmapped']}"
    )
    assert "not represented" in svc["note"].lower() or "no module nodes" in svc["note"].lower()


def test_rf3352_unmapped_is_derived_from_the_organ_table_not_hardcoded():
    """If a Node organ is ever added, the warning must retire itself. Proven by
    injecting one rather than by reading the source."""
    original = list(em._ORGANS)
    try:
        em._ORGANS.append(("node_web", "Web Tier", "aria-web", ("__never_matches__",)))
        cov = asyncio.run(em.get_coverage())
        assert "aria-web" in cov["services"]["mapped"], "a mapped service must leave the unmapped list"
        assert "aria-web" not in cov["services"]["unmapped"]
        assert "aria-wa" in cov["services"]["unmapped"], "the still-unmapped tier must remain declared"
    finally:
        em._ORGANS[:] = original
    # and the real payload is restored
    assert set(asyncio.run(em.get_coverage())["services"]["unmapped"]) == {"aria-web", "aria-wa"}


def test_rf3352_delivery_keywords_prove_the_node_tier_is_missing():
    """Not a wording quibble: the Delivery organ's own keywords for ARIA's output
    limbs match nothing, because those limbs are Node."""
    ids = [em._module_id(p).lower() for p in em.scan_modules()]
    for keyword in ("whatsapp", "telegram", "proprioception"):
        hits = [m for m in ids if em._kw_matches(keyword, m)]
        assert hits == [], (
            f"'{keyword}' now matches Python modules {hits} — if the Node tier has been "
            f"mapped, update this test and the R-F3352 warning together"
        )
    dead = {d["keyword"] for d in em.audit_organ_table()["dead_keywords"] if d["organ"] == "delivery"}
    assert {"whatsapp", "telegram", "proprioception"} <= dead, (
        "the audit must report these as dead keywords rather than hiding them"
    )
