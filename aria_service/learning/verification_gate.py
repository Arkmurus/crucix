"""Verification gate — CRITICAL output double-check by provider disagreement.

Philosophy
══════════
99.9% reliability isn't achieved by faster hardware. It's achieved by
running the same decision through two INDEPENDENT paths and only
accepting the answer when both agree. When they disagree, flag for
human review rather than emit a confident wrong answer.

The operator directive was "invincible and flawless". The engineering
translation: on CRITICAL outputs, require agreement between two
providers (or two evidence sources). Disagreement = UNVERIFIED =
human review.

What counts as CRITICAL
═══════════════════════
  - DD verdicts with risk classification RED (HARD_STOP)
  - Sanctions yes/no answers
  - Compliance gate decisions (proceed / halt)
  - Client-facing draft outputs (writer produce with client send flag)
  - Bright-line hard_stop triggers

What counts as DISAGREEMENT
═══════════════════════════
  - DD risk classification tier differs (RED vs AMBER/GREEN)
  - Sanctions yes/no verdict differs (HIT vs CLEAN)
  - Confidence tag differs by >1 tier (CONFIRMED vs UNCERTAIN)
  - Named-entity presence differs (one finds X, other doesn't)

What isn't reasoned about here
══════════════════════════════
Narrative style, phrasing, formatting. Two providers will always write
differently. This gate cares ONLY about the structured decision.

LLM-free on the comparator. The second-opinion pass DOES call an LLM
but uses the FallbackProvider's SECOND provider (skipping the one that
gave the primary answer) so each verdict has independent provenance.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from ..intel import cost_tracker

logger = logging.getLogger("aria.learning.verification_gate")

# ── The delivered stamps, defined ONCE ───────────────────────────────────────
#
# Two wiring sites append these (dd_orchestrator BLUF, chat_ep response) and a
# third checks for the text to avoid double-stamping. Three copies of a string
# is three chances to drift; they live here now so a reword cannot make the
# dedupe check silently stop matching.
#
# WHY THE WORDING CHANGED (2026-08-03). This used to read
# "[VERIFIED BY DISAGREEMENT — both providers agree]". Measured on delivered DD
# run dd_29368fbb8b3d: it was stamped on the BLUF of a HARD_STOP that rested on
# a false OFAC name collision ("investments" was the only token shared with an
# unrelated sanctioned entity). It read to the operator as though the finding
# had been corroborated. It had not been.
#
# What this gate actually compares is two NARRATIVES generated from the SAME
# upstream finding set. Two models summarising one input agree by construction —
# that is consistency of narration, not independence of evidence. The providers
# are independent MODELS; the evidence behind them is a single body. Calling that
# "VERIFIED" invites exactly the reading it got.
#
# The stamp now says what was measured. It remains useful — a disagreement here
# is still a real signal that the narrative is unstable — but it can no longer be
# mistaken for a second source confirming the fact.
STAMP_CONSISTENT = "🛡 [CROSS-MODEL CONSISTENT — 2 models, same evidence; not independent corroboration]"

#: Substring used to detect an already-stamped response. Deliberately the
#: bracketed head rather than the whole sentence, so the tail can be reworded
#: without breaking the dedupe.
STAMP_CONSISTENT_MARKER = "[CROSS-MODEL CONSISTENT"



# ═══════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════

_REDIS_STATS_KEY        = "crucix:learning:verification:stats_24h"
_REDIS_RECENT_KEY       = "crucix:learning:verification:recent"
_MAX_RECENT             = 100

# Verdict-extraction patterns (structured decision points the comparator
# is actually reasoning about — ignores all other text differences)
_RISK_TIER_RE = re.compile(
    r"\b(RED|AMBER(?:-(?:LIGHT|HIGH))?|GREEN|CLEAN|HARD_STOP|UNVERIFIED|NO-GO|GO)\b",
    re.IGNORECASE,
)
_SANCTIONS_VERDICT_RE = re.compile(
    r"\b(CLEAN|HIT|MATCH|DEBARRED|DESIGNATED|SANCTIONED|NOT\s+SANCTIONED)\b",
    re.IGNORECASE,
)
_CONFIDENCE_TAG_RE = re.compile(
    r"\[(CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN|SPECULATIVE)\]",
    re.IGNORECASE,
)
_RECOMMENDATION_RE = re.compile(
    r"\b(PROCEED|HALT|STOP|NO-GO|GO|APPROVE|DECLINE|BLOCK|ALLOW)\b",
    re.IGNORECASE,
)

# Confidence tier ranking (for "disagreement by >1 tier" detection)
_CONF_RANK = {
    "CONFIRMED":   5,
    "PROBABLE":    4,
    "ASSESSED":    3,
    "UNCERTAIN":   2,
    "SPECULATIVE": 1,
}


# ═══════════════════════════════════════════════════════════════════════
# Severity classification — should this output go through the gate?
# ═══════════════════════════════════════════════════════════════════════

def classify_severity(text: str, metadata: dict[str, Any] | None = None) -> str:
    """Return CRITICAL / HIGH / NORMAL based on content + metadata.

    CRITICAL triggers the double-check. HIGH logs for operator review
    but doesn't block delivery. NORMAL passes through.
    """
    if not text:
        return "NORMAL"
    md = metadata or {}
    upper = text.upper()

    # CRITICAL triggers
    if md.get("risk_classification") in ("RED", "HARD_STOP"):
        return "CRITICAL"
    if md.get("is_client_facing") is True:
        return "CRITICAL"
    if "NO-GO" in upper or "HARD STOP" in upper or "HARD_STOP" in upper:
        return "CRITICAL"
    if "BRIGHT-LINES TRIGGERED" in upper and "HARD_STOP" in upper:
        return "CRITICAL"
    if md.get("writer_type") in (
        "assessment", "compliance_opinion", "procurement_paper"
    ) and md.get("for_external_send") is True:
        return "CRITICAL"

    # HIGH triggers — review-worthy but not blocking
    if any(marker in upper for marker in (
        "AMBER-LIGHT", "AMBER-HIGH", "[RECOMMENDED ACTION",
    )):
        return "HIGH"

    return "NORMAL"


# ═══════════════════════════════════════════════════════════════════════
# Verdict extraction — pull structured decisions out of free-text reply
# ═══════════════════════════════════════════════════════════════════════

def extract_verdict(text: str) -> dict[str, Any]:
    """Extract the structured verdict components from a reply.

    Returns a dict with the same shape for both passes so the
    comparator can diff them field-by-field.
    """
    out: dict[str, Any] = {
        "risk_tier":        None,
        "sanctions_verdict": None,
        "confidence_tag":   None,
        "recommendation":   None,
        "has_no_go":        False,
        "has_hard_stop":    False,
    }
    if not text:
        return out

    # Risk tier — take the FIRST occurrence which is typically the
    # headline verdict at the top of the reply
    m = _RISK_TIER_RE.search(text)
    if m:
        out["risk_tier"] = m.group(1).upper().replace("-LIGHT", "").replace("-HIGH", "")

    # Sanctions verdict
    m = _SANCTIONS_VERDICT_RE.search(text)
    if m:
        v = m.group(1).upper()
        # Normalise variants
        if "NOT" in v:
            out["sanctions_verdict"] = "CLEAN"
        elif v in ("HIT", "MATCH", "DEBARRED", "DESIGNATED", "SANCTIONED"):
            out["sanctions_verdict"] = "HIT"
        else:
            out["sanctions_verdict"] = v

    # Confidence tag (prefer the LAST one — usually the footer verdict)
    matches = _CONFIDENCE_TAG_RE.findall(text)
    if matches:
        out["confidence_tag"] = matches[-1].upper()

    # Recommendation
    m = _RECOMMENDATION_RE.search(text)
    if m:
        v = m.group(1).upper()
        if v in ("STOP", "NO-GO", "DECLINE", "BLOCK", "HALT"):
            out["recommendation"] = "HALT"
        elif v in ("PROCEED", "GO", "APPROVE", "ALLOW"):
            out["recommendation"] = "PROCEED"

    upper = text.upper()
    out["has_no_go"] = "NO-GO" in upper or "NO GO" in upper
    out["has_hard_stop"] = "HARD_STOP" in upper or "HARD STOP" in upper

    return out


# ═══════════════════════════════════════════════════════════════════════
# Disagreement detector — compares two verdicts
# ═══════════════════════════════════════════════════════════════════════

def detect_disagreement(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    """Return a structured disagreement report.

    {
      "agree":          bool,
      "disagreements":  [ {field, primary, secondary, severity}, ... ],
      "severity":       "BLOCKING" | "WARN" | "NONE",
    }
    BLOCKING = risk tier differs OR sanctions verdict differs.
    WARN     = confidence tag differs by >1 tier OR recommendation differs.
    NONE     = all fields match OR both are None for a field.
    """
    report: dict[str, Any] = {
        "agree": True,
        "disagreements": [],
        "severity": "NONE",
    }

    # Risk tier — ordinal comparison (RED > AMBER > GREEN)
    _tier_rank = {"RED": 3, "HARD_STOP": 3, "NO-GO": 3,
                  "AMBER": 2, "GREEN": 1, "CLEAN": 1, "GO": 1, "UNVERIFIED": 2}
    rp = _tier_rank.get((primary.get("risk_tier") or "").upper(), 0)
    rs = _tier_rank.get((secondary.get("risk_tier") or "").upper(), 0)
    if primary.get("risk_tier") and secondary.get("risk_tier") and rp != rs:
        report["disagreements"].append({
            "field": "risk_tier",
            "primary": primary.get("risk_tier"),
            "secondary": secondary.get("risk_tier"),
            "severity": "BLOCKING",
        })
        report["agree"] = False

    # Sanctions verdict
    if (primary.get("sanctions_verdict") and secondary.get("sanctions_verdict")
            and primary["sanctions_verdict"] != secondary["sanctions_verdict"]):
        report["disagreements"].append({
            "field": "sanctions_verdict",
            "primary": primary["sanctions_verdict"],
            "secondary": secondary["sanctions_verdict"],
            "severity": "BLOCKING",
        })
        report["agree"] = False

    # Confidence tag — disagreement if tiers differ by >1
    p_tag = (primary.get("confidence_tag") or "").upper()
    s_tag = (secondary.get("confidence_tag") or "").upper()
    if p_tag and s_tag:
        pr = _CONF_RANK.get(p_tag, 3)
        sr = _CONF_RANK.get(s_tag, 3)
        if abs(pr - sr) > 1:
            report["disagreements"].append({
                "field": "confidence_tag",
                "primary": p_tag,
                "secondary": s_tag,
                "severity": "WARN",
            })
            report["agree"] = False

    # Recommendation
    if (primary.get("recommendation") and secondary.get("recommendation")
            and primary["recommendation"] != secondary["recommendation"]):
        report["disagreements"].append({
            "field": "recommendation",
            "primary": primary["recommendation"],
            "secondary": secondary["recommendation"],
            "severity": "WARN",
        })
        report["agree"] = False

    # Severity ladder
    sevs = [d["severity"] for d in report["disagreements"]]
    if "BLOCKING" in sevs:
        report["severity"] = "BLOCKING"
    elif "WARN" in sevs:
        report["severity"] = "WARN"
    else:
        report["severity"] = "NONE"

    return report


# ═══════════════════════════════════════════════════════════════════════
# Public API — verify()
# ═══════════════════════════════════════════════════════════════════════

async def verify(
    primary_text: str,
    secondary_text: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the verification gate on two independent responses.

    Returns:
      {
        "verdict":        "CRITICAL_VERIFIED" | "CRITICAL_UNVERIFIED" | "NOT_REQUIRED",
        "severity":       severity from classify_severity(primary)
        "disagreement":   full disagreement report
        "primary_verdict":   extracted primary
        "secondary_verdict": extracted secondary
        "recommendation":    operator-facing line
      }
    """
    severity = classify_severity(primary_text, metadata)
    if severity not in ("CRITICAL", "HIGH"):
        return {
            "verdict": "NOT_REQUIRED",
            "severity": severity,
            "disagreement": None,
            "primary_verdict": None,
            "secondary_verdict": None,
            "recommendation": "Standard delivery (not a CRITICAL-grade output).",
        }

    primary_verdict = extract_verdict(primary_text)
    secondary_verdict = extract_verdict(secondary_text)
    disagreement = detect_disagreement(primary_verdict, secondary_verdict)

    if disagreement["agree"]:
        verdict = "CRITICAL_VERIFIED"
        rec = (
            "✅ Both models agree on risk + sanctions + confidence, reading the "
            "SAME evidence — narrative is stable, the finding is NOT independently "
            "corroborated. Safe to deliver with the CROSS-MODEL CONSISTENT tag."
        )
    else:
        if disagreement["severity"] == "BLOCKING":
            verdict = "CRITICAL_UNVERIFIED"
            rec = (
                "🛑 BLOCKING disagreement on risk tier or sanctions verdict. "
                "DO NOT auto-send to client. Human review required to adjudicate."
            )
        else:
            verdict = "CRITICAL_UNVERIFIED"
            rec = (
                "⚠️ Non-blocking disagreement (confidence/recommendation). "
                "Deliver with [CRITICAL — PROVIDERS DISAGREE] tag, flag "
                "for operator review."
            )

    result = {
        "verdict": verdict,
        "severity": severity,
        "disagreement": disagreement,
        "primary_verdict": primary_verdict,
        "secondary_verdict": secondary_verdict,
        "recommendation": rec,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }

    # Persist to rolling recent log + 24h stats
    try:
        from ..intel import redis_store as rs
        recent = await rs.get_json(_REDIS_RECENT_KEY) or []
        if not isinstance(recent, list):
            recent = []
        # Lightweight storage — just verdict + severity + hash of inputs
        recent.append({
            "verdict": verdict,
            "severity": disagreement["severity"],
            "at": result["verified_at"],
            "hash": hashlib.sha1(
                (primary_text[:500] + "||" + secondary_text[:500]).encode("utf-8", errors="ignore")
            , usedforsecurity=False).hexdigest()[:12],
        })
        await rs.set_json(_REDIS_RECENT_KEY, recent[-_MAX_RECENT:])

        stats = await rs.get_json(_REDIS_STATS_KEY) or {
            "verified": 0, "unverified": 0, "blocking": 0, "warn": 0,
        }
        # The dashboard's 24h figures are now derived from the timestamped
        # `recent` list (see 3767fed). These cumulative counters are kept
        # as a sub-24h convenience -- but they should still genuinely roll
        # every 24h rather than reset on every write under continuous
        # traffic. Use keepttl on subsequent writes (same pattern as
        # f981c0a / a01c359).
        is_fresh = stats.get("verified", 0) == 0 and stats.get("unverified", 0) == 0 \
                   and stats.get("blocking", 0) == 0 and stats.get("warn", 0) == 0
        if verdict == "CRITICAL_VERIFIED":
            stats["verified"] += 1
        elif verdict == "CRITICAL_UNVERIFIED":
            stats["unverified"] += 1
            if disagreement["severity"] == "BLOCKING":
                stats["blocking"] += 1
            elif disagreement["severity"] == "WARN":
                stats["warn"] += 1
        if is_fresh:
            await rs.set_json(_REDIS_STATS_KEY, stats, ex=86400)
        else:
            await rs.set_json(_REDIS_STATS_KEY, stats, keepttl=True)
    except Exception as exc:
        logger.debug("verification persistence failed: %s", exc)

    # brain_hook — every verification is a learning signal. Disagreements
    # are particularly valuable training-data candidates.
    try:
        from ..intel import brain_hook
        await brain_hook.absorb(
            module="verification_gate",
            summary=(
                f"Verification: {verdict}"
                + (f" (severity {disagreement['severity']})"
                   if verdict == "CRITICAL_UNVERIFIED" else "")
            ),
            success=verdict != "CRITICAL_UNVERIFIED",
            gap_type="provider_disagreement" if verdict == "CRITICAL_UNVERIFIED" else None,
            gap_detail=(
                ", ".join(d["field"] for d in disagreement["disagreements"][:3])
                if verdict == "CRITICAL_UNVERIFIED" else None
            ),
        )
    except Exception:
        pass

    logger.info("[verification_gate] %s — severity=%s, disagreements=%d",
                verdict, disagreement["severity"], len(disagreement["disagreements"]))
    return result


