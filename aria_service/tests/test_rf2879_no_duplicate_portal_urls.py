"""R-F2879 — no two portal_registry.PORTALS entries may share a URL.

The vault (agent_signup_vault) dedupes on site_id only, and import_open_portals calls
vault.record() directly (bypassing the URL-based dedup on the POST route). So two
PortalDefs with different ids but the SAME url produce two identical vault rows —
which is exactly why "OpenCorporates" showed twice on vault.html (id="opencorporates"
AND id="open_corporates", both https://opencorporates.com). This ratchet keeps the
source-of-truth free of URL duplicates so the vault can't re-acquire the dup.
"""
from collections import Counter

from aria_service.intel.portal_registry import PORTALS


def _norm(u: str) -> str:
    u = (u or "").strip().lower().rstrip("/")
    for p in ("https://", "http://"):
        if u.startswith(p):
            u = u[len(p):]
    if u.startswith("www."):
        u = u[4:]
    return u


# Known, reviewed URL dup NOT collapsed by R-F2879 because it is entangled: the two
# Companies House ids are referenced by DIFFERENT subsystems (`companies_house` by
# registry_coverage's GB mapping; `uk_companies_house` by defence_source_seed +
# portal_scheduler), so collapsing needs a coordinated source-id migration that could
# orphan live state — a separate change, not a same-URL delete. Tracked, not ignored:
# any NEW dup still fails this ratchet.
_KNOWN_DUP_URLS = {"find-and-update.company-information.service.gov.uk"}


def test_rf2879_no_new_duplicate_portal_urls():
    urls = Counter(_norm(p.url) for p in PORTALS if getattr(p, "url", None))
    dups = {u: c for u, c in urls.items() if c > 1 and u not in _KNOWN_DUP_URLS}
    assert not dups, (
        f"NEW duplicate portal URLs would create duplicate vault rows: {dups} — "
        "collapse them to a single PortalDef (see R-F2879)"
    )


def test_rf2879_opencorporates_is_single():
    """The operator-reported case: OpenCorporates must appear exactly once."""
    n = sum(1 for p in PORTALS if _norm(getattr(p, "url", "")) == "opencorporates.com")
    assert n == 1, f"OpenCorporates must be a single PortalDef, found {n}"
