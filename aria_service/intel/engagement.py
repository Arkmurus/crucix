"""R-F1055 — ARIA Professional Engagement Layer.

Enhances ARIA's dialogue quality, engagement, and professional presence.
Provides structured response templates for different interaction types,
ensuring ARIA communicates with the appropriate tone, depth, and structure
for every situation.

Key capabilities:
1. Response structuring — executive summaries, detailed analysis, quick answers
2. Tone calibration — professional, urgent, educational, strategic
3. Engagement quality — proactive insights, follow-up questions, context awareness
4. Confidence communication — clear uncertainty communication without hedging
5. Next-step guidance — always provide actionable next steps
"""
from __future__ import annotations
from .engine_wiring import wire_success

import logging
from typing import Any, Optional

logger = logging.getLogger("aria.engagement")


# ── Response templates ────────────────────────────────────────────────────────

def executive_summary(
    title: str,
    assessment: str,
    confidence: str,
    key_findings: list[str],
    recommendation: str,
    next_steps: Optional[list[str]] = None,
) -> str:
    """Generate a professional executive summary response.

    R-F1066: wired to brain on use.

    Use for: DD results, market intelligence, strategic recommendations.
    """
    _wire_engagement_use("executive_summary")
    lines = [
        f"## {title}",
        "",
        f"**Assessment:** {assessment}",
        f"**Confidence:** {confidence}",
        "",
        "**Key Findings:**",
    ]
    for f in key_findings:
        lines.append(f"  • {f}")
    
    lines.extend([
        "",
        f"**Recommendation:** {recommendation}",
    ])
    
    if next_steps:
        lines.extend([
            "",
            "**Recommended Next Steps:**",
        ])
        for i, step in enumerate(next_steps, 1):
            lines.append(f"  {i}. {step}")
    
    return "\n".join(lines)


def detailed_analysis(
    title: str,
    context: str,
    analysis: str,
    evidence: list[dict],
    confidence: str,
    caveats: Optional[list[str]] = None,
    recommendations: Optional[list[str]] = None,
) -> str:
    """Generate a detailed analytical response with evidence.
    
    Use for: deep research, investigations, complex queries.
    """
    lines = [
        f"# {title}",
        "",
        "## Context",
        context,
        "",
        "## Analysis",
        analysis,
        "",
        "## Supporting Evidence",
    ]
    
    for e in evidence:
        source = e.get("source", "Unknown")
        detail = e.get("detail", "")
        lines.append(f"  • [{source}] {detail}")
    
    lines.extend([
        "",
        f"**Overall Confidence:** {confidence}",
    ])
    
    if caveats:
        lines.extend([
            "",
            "**Caveats and Limitations:**",
        ])
        for c in caveats:
            lines.append(f"  • {c}")
    
    if recommendations:
        lines.extend([
            "",
            "**Recommendations:**",
        ])
        for r in recommendations:
            lines.append(f"  • {r}")
    
    return "\n".join(lines)


def quick_response(
    answer: str,
    confidence: str = "ASSESSED",
    source: str = "",
    follow_up: Optional[str] = None,
) -> str:
    """Generate a concise, professional quick response.
    
    Use for: factual queries, status checks, simple questions.
    """
    lines = [answer]
    
    if source:
        lines.append(f"\n_Source: {source}_")
    
    if confidence:
        lines.append(f"\nConfidence: {confidence}")
    
    if follow_up:
        lines.append(f"\n**Looking ahead:** {follow_up}")
    
    return "\n".join(lines)


def urgent_alert(
    title: str,
    severity: str,
    finding: str,
    impact: str,
    immediate_action: str,
) -> str:
    """Generate an urgent alert with clear severity and action.
    
    Use for: sanctions hits, compliance flags, critical market changes.
    """
    severity_icon = {
        "CRITICAL": "🔴",
        "HIGH": "🟠",
        "MEDIUM": "🟡",
        "LOW": "🟢",
    }.get(severity.upper(), "⚪")
    
    return "\n".join([
        f"{severity_icon} **{severity} ALERT: {title}**",
        "",
        f"**Finding:** {finding}",
        f"**Impact:** {impact}",
        "",
        f"**Immediate Action Required:** {immediate_action}",
    ])


def strategic_briefing(
    title: str,
    situation: str,
    implications: str,
    recommendations: list[str],
    outlook: str,
) -> str:
    """Generate a strategic briefing with situation, implications, and outlook.
    
    Use for: market briefings, competitor analysis, strategic reviews.
    """
    return "\n".join([
        f"# Strategic Briefing: {title}",
        "",
        "## Situation",
        situation,
        "",
        "## Strategic Implications",
        implications,
        "",
        "## Recommended Actions",
    ] + [f"  {i+1}. {r}" for i, r in enumerate(recommendations)] + [
        "",
        "## Outlook",
        outlook,
    ])


# ── Engagement quality enhancers ──────────────────────────────────────────────

def proactive_insight(
    context: str,
    insight: str,
    relevance: str,
    suggested_action: str,
) -> str:
    """Generate a proactive insight that adds value beyond the query.
    
    ARIA should use this when she has relevant information the user
    didn't explicitly ask for but would benefit from knowing.
    """
    return "\n".join([
        f"💡 **Additional Intelligence:**",
        f"{insight}",
        "",
        f"**Why this matters:** {relevance}",
        f"**Suggested action:** {suggested_action}",
    ])


def follow_up_questions(context: str, questions: list[str]) -> str:
    """Generate contextual follow-up questions to deepen engagement."""
    _wire_engagement_use("follow_up_questions")
    return "\n".join([
        "**To help me provide more targeted intelligence:**",
    ] + [f"  • {q}" for q in questions])