# ═══════════════════════════════════════════════════════════════════════
# Double-opinion helper — runs a given callable against two providers
# ═══════════════════════════════════════════════════════════════════════

async def double_opinion_llm(
    system_prompt: str,
    user_prompt: str,
    primary_llm: Any,
    secondary_llm: Any,
    max_tokens: int = 2000,
    timeout: float = 60.0,
) -> tuple[str, str]:
    """Run the same prompt through two distinct LLM providers and return
    (primary_text, secondary_text). Caller wraps in verify()."""
    primary_text = ""
    secondary_text = ""
    with cost_tracker.feature("verification"):
        try:
            r = await primary_llm.complete(
                system_prompt, user_prompt, max_tokens=max_tokens, timeout=timeout,
            )
            primary_text = getattr(r, "text", "") or str(r)
        except Exception as exc:
            logger.warning("primary provider failed: %s", exc)
        try:
            r = await secondary_llm.complete(
                system_prompt, user_prompt, max_tokens=max_tokens, timeout=timeout,
            )
            secondary_text = getattr(r, "text", "") or str(r)
        except Exception as exc:
            logger.warning("secondary provider failed: %s", exc)
    return primary_text, secondary_text


def _vendor_of(provider_name: str) -> str:
    """R-F3692 — the VENDOR behind a chain slot name.

    `deepseek` and `deepseek_backup` are two slots on ONE account (both are
    built from the same DEEPSEEK_API_KEY, fallback.py:1534/1545). Treating them
    as different providers would let a "second opinion" come from the same
    model on the same account — which is not a second opinion.
    """
    return (provider_name or "").lower().strip().split("_")[0]


