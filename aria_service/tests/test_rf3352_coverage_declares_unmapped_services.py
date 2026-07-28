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
    """R-F3358 UPDATE — the gap this test was written to expose is now CLOSED, so
    it asserts the PROPERTY it always meant rather than the 2026-07-28 snapshot
    ("unmapped == {aria-web, aria-wa}"). Pinning the snapshot would have made this
    test fail for the good reason and pushed a future reader to delete it."""
    cov = asyncio.run(em.get_coverage())
    svc = cov.get("services")
    assert svc, "coverage must report service scope"
    assert set(svc["declared"]) == set(em._SERVICES), "declared services must be the full T0 set"

    # unmapped must be EXACTLY the declared services no organ table claims —
    # neither over- nor under-stated.
    organ_services = ({s for _o, _l, s, _k in em._ORGANS}
                      | {s for _o, _l, s, _k in em._NODE_ORGANS})
    assert set(svc["mapped"]) == organ_services & set(em._SERVICES)
    assert set(svc["unmapped"]) == set(em._SERVICES) - organ_services
    assert "not in the counts" in svc["note"].lower() or "no module nodes" in svc["note"].lower()


def test_rf3352_every_declared_service_actually_carries_modules():
    """A service can be 'mapped' by an organ table that matches nothing — which
    would restore the original lie in a new shape. Require real modules."""
    full = asyncio.run(em.build_structure())
    per_service: dict[str, int] = {}
    for n in full["nodes"]:
        if n["type"] == "module":
            per_service[n.get("tier_service") or "?"] = per_service.get(n.get("tier_service") or "?", 0) + 1
    for service in em._SERVICES:
        assert per_service.get(service, 0) > 0, (
            f"{service} is declared and 'mapped' but has ZERO module nodes — "
            f"an empty organ table is not coverage"
        )


def test_rf3352_unmapped_is_derived_from_the_organ_table_not_hardcoded():
    """If a Node organ is ever added, the warning must retire itself. Proven by
    injecting one rather than by reading the source."""
    # R-F3358 UPDATE: with both tiers mapped there is no live gap left to observe,
    # so the derivation is proven by REMOVING a service's organs and watching the
    # warning come back — the same property, driven from the other direction.
    original_node = list(em._NODE_ORGANS)
    try:
        em._NODE_ORGANS[:] = [o for o in original_node if o[2] != "aria-wa"]
        cov = asyncio.run(em.get_coverage())
        assert "aria-wa" in cov["services"]["unmapped"], (
            "dropping a service's organs must re-raise the unmapped warning — if it "
            "does not, the list is hardcoded rather than derived"
        )
        assert "aria-wa" not in cov["services"]["mapped"]
        assert "aria-web" in cov["services"]["mapped"], "unrelated services must be unaffected"
    finally:
        em._NODE_ORGANS[:] = original_node
    assert asyncio.run(em.get_coverage())["services"]["unmapped"] == [], "state not restored"


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