def customer_support_response(
    issue_type: str,
    summary: str,
    resolution: str,
    next_steps: Optional[list[str]] = None,
    escalation_path: Optional[str] = None,
) -> str:
    """Generate a professional customer support response.

    Use for: support requests, issue resolution, escalation handling.

    Args:
        issue_type: Type of issue (technical, data, access, billing, other).
        summary: Brief summary of the issue and what was found.
        resolution: What was done or what the resolution is.
        next_steps: Optional list of next steps for the user.
        escalation_path: Optional escalation path if the issue needs human review.
    """
    _wire_engagement_use("customer_support_response")
    lines = [
        f"## Support Response — {issue_type.replace('_', ' ').title()}",
        "",
        f"**Summary:** {summary}",
        "",
        f"**Resolution:** {resolution}",
    ]
    if next_steps:
        lines.extend([
            "",
            "**Next Steps:**",
        ])
        for step in next_steps:
            lines.append(f"  • {step}")
    if escalation_path:
        lines.extend([
            "",
            f"**Escalation:** {escalation_path}",
            "",
            "This issue has been flagged for human review. You will receive an update.",
        ])
    return "\n".join(lines)


def escalation_response(
    reason: str,
    context: str,
    expected_timeline: str = "within 24 hours",
) -> str:
    """Generate an escalation response when an issue needs human intervention.

    Use for: complex issues, security concerns, compliance questions,
    or any situation where ARIA cannot provide a definitive answer.
    """
    _wire_engagement_use("escalation_response")
    return (
        f"## Escalation Notice\n\n"
        f"**Reason:** {reason}\n\n"
        f"**Context:** {context}\n\n"
        f"This requires human review. A team member will follow up {expected_timeline}. "
        f"You will receive a notification when there is an update."
    )


def proactive_follow_up(
    previous_topic: str,
    new_information: str,
    relevance: str,
) -> str:
    """Generate a proactive follow-up when new information becomes relevant.

    Use for: alerting users to new developments on previous topics,
    surfacing relevant intelligence the user didn't ask for.
    """
    _wire_engagement_use("proactive_follow_up")
    return (
        f"## Proactive Intelligence Update\n\n"
        f"**Regarding:** {previous_topic}\n\n"
        f"**New Information:** {new_information}\n\n"
        f"**Why This Matters:** {relevance}\n\n"
        f"I am monitoring this closely and will keep you informed of developments."
    )


def context_summary(
    previous_findings: list[str],
    new_developments: list[str],
    changed_assessment: Optional[str] = None,
) -> str:
    """Summarize context from previous interactions.
    
    Use when a user returns to a previous topic.
    """
    lines = [
        "## Context from Previous Engagement",
        "",
        "**Previous Findings:**",
    ]
    for f in previous_findings:
        lines.append(f"  • {f}")
    
    if new_developments:
        lines.extend([
            "",
            "**New Developments:**",
        ])
        for d in new_developments:
            lines.append(f"  • {d}")
    
    if changed_assessment:
        lines.extend([
            "",
            f"**Updated Assessment:** {changed_assessment}",
        ])
    
    return "\n".join(lines)


# ── Tone calibration ──────────────────────────────────────────────────────────

PROFESSIONAL_OPENERS = {
    "analysis": "Here is my assessment of the situation.",
    "briefing": "Here is your intelligence briefing.",
    "alert": "I have identified a development that requires your attention.",
    "update": "Here is the latest on this matter.",
    "recommendation": "Based on my analysis, here is my recommendation.",
    "confirmation": "I can confirm the following.",
    "clarification": "Let me clarify this point.",
    "follow_up": "Following up on our previous discussion,",
}

PROFESSIONAL_CLOSERS = {
    "standard": "I will continue monitoring this and report any significant developments.",
    "urgent": "I recommend addressing this promptly. I am standing by to assist.",
    "strategic": "I recommend we discuss the strategic implications at your earliest convenience.",
    "educational": "I hope this analysis is helpful. Please let me know if you would like me to explore any aspect in greater depth.",
    "action": "Please confirm how you would like to proceed, and I will prepare the necessary documentation.",
}


def calibrate_tone(intent: str, urgency: str = "standard") -> dict:
    """Calibrate response tone based on intent and urgency."""
    _wire_engagement_use("calibrate_tone")
    opener = PROFESSIONAL_OPENERS.get(intent, "")
    closer = PROFESSIONAL_CLOSERS.get(urgency, PROFESSIONAL_CLOSERS["standard"])
    
    tone_guide = ""
    if urgency == "urgent":
        tone_guide = "Be direct and concise. Prioritize actionability over detail."
    elif urgency == "strategic":
        tone_guide = "Provide strategic context. Emphasize implications and recommendations."
    elif urgency == "educational":
        tone_guide = "Explain the methodology and reasoning. Teach the user."
    else:
        tone_guide = "Balance detail with clarity. Lead with the bottom line."
    
    return {
        "opener": opener,
        "closer": closer,
        "tone_guide": tone_guide,
    }


# ── Brain wiring ───────────────────────────────────────────────────────

_ENGAGEMENT_USES = 0


def _wire_engagement_use(function_name: str) -> None:
    """Fire-and-forget brain signal on engagement function use."""
    global _ENGAGEMENT_USES
    _ENGAGEMENT_USES += 1
    try:
        from .engine_wiring import wire_success as _ws, wire_failure
        _ws(
            module="engagement",
            summary=f"Engagement: {function_name} used",
            detail=f"Total uses: {_ENGAGEMENT_USES}",
            source_id=f"engagement:{function_name}",
        )
    except Exception:
        pass

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="engagement",
                     summary="engagement module active",
                     source_id="engagement:init")
    except Exception:
        try:
            wire_failure(module="engagement", detail="module init failed",
                        gap_type="engine_failure", source="engagement:init")
        except Exception:
            pass