def pick_secondary_provider(primary_llm: Any, exclude_name: str = "") -> Any | None:
    """Given the fallback-chain LLM used for the primary call, return
    a provider from a DIFFERENT VENDOR for the second opinion.

    Returns None when no non-cooling alternative vendor is configured — and
    returning None is the CORRECT, honest outcome: the caller then emits
    "CRITICAL — UNVERIFIED" instead of a cross-model agreement badge.

    R-F3692 — two defects fixed here, both of which made the badge a lie:

    1. Exclusion was by EXACT NAME, so with `exclude_name="deepseek"` the
       walk happily returned `deepseek_backup` — the same vendor, the same
       API key, the same model. Now excluded by VENDOR.
    2. `cooldown_until` was compared against `time.monotonic()`, but
       fallback.py writes and reads it in WALL-CLOCK (`time.time()`, see
       fallback.py:340, :544, :549). Epoch (~1.78e9) happens to dwarf process
       uptime, so every cooling provider looked available — the comparison
       gave the right answer for the wrong reason and would invert silently
       on any clock-source change. Now `time.time()`, matching the writer.

    Note the caller must PASS the serving provider. With `exclude_name` left
    empty this function returns the FIRST active provider, which is the one
    that just authored the answer.
    """
    if primary_llm is None:
        return None
    providers = getattr(primary_llm, "providers", None)
    if not providers:
        # Not a FallbackProvider — there's no chain to pick from.
        return None

    stats = getattr(primary_llm, "_stats", {}) or {}
    import time as _t
    now = _t.time()  # wall-clock: same domain fallback.py writes cooldown_until in

    # ── R-F3692 — SAFE BY DEFAULT ───────────────────────────────────────────
    #
    # Two of the three call sites passed no `exclude_name` at all
    # (routes/aria.py chat gate, and observe_critical_response below), so the
    # walk returned the FIRST active provider — precisely the one that had just
    # authored the answer. The reply was then stamped
    #   🛡 [CROSS-MODEL CONSISTENT — 2 models, same evidence]
    # on sanctions HIT/CLEAN and HALT/PROCEED answers where ONE model had
    # agreed with itself.
    #
    # Requiring each caller to remember the argument is the shape that failed:
    # a forgotten kwarg fails silently back to the unsafe behaviour. So when no
    # exclusion is given we derive it — the chain's first configured,
    # non-cooling provider IS the one it would have served with, which is the
    # only defensible default for a function whose entire purpose is "give me a
    # DIFFERENT model".
    #
    # The remaining call site (double_via_fallback) passes an explicit name and
    # is unaffected.
    if not (exclude_name or "").strip():
        for prov in providers:
            _n = getattr(prov, "name", "") or ""
            if not _n or not getattr(prov, "is_configured", False):
                continue
            if (stats.get(_n, {}).get("cooldown_until", 0) or 0) > now:
                continue
            exclude_name = _n
            break
    excluded_vendor = _vendor_of(exclude_name)

    for prov in providers:
        name = getattr(prov, "name", "") or ""
        if not name:
            continue
        if excluded_vendor and _vendor_of(name) == excluded_vendor:
            continue
        if not getattr(prov, "is_configured", False):
            continue
        cd = stats.get(name, {}).get("cooldown_until", 0) or 0
        if cd > now:
            continue
        return prov
    return None


