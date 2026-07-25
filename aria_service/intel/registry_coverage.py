"""ARIA registry coverage vault — what we can look up, and whether it is LIVE.

R-F2863.

WHY
───
`capability_manifest` answers "which jurisdictions have an adapter". It cannot
answer "is that adapter actually working right now". A source that is REGISTERED
but DEAD looks identical to one that works — and it reaches a customer as a
confident empty result rather than an honest data gap. That is a false clean
about our own capability, which is the one kind this platform cannot afford.

THE HONESTY RULE
────────────────
Liveness is TRI-STATE and defaults to UNPROVEN:

    live is True   -> we OBSERVED a successful lookup (timestamp is the evidence)
    live is False  -> we OBSERVED failures with no intervening success
    live is None   -> never observed. NOT "probably fine".

Being configured is not evidence of working. Nothing in this module upgrades a
source to live without an observation.

WHY OBSERVED, NOT PINGED
────────────────────────
Liveness is recorded from REAL `lookup_entity` calls rather than a synthetic
ping. A synthetic probe measures whether a health URL answers; this measures
whether the thing customers actually depend on returned data. It also costs
nothing extra — the call already happened.

An EMPTY result is deliberately neither: a registry that correctly answers "no
such company" is WORKING, so counting it as a failure would suspend a healthy
source — but it did not prove liveness either, so it must not mark it live.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.registry_coverage")

# Durable, no TTL — §7: knowledge does not expire.
_KEY = "crucix:aria:registry:coverage"

# Jurisdictions served OUTSIDE the adapter dispatch table, so they are not
# reported as uncovered. GB has a dedicated Companies House branch in
# dd_orchestrator._run_identity rather than an entry in registry_adapters.
_COVERED_ELSEWHERE = {"GB": "companies_house"}

_VALID_OUTCOMES = ("success", "error", "empty")

# ── R-F2929 — WHY, in machine-readable form ──────────────────────────────────
# The tri-state answers "is it live". It cannot answer "why not", and those reasons
# are not interchangeable: Angola has NO public registry API at all, Germany's source
# has DISAPPEARED, India BLOCKS automated access, and Hungary answers but the scrape
# no longer matches. Rendering all four as a flat "unproven" tells a reader nothing
# and quietly implies "we just haven't got round to it" for cases where no amount of
# work by us would change the answer.
#
# `caveat` is the one that matters for the USP. CZ and SK ARE live — a real registry
# answered — but they return the "IČO" LABEL where the company name belongs. A green
# row with no qualifier would be a false clean on the product surface: the page would
# vouch for data that is wrong. Liveness and correctness are different claims and the
# surface must not blur them.
#
# Every entry is dated and comes from a probe recorded in the triage block above.
_NOTE_CLASSES = {
    "stub_no_registry_api": "No public registry API exists — ARIA reads nothing; manual verification required.",
    "source_gone": "The source has disappeared — no code change can restore it.",
    "source_blocks_automation": "The registry blocks automated access — it needs credentials, refuses non-browser clients, or blocks our IP.",
    "reachable_unparsed": "Source responds but exposes no machine-readable API — extracting data would need a fragile scrape (not done).",
}

_ADAPTER_NOTES: dict[str, dict] = {
    # No registry to read. These will never go live without a paid provider or a
    # manual process — an operator decision (§6/§17), not a defect to fix.
    **{c: {"class": "stub_no_registry_api", "probed_at": "2026-07-23"}
       for c in ("AO", "BG", "GH", "KE", "PA", "SA", "US", "ZA")},
    # Sources confirmed gone from TWO independent networks (this workstation and the
    # fly datacenter) so a single network's failure could not be mistaken for a dead source.
    "DE": {"class": "source_gone", "probed_at": "2026-07-23",
           "detail": "api.offeneregister.de no longer resolves (DNS)."},
    "AE": {"class": "source_gone", "probed_at": "2026-07-23",
           "detail": "difc.ae public-register API returns 404; only ever covered the DIFC free zone."},
    # RO reclassified 2026-07-23: NOT gone. ANAF resets the connection from our
    # datacenter IP on every attempt, and the v8 endpoint 404s. It is the only free
    # official RO source; third-party providers (listafirme/termene) are §6-declined.
    "RO": {"class": "source_blocks_automation", "probed_at": "2026-07-23",
           "detail": "ANAF blocks our datacenter IP (connection reset); v8 endpoint also 404s. "
                     "Only free official source; paid/third-party declined (§6)."},
    "IN": {"class": "source_blocks_automation", "probed_at": "2026-07-23",
           "detail": "mca.gov.in returns 403 to a normal client."},
    "NG": {"class": "source_blocks_automation", "probed_at": "2026-07-23",
           "detail": "search.cac.gov.ng returns 403 to a normal client."},
    # TR reclassified 2026-07-23: NOT a parse gap. The MERSIS REST API 302-redirects
    # every call to a login portal — it requires authentication, no free public access.
    "TR": {"class": "source_blocks_automation", "probed_at": "2026-07-23",
           "detail": "MERSIS REST (mersis.ticaret.gov.tr) requires authentication — "
                     "redirects to a login portal. No free public API."},
    # HU probed 2026-07-23: the registry (e-cegjegyzek / e-beszamolo) serves HTML only;
    # no free JSON API found. A scrape would drift and fabricate — declined (cf. R-F2939).
    "HU": {"class": "reachable_unparsed", "probed_at": "2026-07-23",
           "detail": "e-cegjegyzek / e-beszamolo serve HTML only; no free public JSON API."},
    "GI": {"class": "reachable_unparsed", "probed_at": "2026-07-23",
           "detail": "companieshouse.gi responds 200 (HTML); no machine-readable API."},
}

# Defects in adapters that ARE live. Shown as a caveat ON the live row, never as a
# downgrade — the registry genuinely answered, and pretending otherwise would be its
# own inaccuracy.
# R-F2939 — the CZ and SK "IČO" caveats are REMOVED: both adapters were migrated from
# drifted HTML scrapes to the official JSON APIs (CZ ARES, SK RPO), which return the
# real company name, number, address and date (CZ also officers). Verified live:
# CZ -> "Škoda Auto a.s." / 00177041 / 7 officers; SK -> "SLOVNAFT, a.s." / 31322832.
# A caveat that no longer applies would itself be a false signal on the page.
_ADAPTER_CAVEATS: dict[str, str] = {}

# ── R-F2911 — ADAPTER HEALTH TRIAGE, 2026-07-23 ──────────────────────────────
# The exploration ledger below records why a jurisdiction has NO adapter. This
# records what is wrong with the adapters we DO have, for the same reason: the
# answers cost real probing and would otherwise be re-derived from scratch.
#
# Every verdict here comes from a probe run in the FLY DATACENTER (clean egress).
# Where a result could have been this workstation's network or antivirus, it was
# re-checked from both and only recorded when they agreed. Nothing here is inferred
# from an adapter returning None — that alone proves nothing.
#
# LIVE (national registry genuinely answered, verified by a real match):
#   CH CZ EE FI FR NO PL   name lookup
#   BR SK                  IDENTIFIER-based: they return None without a CNPJ / IČO.
#                          A name-only probe made them look dead; with the identifier
#                          BR returns brazil_cnpj/33.000.167/0001-01 and SK returns
#                          slovakia_orsr/31322832.
#   GB                     Companies House, covered OUTSIDE this dispatch table
#                          (_COVERED_ELSEWHERE); proven live 01470151, 13 officers.
#
# SOURCE IS GONE — no code change fixes these:
#   DE  api.offeneregister.de does NOT RESOLVE (DNS). Confirmed from two independent
#       networks. The open-data service has disappeared, not merely changed shape.
#   AE  difc.ae/api/public-register/search -> HTTP 404. Also note the adapter only
#       ever covered the DIFC free zone, not the wider UAE.
#
# SOURCE BLOCKS AUTOMATED ACCESS (needs credentials, or blocks our IP):
#   IN  mca.gov.in -> 403.    NG  search.cac.gov.ng -> 403.
#   RO  ANAF resets the connection from our datacenter IP on every attempt, and the
#       v8 endpoint 404s. Only free official RO source; third-party declined (§6).
#       (Reclassified from "source gone" 2026-07-23 — it is blocking us, not gone.)
#   TR  MERSIS REST 302-redirects every call to a login portal — requires
#       authentication, no free public access. (Reclassified from "reachable_unparsed".)
#
# SOURCE REACHABLE (HTTP 200) BUT NO MACHINE-READABLE API — HTML only. Extracting data
# would need a fragile scrape (which drifts and fabricates — declined, cf. R-F2939):
#   HU  e-cegjegyzek / e-beszamolo (HTML only)   GI  companieshouse.gi (HTML only)
#
# CZ / SK — the "IČO"-as-name data-quality defect is FIXED (R-F2939): both were
# migrated from drifted HTML scrapes to the official JSON APIs (CZ ARES, SK RPO). They
# now return the real name/number/address/date (CZ also officers), verified live —
# CZ "Škoda Auto a.s." / 00177041, SK "SLOVNAFT, a.s." / 31322832 — so they are LIVE
# and CORRECT, with no caveat.
# ─────────────────────────────────────────────────────────────────────────────

# ── R-F2866 — exploration ledger ─────────────────────────────────────────────
# Why a jurisdiction is NOT covered, from an actual probe. Before this, uncovered
# was a bare list, so "can we add Ireland?" was re-researched from scratch every
# time and the answer lived in a commit message. Recording the probe makes the
# next decision cheap and stops us re-probing a source we already ruled out.
#
# The default is UNPROBED, never "unavailable". "We have not looked" and "we
# looked and it is not possible" are different claims, and only one of them is
# evidence. Anything absent from this table reports as unprobed.
#
# probed_at is the date the endpoint was actually exercised — a verdict with no
# date is an opinion, and these go stale as registries open up.
_EXPLORATION: dict[str, dict] = {
    # Ruled out — needs credentials an operator must obtain (§21e).
    "IE": {"status": "credentials_required", "probed_at": "2026-07-22",
           "endpoint": "https://services.cro.ie/cws/companies",
           "detail": "HTTP 401 — CRO requires registered API credentials."},
    "AU": {"status": "credentials_required", "probed_at": "2026-07-22",
           "endpoint": "https://abr.business.gov.au/json/MatchingNames.aspx",
           "detail": "ABR responds but requires a free registered GUID."},
    "DK": {"status": "third_party_only", "probed_at": "2026-07-22",
           "endpoint": "https://cvrapi.dk/api",
           "detail": "Works, but is a THIRD-PARTY proxy — §6 puts the burden of "
                     "proof on it; the official Virk feed needs credentials."},
    # Ruled out — data exists but there is no per-entity lookup API.
    "LV": {"status": "bulk_only", "probed_at": "2026-07-22",
           "endpoint": "https://data.gov.lv/dati/lv/api/3/action/package_search",
           "detail": "Dataset catalogue only — would need a bulk import, not a lookup."},
    "UA": {"status": "bulk_only", "probed_at": "2026-07-22",
           "endpoint": "https://data.gov.ua/api/3/action/package_search",
           "detail": "Dataset catalogue only — would need a bulk import, not a lookup."},
    # Ruled out — no machine-readable endpoint found.
    "SI": {"status": "no_api", "probed_at": "2026-07-22",
           "endpoint": "https://www.ajpes.si/prs/", "detail": "HTML search form only."},
    "BE": {"status": "no_api", "probed_at": "2026-07-22",
           "endpoint": "https://kbopub.economie.fgov.be", "detail": "HTML search form only."},
    "CA": {"status": "no_api", "probed_at": "2026-07-22",
           "endpoint": "https://ised-isde.canada.ca/cc/lgcy/api",
           "detail": "Returns the HTML portal, not JSON."},
    "GR": {"status": "no_api", "probed_at": "2026-07-22",
           "endpoint": "https://publicity.businessportal.gr", "detail": "HTTP 404."},
    "CO": {"status": "no_api", "probed_at": "2026-07-22",
           "endpoint": "https://ruesapi.rues.org.co", "detail": "HTTP 404."},
    "SG": {"status": "no_api", "probed_at": "2026-07-22",
           "endpoint": "https://data.gov.sg/api", "detail": "HTTP 404 / 403."},
    "HR": {"status": "no_api", "probed_at": "2026-07-22",
           "endpoint": "https://sudreg-api.pravosudje.hr", "detail": "Unreachable; registration required."},
    "NZ": {"status": "credentials_required", "probed_at": "2026-07-22",
           "endpoint": "https://api.business.govt.nz", "detail": "HTTP 404 without a subscription key."},
    "JP": {"status": "credentials_required", "probed_at": "2026-07-22",
           "endpoint": "https://api.houjin-bangou.nta.go.jp",
           "detail": "Requires a free application ID."},
}

# Statuses an operator could clear by obtaining a credential — the actionable set.
_OPERATOR_CLEARABLE = {"credentials_required"}

# Bound every store touch: this surface must stay answerable when the store is sick.
_READ_TIMEOUT_S = float(__import__("os").getenv("ARIA_REGISTRY_COVERAGE_TIMEOUT_S", "3") or 3)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _load() -> dict | None:
    """STRICT read. Returns None when the store could not be read.

    Deliberately strict: `get_json()` collapses a StoreReadError to None, the
    caller reads that as "empty", writes it back, and WIPES the durable history
    — the clobber class behind R-F2664, R-F2852 and R-F2854. Here the caller
    must be able to tell "no data yet" ({}) from "could not read" (None).
    """
    import asyncio

    from . import redis_store as rs
    try:
        # Hard-bounded. A wedged state_store must not hang a reader — this
        # surface is meant to be queryable exactly when things are going wrong,
        # and the event-loop starvation history makes an unbounded await here a
        # foot-gun. A timeout is indistinguishable from any other read failure:
        # both mean "could not read", which degrades to unproven, never to live.
        data = await asyncio.wait_for(rs.get_json_strict(_KEY), timeout=_READ_TIMEOUT_S)
    except Exception as exc:
        logger.warning("[registry_coverage] state read deferred, skipping write: %s", exc)
        return None
    return data if isinstance(data, dict) else {}


async def record_outcome(iso2: str, adapter: str, outcome: str) -> bool:
    """Record what a real registry lookup did. Returns True if persisted.

    Never raises — a bookkeeping failure must not break a DD run.
    """
    iso2 = (iso2 or "").upper().strip()
    if not iso2 or outcome not in _VALID_OUTCOMES:
        return False

    state = await _load()
    if state is None:
        return False        # transient read failure -> SKIP the write, never clobber

    entry = dict(state.get(iso2) or {})
    entry["adapter"] = adapter or entry.get("adapter") or ""
    entry["last_seen_at"] = _now_iso()
    if outcome == "success":
        entry["last_success_at"] = _now_iso()
        entry["consecutive_failures"] = 0
    elif outcome == "error":
        entry["last_failure_at"] = _now_iso()
        entry["consecutive_failures"] = int(entry.get("consecutive_failures") or 0) + 1
    else:                                   # "empty" — working, but proves nothing
        entry["last_empty_at"] = _now_iso()
        entry.setdefault("consecutive_failures", 0)
    entry["observations"] = int(entry.get("observations") or 0) + 1

    state[iso2] = entry
    try:
        import asyncio as _a
        from . import redis_store as rs
        await _a.wait_for(rs.set_json(_KEY, state), timeout=_READ_TIMEOUT_S)
        return True
    except Exception as exc:
        logger.warning("[registry_coverage] persist failed: %s", exc)
        return False


def _reason_for(iso2: str) -> dict | None:
    """R-F2929 — the recorded reason a jurisdiction is not live, or None.

    Returns the CLASS (so a UI can group or colour it) alongside human text and the
    probe date. A verdict with no date is an opinion, and these go stale as registries
    open up or move.
    """
    note = _ADAPTER_NOTES.get((iso2 or "").upper())
    if not note:
        return None
    cls = note.get("class", "")
    return {
        "class": cls,
        "summary": _NOTE_CLASSES.get(cls, ""),
        "detail": note.get("detail", ""),
        "probed_at": note.get("probed_at", ""),
    }

def _status_for(entry: dict) -> tuple[bool | None, str]:
    """Derive (live, status) from observations only. Never assumes."""
    if int(entry.get("consecutive_failures") or 0) > 0:
        return False, "failing"
    if entry.get("last_success_at"):
        return True, "live"
    return None, "unproven"


async def coverage() -> dict[str, Any]:
    """Full inventory: every jurisdiction, its adapter, and its observed liveness.

    Also reports what is NOT covered, so "what else can we explore" is answerable
    from data rather than from memory.
    """
    from . import registry_adapters as ra

    observed = await _load()
    if observed is None:
        observed = {}       # rendering a read failure as "no observations" is safe:
                            # it degrades to unproven, never to a false live claim

    jurisdictions: dict[str, dict] = {}
    for iso2, fn in sorted(ra._DISPATCH.items()):
        adapter_name = getattr(fn, "__name__", "").removeprefix("_lookup_")
        entry = dict(observed.get(iso2) or {})
        live, status = _status_for(entry)
        recorded_adapter = entry.get("adapter") or ""
        try:
            reg_status = ra.RegistryStatus.for_adapter(recorded_adapter).value \
                if recorded_adapter else None
        except Exception:
            reg_status = None
        jurisdictions[iso2] = {
            "adapter": recorded_adapter or adapter_name,
            "registry_status": reg_status,
            "live": live,
            "status": status,
            "observations": int(entry.get("observations") or 0),
            "consecutive_failures": int(entry.get("consecutive_failures") or 0),
            "last_success_at": entry.get("last_success_at"),
            "last_failure_at": entry.get("last_failure_at"),
            # R-F2929 — WHY it is not live, and any caveat on a row that IS live.
            # `reason` is only attached when the jurisdiction is not live: a live row
            # needs no excuse, and carrying a stale one would invite a reader to
            # discount evidence that has since been earned.
            **({"reason": _reason_for(iso2)} if live is not True and _reason_for(iso2) else {}),
            **({"caveat": _ADAPTER_CAVEATS[iso2]} if iso2 in _ADAPTER_CAVEATS else {}),
        }

    for iso2, adapter_name in _COVERED_ELSEWHERE.items():
        if iso2 not in jurisdictions:
            entry = dict(observed.get(iso2) or {})
            live, status = _status_for(entry)
            jurisdictions[iso2] = {
                "adapter": adapter_name, "registry_status": None,
                "live": live, "status": status,
                "observations": int(entry.get("observations") or 0),
                "consecutive_failures": int(entry.get("consecutive_failures") or 0),
                "last_success_at": entry.get("last_success_at"),
                "last_failure_at": entry.get("last_failure_at"),
            }

    manual_only = sorted(set(_hint_jurisdictions()) - set(jurisdictions))
    # R-F2866 — say WHY each one is uncovered, from a dated probe. Anything we
    # have not actually exercised reports as `unprobed`, never as unavailable.
    exploration = {
        iso2: dict(_EXPLORATION.get(iso2) or {"status": "unprobed",
                                              "probed_at": None,
                                              "endpoint": None,
                                              "detail": "no probe recorded"})
        for iso2 in manual_only
    }
    operator_clearable = sorted(
        iso2 for iso2, e in exploration.items()
        if e.get("status") in _OPERATOR_CLEARABLE
    )
    live_count = sum(1 for v in jurisdictions.values() if v["live"] is True)
    return {
        "jurisdictions": jurisdictions,
        "manual_only": manual_only,
        "exploration": exploration,
        "operator_clearable": operator_clearable,
        "summary": {
            "with_adapter": len(jurisdictions),
            "manual_only": len(manual_only),
            "probed": sum(1 for e in exploration.values() if e["status"] != "unprobed"),
            "unprobed": sum(1 for e in exploration.values() if e["status"] == "unprobed"),
            "operator_clearable": len(operator_clearable),
            "live": live_count,
            "failing": sum(1 for v in jurisdictions.values() if v["live"] is False),
            # Named explicitly so nobody reads "not live" as "broken".
            "unproven": sum(1 for v in jurisdictions.values() if v["live"] is None),
        },
        "generated_at": _now_iso(),
    }


def _hint_jurisdictions() -> list[str]:
    """Every jurisdiction ARIA knows a registry for, covered or not."""
    try:
        from .dd_orchestrator import _NATIONAL_REGISTRY_HINTS
        return list(_NATIONAL_REGISTRY_HINTS)
    except Exception as exc:
        logger.debug("[registry_coverage] hint list unavailable: %s", exc)
        return []
