"""ARIA confidence-tagged reply footer.

Wires the existing observability signals (confidence tags, source verifier,
RAG retrieval count) into a structured footer block appended to chat replies.
The goal is to make ARIA's epistemic state visible to the client at a glance,
which is the single biggest "professional intelligence vs chatbot" cue.

Example output
══════════════
    ─────
    *Confidence:* 87% [PROBABLE]  ·  *Sources:* 12 grounded / 1 unverified
    *Tier mix:* A=3 D=4 ·  *Verification:* PASS
    ⚠ *Assumptions to validate:*
    • Ultimate beneficial owner not confirmed
    • Physical address relies on a single Companies House filing

Why this exists
═══════════════
The chat reply already carries [CONFIRMED]/[PROBABLE]/[ASSESSED]/[UNCERTAIN]/
[SPECULATIVE] tags inline, and source_verifier already computes a per-reply
verdict + grounded_rate + cited/unverified counts. None of that is surfaced
to the user in a structured way — it's all internal observability. The
footer makes it visible without changing the body of the reply.

Behind ARIA_CONFIDENCE_FOOTER env var (default ON during the new feature
window). Disabled → footer is empty string and chat replies are unchanged.

Honesty score is intentionally NOT included — honesty_judge runs in the
background after the chat reply is sent, so its score is never available
in time. The /honesty command surfaces it after the fact.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("aria.confidence_footer")

# Tag → numeric confidence used for the headline percentage. Mirrors the
# rank in reasoning_library._CONFIDENCE_RANK so the two stay aligned.
_TAG_TO_CONFIDENCE = {
    "CONFIRMED":   0.95,
    "PROBABLE":    0.78,
    "ASSESSED":    0.60,
    "UNCERTAIN":   0.40,
    "SPECULATIVE": 0.20,
}
# Pre-Phase-3 fix 2026-04-09: previously this regex was a strict exact-match
# `\[(...)\]` which missed the very common `[UNCERTAIN — insufficient data]`
# / `[ASSESSED — single source]` / `[PROBABLE — two sources]` patterns the
# LLM produces with caveat text inside the bracket. As a result the footer
# silently dropped UNCERTAIN tags, picked the next-strongest tag instead,
# and undercounted confidence floors. Now matches an optional caveat after
# the tag word.
_TAG_RE = re.compile(
    r"\[(CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN|SPECULATIVE)(?:\s*[—–-][^\]]*)?\]",
    re.IGNORECASE,
)

# Lines starting with one of these markers are picked up as "assumption to
# validate" candidates. Conservative — only counts lines the LLM has explicitly
# flagged.
_ASSUMPTION_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:assumption|caveat|gap|unknown|to verify|to confirm|"
    r"unverified|⚠️?\s*)\s*[:—-]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def is_enabled() -> bool:
    """Feature flag — default ON. Set ARIA_CONFIDENCE_FOOTER=0 to disable."""
    val = os.getenv("ARIA_CONFIDENCE_FOOTER", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


def _dominant_tag(response_text: str) -> str | None:
    """Return the WEAKEST confidence tag present in the response.

    Pre-Phase-3 cleanup 2026-04-09: this used to return the strongest tag
    (CONFIRMED beats PROBABLE beats ASSESSED…) on the theory that the headline
    should reflect what ARIA most confidently asserted. That was wrong in
    practice. It produced three known incidents (Modirum, Modirum-rerun,
    ARK-SER-01 contract review) where the footer reported
    "Confidence: 95% [CONFIRMED]" while the body had [UNCERTAIN] and
    [ASSESSED] sections — misleading readers about the actual confidence
    floor of the assessment.

    The correct rule: the footer reflects the WEAKEST tag in the body, so a
    reply that mixes [CONFIRMED] facts with [UNCERTAIN] gaps is presented as
    [UNCERTAIN] overall. The body still shows the per-section tags so the
    reader sees both the high-confidence facts and the low-confidence gaps —
    but the headline cannot oversell the assessment.
    """
    if not response_text:
        return None
    # Normalize to upper-case because _TAG_RE is now IGNORECASE — the LLM
    # occasionally writes [Probable] or [uncertain] instead of [PROBABLE].
    found = {t.upper() for t in _TAG_RE.findall(response_text)}
    if not found:
        return None
    # Iterate weakest → strongest. First match wins, so the weakest tag
    # present in the body becomes the headline.
    for tag in ("SPECULATIVE", "UNCERTAIN", "ASSESSED", "PROBABLE", "CONFIRMED"):
        if tag in found:
            return tag
    return None


def _extract_assumptions(response_text: str, max_items: int = 4) -> list[str]:
    """Pull explicit assumption / caveat / gap lines from the reply body.

    Returns at most `max_items` cleaned strings, each truncated to 160 chars.
    Conservative on purpose — we only surface what the LLM has explicitly
    flagged, never invented warnings.
    """
    if not response_text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _ASSUMPTION_RE.finditer(response_text):
        line = m.group(1).strip().rstrip(".,;:")
        if not line or len(line) < 8:
            continue
        key = line.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(line[:160])
        if len(out) >= max_items:
            break
    return out


def build_footer(
    response_text: str,
    verification: dict | None,
    rag_sources_count: int = 0,
) -> str:
    """Compose the structured footer block.

    Returns the footer string (with leading separator) ready to append to the
    reply, or an empty string if the feature is disabled, the reply is too
    short to bother, or there are no signals to display.

    Parameters
    ----------
    response_text:
        The full ARIA reply body. Used to extract confidence tags and any
        assumption lines the LLM explicitly flagged.
    verification:
        The verification summary returned by source_verifier (or None if no
        tool ran in this request). Expected keys: verdict, grounded_rate,
        cited, unverified.
    rag_sources_count:
        Number of RAG passages that contributed to the context for this
        reply. Optional — pass 0 if unknown.
    """
    if not is_enabled():
        return ""
    if not response_text or len(response_text) < 80:
        # Don't decorate short replies — looks ridiculous on a 1-line answer.
        return ""

    tag = _dominant_tag(response_text)
    has_verification = bool(verification)
    has_rag = rag_sources_count > 0

    # M4: when no tool ran OR no citations were grounded, no claim in the
    # reply is actually verified — so a [CONFIRMED] headline is misleading.
    # Demote the tag to at most [ASSESSED] in that case. The body still
    # shows the inline [CONFIRMED] tags the model produced, but the
    # headline reflects that ARIA couldn't ground them in a fresh source.
    if has_verification and tag in ("CONFIRMED", "PROBABLE"):
        v = verification or {}
        verdict = str(v.get("verdict") or "").lower()
        cited = int(v.get("cited", 0) or 0)
        unverified = int(v.get("unverified", 0) or 0)
        grounded = cited - unverified
        if verdict in ("no_tool", "no_citations") or grounded <= 0:
            logger.debug(
                "Demoting footer tag %s -> ASSESSED (verdict=%s grounded=%d)",
                tag, verdict, grounded,
            )
            tag = "ASSESSED"

    # If we have nothing to say, don't draw the box.
    if not tag and not has_verification and not has_rag:
        return ""

    lines: list[str] = ["", "─────"]

    # ── Headline: confidence percentage + dominant tag ──
    if tag:
        pct = int(round(_TAG_TO_CONFIDENCE[tag] * 100))
        head = f"*Confidence:* {pct}% [{tag}]"
    else:
        head = "*Confidence:* (no tag — treat as unverified)"

    # ── Sources / verification line ──
    if has_verification:
        v = verification or {}
        cited = int(v.get("cited", 0) or 0)
        unverified = int(v.get("unverified", 0) or 0)
        grounded = cited - unverified
        verdict = (v.get("verdict") or "").upper() or "—"
        rate = v.get("grounded_rate")
        rate_pct = f"{int(round(float(rate) * 100))}%" if isinstance(rate, (int, float)) else "—"
        head += f"  ·  *Sources:* {grounded} grounded / {unverified} unverified ({rate_pct})"
        head += f"  ·  *Verification:* {verdict}"
    elif has_rag:
        head += f"  ·  *RAG passages used:* {rag_sources_count}"

    lines.append(head)

    # ── Assumptions block (only if the LLM flagged any) ──
    assumptions = _extract_assumptions(response_text)
    if assumptions:
        lines.append("⚠ *Assumptions to validate:*")
        for a in assumptions:
            lines.append(f"  • {a}")

    return "\n".join(lines)