async def double_via_fallback(
    system_prompt: str,
    user_prompt: str,
    fallback_llm: Any,
    max_tokens: int = 2000,
    timeout: float = 60.0,
) -> tuple[str, str, str, str]:
    """Convenience wrapper: given a FallbackProvider, produce two
    independent answers using two different underlying providers.

    Returns (primary_text, secondary_text, primary_name, secondary_name).
    Either text may be empty if that provider failed — caller must
    handle the empty-string case before calling verify().
    """
    # Primary pass through the full fallback chain — use whichever
    # provider answers first. We capture its name from the LLMResult.
    primary_text = ""
    primary_name = ""
    with cost_tracker.feature("verification"):
        try:
            r = await fallback_llm.complete(
                system_prompt, user_prompt, max_tokens=max_tokens, timeout=timeout,
            )
            primary_text = getattr(r, "text", "") or ""
            # LLMResult carries routed_via = "fallback:<provider_name>" from
            # FallbackProvider.complete — parse the name out.
            routed = getattr(r, "routed_via", "") or ""
            if routed.startswith("fallback:"):
                primary_name = routed.split(":", 1)[1]
        except Exception as exc:
            logger.warning("verification primary call failed: %s", exc)

        # Secondary pass — explicit second provider
        secondary_text = ""
        secondary_name = ""
        sec_provider = pick_secondary_provider(fallback_llm, exclude_name=primary_name)
        if sec_provider is not None:
            secondary_name = getattr(sec_provider, "name", "") or ""
            try:
                r = await sec_provider.complete(
                    system_prompt, user_prompt, max_tokens=max_tokens, timeout=timeout,
                )
                secondary_text = getattr(r, "text", "") or ""
            except Exception as exc:
                logger.warning("verification secondary call failed: %s", exc)

    return primary_text, secondary_text, primary_name, secondary_name


