"""Regional bright-line compliance rules.

Hard / enhanced-DD rules for specific regions where the commercial
opportunity is significant but the compliance posture requires a
pre-mandate gate. These are ABSOLUTE rules encoded for machine
checking — not advisory heuristics.

Extracted from the 2026-04-17 heat-map expansion review:
  - AES Alliance (Niger / Mali / Burkina Faso) — Russian intermediary
    involvement + ECOWAS sanctions / arms embargo layer
  - Algeria dual-exposure — CAATSA §231 risk for brokers active in
    Morocco + Algeria simultaneously
  - DRC counterparty rule — armed-group funding + UN GoE scrutiny
  - UAE-adjacent proliferation — Houthi network, Iran re-export risk
  - Libya — comprehensive UN arms embargo (Resolution 1970)
  - Myanmar — comprehensive EU / UK / US sanctions

Each rule returns a structured flag: severity + required actions +
source citations. Operators decide whether to accept a mandate after
seeing the flag — ARIA does NOT auto-decline, but it makes the risk
visible and auditable.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("aria.intel.regional_bright_lines")


# ═══════════════════════════════════════════════════════════════════════
# Rule definitions
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BrightLineRule:
    code:         str
    title:        str
    severity:     str              # "hard_stop" | "enhanced_dd" | "flag"
    triggers:     tuple[str, ...]   # lowercase keywords to watch in text
    required:     tuple[str, ...]   # checks that must pass
    citations:    tuple[str, ...]
    rationale:    str


_RULES: tuple[BrightLineRule, ...] = (
    # ── AES Alliance (Niger / Mali / Burkina Faso) ─────────────────────
    BrightLineRule(
        code="AES_ALLIANCE",
        title="Sahel AES Alliance — Russian intermediary + ECOWAS layer",
        severity="enhanced_dd",
        triggers=(
            "niger", "niamey",
            "mali", "bamako",
            "burkina faso", "ouagadougou",
            "aes alliance", "alliance of sahel states",
        ),
        required=(
            "Screen counterparty for Wagner / Africa Corps affiliation (OFAC + UK OFSI designations)",
            "Verify no ECOWAS sanctions / arms-embargo breach (ECOWAS Commission list)",
            "Confirm end-use certificate terminates within AES state — no onward transit to Russian-held positions",
            "UK ECJU consultation before any licence application",
        ),
        citations=(
            "ECOWAS Commission arms-embargo measures (imposed 2023-2024 against AES Alliance members)",
            "OFAC Wagner Group SDN designation (2022)",
            "UK OFSI Wagner Group designation (2023)",
            "UN Panel of Experts on Mali (1735 Committee)",
        ),
        rationale=(
            "AES Alliance states have pivoted from Western to Russian / Chinese / "
            "Turkish supply. Wagner / Africa Corps footprint creates secondary "
            "sanctions exposure for European brokers. ECOWAS sanctions regime "
            "creates direct exposure for Arkmurus. Do not accept mandate "
            "without documented clearance on all four checks."
        ),
    ),

    # ── Algeria dual-exposure ──────────────────────────────────────────
    BrightLineRule(
        code="ALGERIA_DUAL_EXPOSURE",
        title="Algeria dual-exposure — CAATSA §231 risk when combined with Morocco",
        severity="enhanced_dd",
        triggers=("algeria", "algiers", "mdn"),
        required=(
            "Confirm Arkmurus is NOT simultaneously engaged in Morocco on same product category",
            "Screen counterparty for CAATSA §231 significant-transaction exposure",
            "Confirm payment currency is NOT USD, OR confirm transaction does not meet 'significant' threshold",
            "Verify no Russian-origin component in supply chain",
        ),
        citations=(
            "CAATSA §231 — Countering America's Adversaries Through Sanctions Act",
            "US State Department §231 guidance (12 prohibited Russian defence entities)",
            "SIPRI Algeria transfer database (Russia is largest supplier)",
        ),
        rationale=(
            "Algeria is Russia's primary African arms client. CAATSA §231 creates "
            "secondary-sanctions exposure for any third-country broker whose "
            "transactions intersect with the US prohibition list. Concurrent "
            "engagement with Morocco (US MNNA) compounds re-export / re-transfer "
            "risk — any product that touches both markets simultaneously becomes "
            "a dual-use liability."
        ),
    ),

    # ── DRC counterparty ───────────────────────────────────────────────
    BrightLineRule(
        code="DRC_COUNTERPARTY",
        title="DRC counterparty — armed-group funding + UN GoE scrutiny",
        severity="enhanced_dd",
        triggers=(
            "drc", "democratic republic of congo", "congo-kinshasa",
            "fardc", "kinshasa", "m23", "monusco", "samidrc",
        ),
        required=(
            "Confirm counterparty is NOT on UN DRC sanctions list (1533 Committee)",
            "Confirm no minerals-funded source-of-funds (Dodd-Frank §1502 / EU Conflict Minerals Regulation)",
            "If peacekeeping-mission supply: confirm UN Panel of Experts pre-notification",
            "Review most-recent UN Group of Experts on DRC report for counterparty mention",
        ),
        citations=(
            "UN Security Council Resolution 2688 (2023) — renewed DRC arms-embargo exemptions",
            "UN GoE DRC — annual S/2024/432 series",
            "EU Conflict Minerals Regulation (2017/821)",
            "Dodd-Frank §1502 (US conflict-minerals disclosure)",
        ),
        rationale=(
            "Multi-party procurement (government, peacekeeping missions, "
            "parallel supply chains) creates intersection risk. UN Panel of "
            "Experts publishes annual findings on supply-chain participants; "
            "a counterparty mention in those reports is a hard compliance "
            "event. Minerals-funded transactions carry US / EU disclosure "
            "obligations that propagate to advisory brokers."
        ),
    ),

    # ── UAE-adjacent / Houthi proliferation ────────────────────────────
    BrightLineRule(
        code="UAE_HOUTHI_PROLIFERATION",
        title="UAE free-zone — Houthi / Iran proliferation-network overlay",
        severity="enhanced_dd",
        triggers=(
            "uae", "emirates", "abu dhabi", "dubai", "sharjah",
            "jebel ali", "dafza", "dmcc", "jafz",
            "ras al khaimah", "rak",
        ),
        required=(
            "Screen counterparty against OFAC SDN (Houthi, IRGC, Hezbollah designations)",
            "Screen against UK OFSI + EU consolidated + UN 1718 (Iran) + 2140 (Yemen)",
            "Flag entity if recent incorporation + thin staff + address mismatch (front-company pattern)",
            "Verify no trans-shipment route via UAE to Yemen or Iran",
        ),
        citations=(
            "OFAC Counter-Terrorism (Houthi Ansarallah SDGT designation)",
            "UN Security Council Resolution 2140 (Yemen sanctions)",
            "UK OFSI Iran regime",
            "EU Council Decision 2014/512/CFSP (Iran)",
        ),
        rationale=(
            "UAE free zones (Jebel Ali, DAFZA, DMCC, JAFZ, RAK FTZ) are "
            "frequent transit points for Iran / Houthi proliferation finance. "
            "Enhanced sanctions screening + front-company pattern checks are "
            "mandatory before engaging any newly-incorporated UAE entity."
        ),
    ),

    # ── Libya arms embargo ─────────────────────────────────────────────
    BrightLineRule(
        code="LIBYA_EMBARGO",
        title="Libya — UN comprehensive arms embargo (Resolution 1970)",
        severity="hard_stop",
        triggers=("libya", "tripoli", "tobruk", "benghazi", "gna", "gnu", "hor", "hna", "lna", "haftar"),
        required=(
            "ANY arms transfer is prohibited absent explicit UN Panel of Experts authorisation",
            "Advisory-only services require disclosure to UK ECJU / OFSI",
            "No payment rail that connects to either parallel government without pre-clearance",
        ),
        citations=(
            "UN Security Council Resolution 1970 (2011) and subsequent renewals",
            "UN Panel of Experts on Libya annual reports",
        ),
        rationale=(
            "UN arms embargo on Libya is comprehensive and active. The "
            "embargo covers transfer of arms, related materiel, and the "
            "provision of armed mercenary personnel. Only advisory-only "
            "services are possible and only with pre-cleared scope. "
            "Arms transfer of any kind is a hard-stop."
        ),
    ),

    # ── Myanmar sanctions ──────────────────────────────────────────────
    BrightLineRule(
        code="MYANMAR_SANCTIONS",
        title="Myanmar — comprehensive EU / UK / US sanctions (post-2021 coup)",
        severity="hard_stop",
        triggers=("myanmar", "burma", "tatmadaw", "naypyidaw", "yangon"),
        required=(
            "No arms transfer permitted under UK / EU / US sanctions regimes",
            "No advisory engagement with sanctioned military or MoHA entities",
            "Screen any Myanmar-adjacent counterparty against OFAC + UK OFSI + EU lists",
        ),
        citations=(
            "UK Myanmar (Sanctions) Regulations 2021",
            "EU Council Decision (CFSP) 2013/184 (Myanmar, as amended)",
            "US EO 14014 (Burma)",
        ),
        rationale=(
            "Post-2021 coup sanctions regimes prohibit arms transfer, "
            "military-adjacent advisory, and most economic engagement with "
            "Tatmadaw-controlled entities. Any Myanmar-connected mandate is "
            "a hard-stop."
        ),
    ),

    # ── DPRK — UN 1718 ─────────────────────────────────────────────────
    BrightLineRule(
        code="DPRK_UN1718",
        title="DPRK — UN 1718 comprehensive sanctions",
        severity="hard_stop",
        triggers=("dprk", "north korea", "pyongyang", "kpark", "komid"),
        required=(
            "ANY engagement prohibited under UN 1718 Committee scope",
            "Any counterparty mention of DPRK-origin equipment is a hard-stop",
        ),
        citations=(
            "UN Security Council Resolution 1718 (2006) and successor resolutions (2087, 2094, 2270, 2321, 2371, 2375, 2397)",
            "UN 1718 Committee Panel of Experts reports",
        ),
        rationale=(
            "Universal hard-stop. All UN member states are obligated. "
            "Any DPRK-adjacent mandate ends the conversation."
        ),
    ),
)


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def check_text(text: str) -> list[dict[str, Any]]:
    """Scan free-text for bright-line triggers.

    Returns a list of matched rules, each as a dict suitable for
    rendering into a DD finding or chat-context injection. Empty list
    if no rules are triggered.

    Side-effect: increments a Redis-backed hit counter per rule so the
    autonomy_surface panel + WhatsApp briefing can show what fired in
    the last 24h. Counter is best-effort (fails silently if Redis is
    unreachable).
    """
    if not text or not text.strip():
        return []
    needle = text.lower()
    out: list[dict[str, Any]] = []
    for rule in _RULES:
        for trigger in rule.triggers:
            # Use whole-word matching for short triggers; substring for longer
            if len(trigger) <= 4:
                pat = rf"\b{re.escape(trigger)}\b"
                if re.search(pat, needle):
                    out.append(_rule_to_dict(rule, trigger))
                    break
            else:
                if trigger in needle:
                    out.append(_rule_to_dict(rule, trigger))
                    break
    # Fire-and-forget hit counter + brain-hook absorb
    if out:
        try:
            _record_hits([h["code"] for h in out])
        except Exception as exc:
            logger.debug("bright-line hit counter failed: %s", exc)
        try:
            import asyncio as _asyncio
            from . import brain_hook as _bh
            codes = [h["code"] for h in out]
            sevs = [h["severity"] for h in out]
            worst = "hard_stop" if "hard_stop" in sevs else (
                "enhanced_dd" if "enhanced_dd" in sevs else "flag"
            )
            async def _emit():
                try:
                    await _bh.absorb(
                        module="regional_bright_lines",
                        summary=f"Bright-lines triggered: {', '.join(codes)} ({worst})",
                        success=True,
                        confidence="CONFIRMED",
                        # A triggered hard-stop/enhanced-DD IS a capability gap
                        # in the sense that it identifies compliance friction
                        # that needs operator action.
                        gap_type="compliance_gate" if worst != "flag" else None,
                        gap_detail=", ".join(codes) if worst != "flag" else None,
                    )
                except Exception as e:
                    logger.debug("bright-lines brain_hook failed: %s", e)
            try:
                loop = _asyncio.get_running_loop()
                loop.create_task(_emit())
            except RuntimeError:
                # No running loop — skip (check_text was called from sync).
                pass
        except Exception as exc:
            logger.debug("bright-lines brain_hook dispatch failed: %s", exc)
    return out


# ═══════════════════════════════════════════════════════════════════════
# Hit counter (Redis-backed, 24h rolling)
# ═══════════════════════════════════════════════════════════════════════

_HITS_KEY = "crucix:aria:bright_lines:hits_24h"


def _record_hits(codes: list[str]) -> None:
    """Best-effort incremental write to the rolling 24h hit log."""
    from . import redis_store as rs  # local import to avoid cycles at module load
    import asyncio
    from datetime import datetime, timezone

    async def _write():
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            existing = await rs.get_json(_HITS_KEY) or {}
            if not isinstance(existing, dict) or "items" not in existing:
                existing = {"items": []}
            for code in codes:
                existing["items"].append({"code": code, "at": now_iso})
            # Trim to last 24h
            cutoff = datetime.now(timezone.utc).timestamp() - 86400
            kept = []
            for it in existing["items"][-500:]:
                try:
                    ts = datetime.fromisoformat(it["at"].replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = 0
                if ts >= cutoff:
                    kept.append(it)
            existing["items"] = kept
            await rs.set_json(_HITS_KEY, existing, ex=172800)
        except Exception:
            # Redis unreachable — silently skip; counter is best-effort.
            return

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_write())
    except RuntimeError:
        # No running loop — fall back to a blocking run (only fires when
        # check_text is called outside an event loop, e.g. CLI tests).
        try:
            asyncio.run(_write())
        except Exception as exc:
            # Already-closed-loop / nested-run conditions — drop silently.
            import logging as _logging
            _logging.getLogger("aria.intel.regional_bright_lines").debug(
                "hit-counter fallback write failed: %s", exc
            )


async def get_hits_24h() -> dict[str, Any]:
    """Return the rolling 24h hit counter for autonomy_surface."""
    from . import redis_store as rs
    from datetime import datetime, timezone
    try:
        data = await rs.get_json(_HITS_KEY)
    except Exception:
        return {"total": 0, "by_code": {}, "items": []}
    if not isinstance(data, dict):
        return {"total": 0, "by_code": {}, "items": []}
    items = data.get("items") or []
    # Re-filter to 24h to correct for any write-side drift
    cutoff = datetime.now(timezone.utc).timestamp() - 86400
    kept = []
    for it in items:
        try:
            ts = datetime.fromisoformat(it["at"].replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if ts >= cutoff:
            kept.append(it)
    by_code: dict[str, int] = {}
    for it in kept:
        c = it.get("code", "?")
        by_code[c] = by_code.get(c, 0) + 1
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="regional_bright_lines",
        summary="Get Hits 24H",
        source_id="regional_bright_lines:R-F996",
    )

    return {"total": len(kept), "by_code": by_code, "items": kept[-20:]}


def check_country(iso2: str) -> list[dict[str, Any]]:
    """Look up rules matching a specific ISO2 country code."""
    iso2 = (iso2 or "").upper().strip()
    mapping = {
        "NE": "AES_ALLIANCE", "ML": "AES_ALLIANCE", "BF": "AES_ALLIANCE",
        "DZ": "ALGERIA_DUAL_EXPOSURE",
        "CD": "DRC_COUNTERPARTY",
        "AE": "UAE_HOUTHI_PROLIFERATION",
        "LY": "LIBYA_EMBARGO",
        "MM": "MYANMAR_SANCTIONS",
        "KP": "DPRK_UN1718",
    }
    code = mapping.get(iso2)
    if not code:
        return []
    for rule in _RULES:
        if rule.code == code:
            return [_rule_to_dict(rule, iso2.lower())]
    return []


def list_rules() -> list[dict[str, Any]]:
    """Expose the full rule catalogue — for capability manifest + dashboard."""
    return [_rule_to_dict(r, "") for r in _RULES]


def _rule_to_dict(rule: BrightLineRule, matched_trigger: str) -> dict[str, Any]:
    return {
        "code": rule.code,
        "title": rule.title,
        "severity": rule.severity,
        "matched_trigger": matched_trigger,
        "required_actions": list(rule.required),
        "citations": list(rule.citations),
        "rationale": rule.rationale,
    }


def summary() -> dict[str, Any]:
    """Counts for capability manifest."""
    by_severity: dict[str, int] = {}
    for r in _RULES:
        by_severity[r.severity] = by_severity.get(r.severity, 0) + 1
    return {
        "rules_total": len(_RULES),
        "by_severity": by_severity,
        "codes": [r.code for r in _RULES],
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
