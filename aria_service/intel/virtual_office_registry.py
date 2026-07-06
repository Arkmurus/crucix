"""Virtual-office / mail-drop / registered-agent address detector.

Extracted from the NAK / SERBAN / F3 learnings 2026-04-17: a US LLC
registered at a suite-numbered North Miami Beach address is a textbook
virtual-office pattern, and ARIA had no specific detector for it beyond
the generic ghost-score. This module closes that gap.

Contains a curated seed list of known virtual-office corridors
(Commercial Mail Receiving Agency addresses, registered-agent tower
blocks, high-volume Wyoming / Delaware / Nevada suites). Returns a
structured match so the DD orchestrator can (a) raise the risk signal,
(b) cite the reason, (c) prompt the operator for an enhanced-disclosure
step.

The list is intentionally conservative — only high-confidence known
entries are seeded. False positives on this signal would be worse than
a miss, because an address flagged as virtual-office pushes the DD
toward HIGH/NO-GO.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("aria.intel.virtual_office_registry")


# ═══════════════════════════════════════════════════════════════════════
# Seed list: known virtual-office / CMRA / registered-agent addresses
# ═══════════════════════════════════════════════════════════════════════
#
# Each entry:
#   match     — case-insensitive substring(s) that MUST appear in the
#               normalised address for this entry to fire.
#   provider  — what this address is (for the operator-facing message).
#   risk      — low / medium / high (how unusual it is for a legitimate
#               operating business to be at this address).
#   notes     — optional extra context.
#
# Keep the `match` list short and specific. Over-broad matches (e.g.
# "florida") would false-positive on every Florida business. Use street
# number + street name fragments that uniquely identify the corridor.

@dataclass(frozen=True)
class _VirtualOfficeEntry:
    match: tuple[str, ...]
    provider: str
    risk: str
    notes: str = ""


_KNOWN_VIRTUAL_OFFICES: tuple[_VirtualOfficeEntry, ...] = (
    # ── North Miami Beach FL — dense virtual-office corridor ──
    _VirtualOfficeEntry(
        match=("3363 ne 163rd",),
        provider="Intermix Mail / suite-rental tower, North Miami Beach FL",
        risk="high",
        notes="High density of international-facing LLCs registered to suite numbers at this building.",
    ),
    _VirtualOfficeEntry(
        match=("16900 collins ave",),
        provider="Sunny Isles mail-drop cluster",
        risk="medium",
    ),
    # ── Wyoming Cheyenne — US-LLC privacy haven ──
    _VirtualOfficeEntry(
        match=("1621 central ave", "cheyenne, wy"),
        provider="Registered Agents Inc (Cheyenne WY registered-agent address)",
        risk="medium",
        notes="Legitimate registered-agent address — but if this is ALSO the principal address, that's a shell signal.",
    ),
    _VirtualOfficeEntry(
        match=("30 n gould st", "sheridan, wy"),
        provider="Sheridan WY mass-registration address (Cloud Peak Law)",
        risk="high",
        notes="Thousands of LLCs share this single address. Almost always a shell / privacy vehicle.",
    ),
    # ── Delaware — National Corporate Research / CSC / Harvard ──
    _VirtualOfficeEntry(
        match=("1209 orange st", "wilmington, de"),
        provider="CSC (Corporation Service Company) Wilmington — registered-agent",
        risk="medium",
        notes="Legitimate RA used by Fortune 500 — but on a small LLC it means offshore / privacy structure.",
    ),
    _VirtualOfficeEntry(
        match=("1013 centre rd", "wilmington, de"),
        provider="National Corporate Research Wilmington — registered-agent",
        risk="medium",
    ),
    _VirtualOfficeEntry(
        match=("160 greentree dr", "dover, de"),
        provider="Harvard Business Services — registered-agent, Dover DE",
        risk="medium",
    ),
    _VirtualOfficeEntry(
        match=("251 little falls dr", "wilmington"),
        provider="CSC Little Falls — registered-agent cluster",
        risk="medium",
    ),
    # ── London virtual-office corridors ──
    _VirtualOfficeEntry(
        match=("71-75 shelton st", "covent garden"),
        provider="Company Express / Made Simple — high-density UK Ltd registrations",
        risk="high",
        notes="Very large volume of UK Ltd companies registered to this Covent Garden address.",
    ),
    _VirtualOfficeEntry(
        match=("86-90 paul st", "ec2a"),
        provider="Hoxton Mix / RapidFormations — virtual-office and service-address",
        risk="medium",
    ),
    _VirtualOfficeEntry(
        match=("20-22 wenlock rd",),
        provider="Wenlock Road N1 — dense UK-Ltd formation-agent cluster",
        risk="high",
    ),
    _VirtualOfficeEntry(
        match=("124 city rd", "ec1v 2nx"),
        provider="124 City Road — formation-agent high-density cluster",
        risk="high",
    ),
    # ── Gibraltar — many formation-agent tower blocks ──
    _VirtualOfficeEntry(
        match=("suite 1, burns house",),
        provider="Burns House, Gibraltar — formation-agent cluster",
        risk="medium",
    ),
    # ── Dubai / UAE free-zone virtual addresses ──
    _VirtualOfficeEntry(
        match=("business center 1", "meydan free zone"),
        provider="Meydan Free Zone virtual-office bundle",
        risk="medium",
        notes="Meydan allows virtual-office licences — legal but a shell-company indicator.",
    ),
    _VirtualOfficeEntry(
        match=("ifza business park",),
        provider="IFZA (Dubai Silicon Oasis / International Free Zone Authority) virtual office",
        risk="medium",
    ),
)


# ═══════════════════════════════════════════════════════════════════════
# Heuristic patterns — signals that are not tied to a specific building
# ═══════════════════════════════════════════════════════════════════════
#
# These fire when the address shape itself looks like a CMRA / mail-drop
# regardless of whether the specific address is in the seed list. They
# carry lower confidence than a seed-list hit but should still surface
# as a data gap.

# Matches:   #602   Suite 600   Ste. 1200   Unit 42
# Combined with a street-level business address, a high suite number is
# one of several signals. We DO NOT fire on suite-number alone because
# legitimate offices also have suites.
_SUITE_RE = re.compile(
    r"\b(?:#|ste\.?|suite|unit|apt\.?|apartment)\s*(\d{1,5})\b",
    re.IGNORECASE,
)

# "PMB" = Private Mail Box (USPS CMRA-required designation). This is a
# STRONG signal — by law a USPS CMRA recipient should include PMB.
_PMB_RE = re.compile(r"\bp\.?\s*m\.?\s*b\.?\b", re.IGNORECASE)

# Generic "mailbox" / "box" language without a PO box format
_MAILBOX_RE = re.compile(
    r"\b(mailbox|mail\s*box|mail\s*drop|c/o\s+registered\s+agent)\b",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def _normalise(address: str) -> str:
    """Lowercase, collapse whitespace, drop double punctuation."""
    addr = (address or "").lower()
    addr = re.sub(r"\s+", " ", addr)
    addr = addr.replace(",,", ",")
    return addr.strip()


def check_address(address: str) -> dict[str, Any]:
    """Check an address against the virtual-office registry + heuristics.

    Returns a dict:
        {
          "is_virtual_office":  bool,
          "confidence":         "HIGH" | "MEDIUM" | "LOW" | "NONE",
          "risk":               "high" | "medium" | "low" | "none",
          "provider":           str | None,
          "signals":            [str, ...],   # human-readable
          "notes":              str,
          "address":            <original input>,
        }
    """
    out: dict[str, Any] = {
        "is_virtual_office": False,
        "confidence": "NONE",
        "risk": "none",
        "provider": None,
        "signals": [],
        "notes": "",
        "address": address,
    }
    if not address or not address.strip():
        return out

    normalised = _normalise(address)

    # 1. Seed-list match — strongest signal
    for entry in _KNOWN_VIRTUAL_OFFICES:
        if all(m in normalised for m in entry.match):
            out["is_virtual_office"] = True
            out["confidence"] = "HIGH"
            out["risk"] = entry.risk
            out["provider"] = entry.provider
            out["signals"].append(
                f"Address matches known virtual-office corridor: {entry.provider}"
            )
            if entry.notes:
                out["notes"] = entry.notes
            return out

    # 2. PMB marker — law-level CMRA signal
    if _PMB_RE.search(address):
        out["is_virtual_office"] = True
        out["confidence"] = "HIGH"
        out["risk"] = "high"
        out["provider"] = "USPS CMRA (Commercial Mail Receiving Agency)"
        out["signals"].append(
            "Address contains 'PMB' — USPS designation for a Commercial Mail "
            "Receiving Agency. This is a mail-drop by definition."
        )
        return out

    # 3. Generic mail-drop language
    if _MAILBOX_RE.search(address):
        out["is_virtual_office"] = True
        out["confidence"] = "MEDIUM"
        out["risk"] = "medium"
        out["signals"].append(
            "Address contains generic mail-drop language (mailbox / mail-drop "
            "/ c/o registered agent) — operating business is not at this location."
        )
        return out

    # 4. Heuristic: suite + certain known problem cities
    suite_match = _SUITE_RE.search(address)
    if suite_match:
        suite_no = suite_match.group(1)
        # A suite number alone is not a signal. But a high suite number in a
        # short address (no other street context) is a weak signal.
        out["signals"].append(
            f"Address includes suite/unit number {suite_no} — not a signal "
            f"by itself; cross-check with virtual-office list annually."
        )
        # Do not flip is_virtual_office on this alone.

    # Brain-hook: feed check result to the brain so stale-module alerts
    # stop firing. Only absorb on a positive match (actual work).
    if out["is_virtual_office"]:
        try:
            import asyncio as _asyncio
            from . import brain_hook as _bh
            provider = out.get("provider") or "virtual-office"
            risk = out.get("risk") or "medium"
            async def _emit():
                try:
                    await _bh.absorb(
                        module="virtual_office_registry",
                        summary=f"Virtual-office match: {provider} (risk={risk})",
                        success=True,
                        confidence=out.get("confidence") or "MEDIUM",
                        gap_type="ghost_entity" if risk == "high" else None,
                        gap_detail=address[:160],
                    )
                except Exception as e:
                    logger.debug("virtual_office_registry brain_hook failed: %s", e)
            try:
                _asyncio.get_running_loop().create_task(_emit())
            except RuntimeError:
                pass
        except Exception as exc:
            logger.debug("virtual_office brain dispatch failed: %s", exc)

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="virtual_office_registry",
        summary="Check Address",
        source_id="virtual_office_registry:R-F996",
    )

    return out


def known_providers() -> list[dict[str, Any]]:
    """Expose the seed list for tests / operator review."""
    return [
        {
            "provider": e.provider,
            "match": list(e.match),
            "risk": e.risk,
            "notes": e.notes,
        }
        for e in _KNOWN_VIRTUAL_OFFICES
    ]


def summary() -> dict[str, Any]:
    """High-level counts for capability manifest."""
    by_risk: dict[str, int] = {}
    for e in _KNOWN_VIRTUAL_OFFICES:
        by_risk[e.risk] = by_risk.get(e.risk, 0) + 1
    return {
        "total_seed_entries": len(_KNOWN_VIRTUAL_OFFICES),
        "by_risk": by_risk,
        "heuristic_patterns": ["PMB marker", "mailbox / mail-drop language"],
    }

# R-F2119 §21a — wire failure handler for virtual_office_registry
try:
    wire_failure(module="virtual_office_registry", detail="module shutdown",
                gap_type="engine_failure", source="virtual_office_registry:shutdown")
except Exception:
    pass