# ═══════════════════════════════════════════════════════════════════════
# Stats accessors
# ═══════════════════════════════════════════════════════════════════════

async def observe_critical_response(
    response_text: str,
    user_message: str,
    llm: Any,
    *,
    source: str = "chat",
) -> dict[str, Any]:
    """Run the full CRITICAL verification flow and record the outcome.

    Shared by /chat (non-stream) and /chat/stream so the gate fires on
    every WA reply, not just the non-stream path. The stream path
    cannot rewrite a response after emission, so the caller decides
    what to do with the returned verdict (append a warning in a
    follow-up meta event, flag for operator review, etc.).

    Returns (never raises — always logs stats on the way):
      {
        "fired":     bool,     # True iff we ran verify()
        "severity":  str,      # CRITICAL / HIGH / NORMAL
        "verdict":   str,      # CRITICAL_VERIFIED / CRITICAL_UNVERIFIED / SKIPPED / NOT_CRITICAL
        "skipped_reason": str, # set only when verdict == "SKIPPED"
        "secondary_provider": str,
        "disagreement": dict,  # populated on CRITICAL_UNVERIFIED
      }

    Non-CRITICAL outputs short-circuit with `fired=False,
    verdict="NOT_CRITICAL"` — no LLM call, no cost, no dashboard skew.
    """
    out: dict[str, Any] = {
        "fired": False,
        "severity": "NORMAL",
        "verdict": "NOT_CRITICAL",
        "skipped_reason": "",
        "secondary_provider": "",
        "disagreement": {},
    }
    if not response_text or llm is None:
        return out
    try:
        severity = classify_severity(response_text)
    except Exception as exc:
        logger.debug("classify_severity failed: %s", exc)
        return out
    out["severity"] = severity
    if severity != "CRITICAL":
        return out

    secondary = pick_secondary_provider(llm)
    if secondary is None:
        await record_skipped("no_secondary_provider")
        out["verdict"] = "SKIPPED"
        out["skipped_reason"] = "no_secondary_provider"
        return out

    out["secondary_provider"] = getattr(secondary, "name", "") or ""
    # Tag the secondary-opinion LLM call as `verification` so it doesn't
    # leak into uncategorized. The `double_*` helpers in this module
    # already carry the feature wrap, but observe_critical_response
    # calls the secondary directly and was missing the context.
    try:
        with cost_tracker.feature("verification"):
            r = await secondary.complete(
                "You are ARIA reviewing a user question. Return your own "
                "brief structured verdict on the question below. Include: "
                "risk tier (RED/AMBER/GREEN), sanctions (HIT/CLEAN), "
                "recommendation (HALT/PROCEED), confidence tag "
                "[CONFIRMED/PROBABLE/ASSESSED/UNCERTAIN]. Keep it to 6-10 "
                "lines. Be honest — if evidence is insufficient, say "
                "UNCERTAIN.",
                (user_message or "")[:3000],
                max_tokens=350,
                timeout=40.0,
            )
        sec_text = getattr(r, "text", "") or ""
    except Exception as exc:
        logger.debug("secondary call failed: %s", exc)
        await record_skipped("secondary_call_failed")
        out["verdict"] = "SKIPPED"
        out["skipped_reason"] = "secondary_call_failed"
        return out

    if not sec_text:
        await record_skipped("secondary_empty")
        out["verdict"] = "SKIPPED"
        out["skipped_reason"] = "secondary_empty"
        return out

    try:
        vres = await verify(
            response_text, sec_text,
            metadata={"is_client_facing": False, "source": source},
        )
    except Exception as exc:
        logger.debug("verify() failed: %s", exc)
        return out

    out["fired"] = True
    out["verdict"] = vres.get("verdict") or ""
    out["disagreement"] = vres.get("disagreement") or {}
    return out


