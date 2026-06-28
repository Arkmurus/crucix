"""Protective-reply drafter.

Extracted from the SERBAN letter learning 2026-04-17: when a
counterparty sends a hostile / deceptive letter imposing unilateral
terms, the right ARIA output is not just the analysis — it is also a
ready-to-send, protective reply that (a) does not acknowledge the
unilateral terms, (b) does not close the channel, (c) restates our
compliance-first position, (d) asks for the specific disclosures we
actually need.

Design:
  - Template-based. Works with NO live LLM call — deterministic output
    even when Anthropic/DeepSeek are offline.
  - Optional `polish` pass via the compliance writer when available.
  - Caller supplies: counterparty name, deal subject, deception notes,
    unverified cited artifacts, and the specific issues to surface.
  - Returns structured dict: { subject, body, recipients, attachments }
    plus a plain-text rendering suitable for email paste.

NOT a legal opinion. The output is a good-faith business-reply draft
intended to buy time and force disclosure. Legal sign-off still
required before sending.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.intel.protective_reply_drafter")


# ═══════════════════════════════════════════════════════════════════════
# Inputs
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ProtectiveReplyInput:
    counterparty:           str
    deal_subject:           str
    deception_tier:         str = "ELEVATED"   # LOW/MODERATE/ELEVATED/HIGH
    deception_signals:      list[str] = field(default_factory=list)
    unverified_citations:   list[str] = field(default_factory=list)
    unilateral_terms:       list[str] = field(default_factory=list)
    required_disclosures:   list[str] = field(default_factory=list)
    sender_name:            str = "Arkmurus BD Team"
    sender_company:         str = "Arkmurus Ltd"
    cc_list:                list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# Builder
# ═══════════════════════════════════════════════════════════════════════

def _opening_line(tier: str) -> str:
    """Tone-matched opening line — firmer as tier rises."""
    tier_u = (tier or "").upper()
    if tier_u == "HIGH":
        return (
            "We acknowledge receipt of your correspondence. Before we can "
            "respond on substance, we need to address several process and "
            "disclosure concerns it raises."
        )
    if tier_u == "ELEVATED":
        return (
            "Thank you for your letter. We acknowledge receipt. We cannot "
            "proceed on the terms proposed without first resolving the "
            "disclosure and structural concerns set out below."
        )
    return (
        "Thank you for your letter. We will revert on substance once the "
        "procedural points below are agreed."
    )


def _standard_position_block(sender_company: str) -> str:
    return (
        f"1. Compliance-first position.\n"
        f"   {sender_company} operates under the UK Bribery Act 2010 "
        f"(including section 7 adequate procedures), the US FCPA where a "
        f"US nexus is present, UK export-control law (Export Control Order "
        f"2008, SPIRE), and applicable sanctions regimes (OFAC, UK OFSI, EU, "
        f"UN). No engagement, intermediary relationship, or payment may be "
        f"structured in a way that conflicts with those obligations.\n"
    )


def _unilateral_terms_block(terms: list[str]) -> str:
    if not terms:
        return ""
    lines = ["2. Unilateral terms in your letter.\n",
             "   The following provisions in your correspondence are not "
             "agreed and are not binding on us by virtue of receipt:\n"]
    for t in terms:
        lines.append(f"   - {t}\n")
    lines.append(
        "   We will consider these on a clause-by-clause basis only once "
        "the disclosures requested in section 4 have been provided.\n"
    )
    return "".join(lines)


def _citations_block(unverified: list[str]) -> str:
    if not unverified:
        return ""
    joined = ", ".join(unverified)
    return (
        f"3. Cited references we cannot verify.\n"
        f"   Your letter cites the following document identifiers for which "
        f"we have no prior record on our side: {joined}. Please provide "
        f"copies of each, with the original execution date and signing "
        f"parties, so we can reconcile against our correspondence log "
        f"before acknowledging them in any future communication.\n"
    )


def _deception_signals_block(signals: list[str]) -> str:
    """Internal-only — NOT included in the outbound body. Surfaced back
    to the operator as a parallel note so they understand why the
    tone is what it is."""
    if not signals:
        return ""
    lines = ["Internal note (NOT FOR SEND) — deception signals detected:\n"]
    for s in signals[:8]:
        lines.append(f"   • {s}\n")
    return "".join(lines)


def _disclosures_block(required: list[str]) -> str:
    lines = ["4. Disclosures required before further engagement.\n",
             "   To proceed, we need the following, in writing and on "
             "letterhead, from the authorised signatory of your company:\n"]
    default_disclosures = [
        "the identity and registered address of the ultimate end-user and "
        "end-customer of the goods, with end-user certificate (EUC) or "
        "equivalent government letter",
        "the full ownership chain of any financing entity, including UBO "
        "at 25%+ thresholds, supported by certified register extracts and "
        "(for US entities) a FinCEN BOI report",
        "the intended payment currency and the banking route, including "
        "correspondent banks in each jurisdiction through which funds will "
        "pass",
        "confirmation that no party to the transaction is on any OFAC, UK "
        "OFSI, EU consolidated, or UN sanctions list, with the date of the "
        "screening",
    ]
    for d in (required or default_disclosures):
        lines.append(f"   - {d}\n")
    return "".join(lines)


def _closing_block(sender_name: str, sender_company: str) -> str:
    return (
        f"5. Channel and next step.\n"
        f"   This is a procedural response, not a counter-offer. No offer, "
        f"commitment, or waiver is implied or given by this letter. We "
        f"remain open to progressing the opportunity once the disclosures "
        f"above are in place and a mutually acceptable framework agreement "
        f"is in effect.\n"
        f"\n"
        f"   For any further correspondence on this matter please reply to "
        f"this email thread only.\n"
        f"\n"
        f"Sincerely,\n"
        f"{sender_name}\n"
        f"{sender_company}\n"
    )


def draft_protective_reply(inp: ProtectiveReplyInput) -> dict[str, Any]:
    """Produce a ready-to-send protective reply plus metadata.

    Returns:
        {
          "subject":           str,
          "body":              str,             # plain text, email-paste ready
          "internal_note":     str,             # why this tone was chosen
          "recipients":        [str, ...],      # empty — operator fills to:
          "cc":                [str, ...],
          "generated_at":      ISO-8601 string,
          "tier":              echo of inp.deception_tier,
        }
    """
    subject_line = (
        f"Re: {inp.deal_subject} — procedural response and disclosure request"
    )
    body_parts = [
        _opening_line(inp.deception_tier),
        "",
        _standard_position_block(inp.sender_company),
        _unilateral_terms_block(inp.unilateral_terms),
        _citations_block(inp.unverified_citations),
        _disclosures_block(inp.required_disclosures),
        _closing_block(inp.sender_name, inp.sender_company),
    ]
    body = "\n".join(p for p in body_parts if p is not None)

    out = {
        "subject": subject_line,
        "body": body,
        "internal_note": _deception_signals_block(inp.deception_signals),
        "recipients": [],
        "cc": list(inp.cc_list or []),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tier": inp.deception_tier,
        "counterparty": inp.counterparty,
    }

    # Brain-hook: each protective reply drafted is high-value work
    try:
        import asyncio as _asyncio
        from . import brain_hook as _bh
        async def _emit():
            try:
                await _bh.absorb(
                    module="protective_reply_drafter",
                    summary=f"Protective reply drafted for {inp.counterparty} "
                            f"(tier={inp.deception_tier}, subject={inp.deal_subject[:60]})",
                    entity_name=inp.counterparty,
                    success=True,
                    confidence="ASSESSED",
                )
            except Exception as e:
                logger.debug("protective_reply brain_hook failed: %s", e)
        try:
            _asyncio.get_running_loop().create_task(_emit())
        except RuntimeError:
            pass
    except Exception as exc:
        logger.debug("protective_reply brain dispatch failed: %s", exc)
        from .engine_wiring import wire_failure as _wf
        _wf(
            module="protective_reply_drafter",
            detail=f"brain dispatch failed: {exc}",
            gap_type="engine_failure",
            source="protective_reply_drafter:draft_protective_reply",
        )

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="protective_reply_drafter",
        summary="Draft Protective Reply",
        source_id="protective_reply_drafter:R-F996",
    )

    return out


# ═══════════════════════════════════════════════════════════════════════
# Integration helper — call-site glue for deception_detection
# ═══════════════════════════════════════════════════════════════════════

def draft_if_deception_high(
    deception_score_tier: str,
    counterparty: str,
    deal_subject: str,
    deception_signals: list[str] | None = None,
    unverified_citations: list[str] | None = None,
    unilateral_terms: list[str] | None = None,
) -> dict[str, Any] | None:
    """Convenience: only draft when the deception score is ELEVATED or HIGH.

    Returns None for LOW/MODERATE so callers can unconditionally invoke
    without branching.
    """
    tier = (deception_score_tier or "").upper()
    if tier not in ("ELEVATED", "HIGH"):
        return None

    inp = ProtectiveReplyInput(
        counterparty=counterparty,
        deal_subject=deal_subject,
        deception_tier=tier,
        deception_signals=deception_signals or [],
        unverified_citations=unverified_citations or [],
        unilateral_terms=unilateral_terms or [],
    )
    return draft_protective_reply(inp)

# R-F2119 §21a — wire failure handler for protective_reply_drafter
try:
    wire_failure(module="protective_reply_drafter", detail="module shutdown",
                gap_type="engine_failure", source="protective_reply_drafter:shutdown")
except Exception:
    pass