async def record_skipped(reason: str) -> None:
    """Log that a CRITICAL output wanted verification but couldn't get it.
    Reasons: 'no_secondary_provider', 'secondary_call_failed',
    'secondary_empty'. Exposed on /verification/stats so three zeros on
    the dashboard can be distinguished from 'wanted to verify N times
    but secondary is broken N times'."""
    try:
        from ..intel import redis_store as rs
        existing = await rs.get_json(_REDIS_STATS_KEY)
        is_fresh = not isinstance(existing, dict)
        stats = existing if isinstance(existing, dict) else {
            "verified": 0, "unverified": 0, "blocking": 0, "warn": 0,
        }
        stats["skipped_total"] = stats.get("skipped_total", 0) + 1
        by_reason = stats.get("skipped_by_reason") or {}
        by_reason[reason] = by_reason.get(reason, 0) + 1
        stats["skipped_by_reason"] = by_reason
        # keepttl on subsequent writes -- under heavy skip events
        # (broken secondary provider) the previous code reset the 24h
        # window every call, turning skipped_total into a lifetime tally.
        if is_fresh:
            await rs.set_json(_REDIS_STATS_KEY, stats, ex=86400)
        else:
            await rs.set_json(_REDIS_STATS_KEY, stats, keepttl=True)
        logger.warning(
            "[verification_gate] skipped — %s (total_skipped=%d)",
            reason, stats["skipped_total"],
        )
    except Exception as exc:
        logger.debug("record_skipped failed: %s", exc)


async def get_stats() -> dict[str, Any]:
    from ..intel import redis_store as rs
    try:
        stats = await rs.get_json(_REDIS_STATS_KEY) or {}
        recent = await rs.get_json(_REDIS_RECENT_KEY) or []
    except Exception:
        stats = {}
        recent = []
    if not isinstance(recent, list):
        recent = []

    # 2026-04-26: pre-fix, this function read counters from a single
    # Redis key (`stats_24h`) with `ex=86400` — a key-eviction TTL, NOT
    # a rolling-window counter. Behaviour: after the last verification,
    # the entire counter snapped to 0 exactly 24h later. Dashboard
    # reported 0/0/0 even when `recent` plainly showed an entry at
    # T-24.5h. Now we derive the 24h stats from the timestamped
    # `recent` list (which has its own _MAX_RECENT-based eviction).
    # The Redis stats counter is kept as a cumulative-since-last-prune
    # convenience — useful for sub-24h periods, but no longer the
    # source of truth for the dashboard's 24h numbers.
    now_ts = time.time()
    cutoff = now_ts - 86400
    verified_24h = 0
    unverified_24h = 0
    blocking_24h = 0
    warn_24h = 0
    for entry in recent:
        if not isinstance(entry, dict):
            continue
        at_str = entry.get("at") or ""
        try:
            entry_ts = datetime.fromisoformat(at_str.replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if entry_ts < cutoff:
            continue
        verdict = entry.get("verdict")
        severity = entry.get("severity")
        if verdict == "CRITICAL_VERIFIED":
            verified_24h += 1
        elif verdict == "CRITICAL_UNVERIFIED":
            unverified_24h += 1
            if severity == "BLOCKING":
                blocking_24h += 1
            elif severity == "WARN":
                warn_24h += 1

    # `skipped` is still TTL-windowed because the skip events aren't
    # carried in `recent` (they record a "didn't run" reason, not a
    # verdict). Acceptable approximation — skipped is a rate signal,
    # not a precise count.
    skipped = stats.get("skipped_total", 0)

    # 2026-04-25: skipped attempts (no_secondary_provider, llm_unavailable,
    # etc.) shouldn't count as failures — they're "didn't run" events. Only
    # divide by attempts that actually completed verification. Returns None
    # when no real verifications have run, so the dashboard shows "—" not
    # "0%".
    completed = verified_24h + unverified_24h
    rate = (verified_24h / completed) if completed > 0 else None

    return {
        "verified_24h": verified_24h,
        "unverified_24h": unverified_24h,
        "blocking_disagreements_24h": blocking_24h,
        "warn_disagreements_24h": warn_24h,
        "skipped_24h": skipped,
        "skipped_by_reason_24h": stats.get("skipped_by_reason", {}),
        "verification_rate": round(rate, 3) if rate is not None else None,
        "recent": recent[-10:],
    }


def summary() -> dict[str, Any]:
    return {
        "severity_tiers": ["CRITICAL", "HIGH", "NORMAL"],
        "disagreement_severities": ["BLOCKING", "WARN", "NONE"],
        "compared_fields": [
            "risk_tier", "sanctions_verdict",
            "confidence_tag", "recommendation",
        ],
    }
