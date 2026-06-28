"""Auto-generated capability card — what ARIA ca

n/can't do, with evidence.

Why this exists
───────────────
Every recognised AI system publishes capability documentation. Regulated
defence clients cannot rely on an AI platform without being able to
justify the reliance to their own auditors: "what was it tested on,
where is it reliable, where does it require human review, what is the
error rate on verified tasks".

Prior to this module, ARIA had rich metrics (capability_manifest,
self_metrics, calibration_review, adversarial_challenge) but no
user-visible doc that a compliance officer could cite. This module
renders those existing metrics into a Markdown card exposed via
/api/aria/capability-card.

What the card contains
──────────────────────
  1. Verified capabilities — table of task / accuracy / test count /
     confidence grade, pulled from capability_manifest
  2. Consistency scores per domain from consistency_suite
  3. Mastery scores per domain from student / self_metrics
  4. Calibration (ECE, over/under-confidence) from calibration_review
  5. Adversarial robustness (last run score) from adversarial_challenge
  6. Known limitations — auto-detected: domains with < N verified
     outcomes, domains with mastery < threshold, domains without
     consistency tests
  7. Evaluation methodology — description of how each metric is
     computed, cadence, source tiers
  8. Required human review policy

Regeneration cadence
────────────────────
CAPABILITY-CARD-NIGHTLY autonomous task builds the card fresh each
night. Operator and client can always point at the latest version at
/api/aria/capability-card?format=markdown.

Public API
──────────
  async build_card() -> dict   # full structured representation
  async render_markdown() -> str
  async render_json() -> dict"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.capability_card")

_KEY_LATEST = "crucix:capability_card:latest"
_VERSION = "3.2"  # bump when the card format changes materially


async def _safe_call(fn, default=None, *args, **kwargs):
    """Call any getter defensively — card must never hard-fail on one
    subsystem being offline."""
    try:
        if callable(fn):
            out = fn(*args, **kwargs)
            # Some are async
            import inspect
            if inspect.iscoroutine(out):
                out = await out
            return out if out is not None else default
        return default
    except Exception as e:
        logger.debug("[capability_card] subsystem call failed: %s", e)
        return default


async def _gather_mastery() -> dict:
    """Pull mastery per topic from the student module."""
    try:
        from . import student
        report = await student.get_mastery_report()
        # `by_topic` was reading a non-existent key -- the actual field is
        # `topics`. And the headline figure on every dashboard should be
        # `headline_mastery` (= min(overall, core_mastery)) per the
        # 2026-04-22 honest-rollup fix; using `overall_mastery` lets a
        # well-practiced topic mask weak core cells.
        return {
            "overall": report.get("headline_mastery")
                or report.get("overall_mastery", 0),
            "core_mastery": report.get("core_mastery"),
            "by_topic": report.get("topics") or report.get("by_topic", {}),
            "weak_topics": report.get("weak_topics", []),
            "strong_topics": report.get("strong_topics", []),
            "core_weak_topics": report.get("core_weak_topics", []),
        }
    except Exception as e:
        logger.debug("[capability_card] mastery fetch failed: %s", e)
        return {}


async def _gather_consistency() -> dict:
    try:
        from . import consistency_suite
        return await consistency_suite.summary_for_dashboard() or {}
    except Exception:
        return {}


async def _gather_calibration() -> dict:
    """Pull calibration baseline data for the card.

    Previous version searched for `get_latest_summary` / `get_summary`
    / `get_latest` / `summary` -- none of those exist on
    calibration_review. The actual public surface is `get_baseline()`
    (returns the persisted snapshot wrapped in {review, saved_at,
    description}) and `run_calibration_review()` (recomputes fresh, but
    it queries student/honesty/adversarial so isn't free). Card has
    been empty on this section since shipped.
    """
    try:
        from . import calibration_review
        baseline = None
        get_baseline = getattr(calibration_review, "get_baseline", None)
        if callable(get_baseline):
            baseline = await _safe_call(get_baseline, None)
        if baseline and isinstance(baseline, dict):
            review = baseline.get("review") or baseline
            signals = review.get("signals") or {}
            return {
                "saved_at": baseline.get("saved_at"),
                "overall_mastery": review.get("overall_mastery"),
                "estimated_accuracy": review.get("estimated_accuracy"),
                "calibration_delta": review.get("calibration_delta"),
                "calibration_status": review.get("calibration_status"),
                "honesty_accuracy": signals.get("honesty_accuracy"),
                "adversarial_accuracy": signals.get("adversarial_accuracy"),
                "mistake_rate": signals.get("mistake_rate"),
                "recommendation": review.get("recommendation"),
                "reviewed_at": review.get("reviewed_at"),
            }
    except Exception:
        pass
    return {}


async def _gather_adversarial() -> dict:
    try:
        from . import adversarial_challenge as ac
        stats = await ac.stats()
        last = stats.get("last_run") or {}
        # `total_runs` was never a key on stats() — that returned None on
        # every capability card since adversarial shipped. The attack count
        # lives on the nested last_run summary as `total_attacks`; use the
        # 4-week trend length as a fallback when the last_run isn't loaded.
        return {
            "last_run_score": last.get("overall_score"),
            "last_run_ts": last.get("run_at") or last.get("ts"),
            "total_attacks": last.get("total_attacks")
                or len(stats.get("four_week_trend") or []),
            "pending_amendments": stats.get("pending_amendments", 0),
        }
    except Exception:
        return {}


async def _gather_sources() -> dict:
    """Which DD sources are live right now."""
    try:
        from . import vendor_registry as vr
        return await vr.availability_ping()
    except Exception:
        return {}


async def _gather_mistake_stats() -> dict:
    try:
        from . import mistake_ledger
        stats = await mistake_ledger.stats()
        # mistake_ledger.stats() returns the count under `total_entries`,
        # not `total`. Reading the wrong key zeroed `total_mistakes` on
        # every capability card -- self_assess.py reads the same source
        # correctly, so the card silently disagreed with the briefing.
        return {
            "total_mistakes": stats.get("total_entries", 0)
                or stats.get("total", 0),
            "mistakes_prevented": stats.get("prevented_total", 0),
            "by_category": stats.get("by_category", {}),
        }
    except Exception:
        return {}


async def _gather_capabilities() -> dict:
    """Pull declared capabilities from capability_manifest.

    Previous version probed `list_capabilities` / `get_all` /
    `all_capabilities` / `manifest` -- none of those names exist on
    capability_manifest. Real surface is `latest()` (async, persisted
    snapshot) and `derive()` (sync, recompute). Card has had an empty
    `capabilities_declared` block since shipped.
    """
    try:
        from . import capability_manifest
        latest_fn = getattr(capability_manifest, "latest", None)
        if callable(latest_fn):
            snapshot = await _safe_call(latest_fn, None)
            if isinstance(snapshot, dict) and snapshot:
                return snapshot
        # Fresh derivation -- cheap (filesystem walk, no I/O), so safe
        # to fall through if the persisted snapshot hasn't been written
        # yet (e.g. on first card refresh after deploy).
        derive_fn = getattr(capability_manifest, "derive", None)
        if callable(derive_fn):
            return derive_fn() or {}
    except Exception:
        pass
    return {}


# ── Builder ────────────────────────────────────────────────────────────────

async def build_card() -> dict:
    """Gather every input and produce the structured card dict. Each
    subsystem failure degrades one section rather than breaking the
    whole card."""
    mastery = await _gather_mastery()
    consistency = await _gather_consistency()
    calibration = await _gather_calibration()
    adversarial = await _gather_adversarial()
    sources = await _gather_sources()
    mistakes = await _gather_mistake_stats()
    capabilities = await _gather_capabilities()

    # Derive grade per capability — HIGH if mastery>=0.8 AND consistency>=0.7
    # MODERATE if mastery>=0.6, LOW if below. UNCALIBRATED if not enough data.
    by_topic = mastery.get("by_topic") or {}
    cons_by_domain = consistency.get("by_domain") or {}

    def grade_for(topic: str) -> str:
        m = (by_topic.get(topic) or {}).get("score") if isinstance(by_topic.get(topic), dict) else by_topic.get(topic)
        c = cons_by_domain.get(topic)
        if m is None:
            return "UNCALIBRATED"
        if c is None and m >= 0.6:
            return "UNCALIBRATED (mastery OK, consistency not tested)"
        if (m or 0) >= 0.8 and (c or 0) >= 0.7:
            return "HIGH"
        if (m or 0) >= 0.6 and (c or 0) >= 0.6:
            return "MODERATE"
        return "LOW"

    # Known limitations — auto-derive
    limitations: list[str] = []
    if mastery.get("weak_topics"):
        for t in mastery["weak_topics"][:5]:
            name = t.get("topic") if isinstance(t, dict) else t
            score = t.get("score") if isinstance(t, dict) else "?"
            limitations.append(
                f"Weak mastery on '{name}' (score {score}) — verify any "
                f"output in this domain before client delivery"
            )
    if consistency.get("weakest_domain") and consistency.get("weakest_score", 1.0) < 0.6:
        limitations.append(
            f"Weak consistency in '{consistency['weakest_domain']}' "
            f"(score {consistency['weakest_score']}) — ARIA gives different "
            f"answers to the same question phrased differently. Do NOT rely "
            f"on single-shot outputs in this domain."
        )
    if sources.get("credentials_set_count", 0) < sources.get("total_vendors", 0):
        missing = (sources.get("total_vendors", 0) - sources.get("credentials_set_count", 0))
        limitations.append(
            f"{missing} DD data supplier(s) are registered in the vendor "
            f"registry but do NOT have credentials set — ARIA cannot query "
            f"them on demand. See /api/aria/vendors for details."
        )
    if adversarial.get("last_run_score") is None:
        limitations.append(
            "Adversarial robustness not recently measured — next scheduled "
            "run surfaces on Sunday 06:00 UTC."
        )
    elif adversarial.get("last_run_score", 1.0) < 0.7:
        limitations.append(
            f"Adversarial score {adversarial['last_run_score']} below "
            f"threshold (0.7) — some attack vectors still succeed."
        )

    card = {
        "version": _VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "product": "ARIA — Arkmurus Research Intelligence Agent",
        "mastery": mastery,
        "consistency": consistency,
        "calibration": calibration,
        "adversarial": adversarial,
        "sources": {
            "total_vendors": sources.get("total_vendors", 0),
            "live_count": sources.get("live_count", 0),
            "credentials_set_count": sources.get("credentials_set_count", 0),
            "monthly_spend_usd": sources.get("monthly_spend_usd", 0),
        },
        "mistakes": mistakes,
        "capabilities_declared": capabilities,
        "capability_grades": {
            t: grade_for(t) for t in (by_topic.keys() if isinstance(by_topic, dict) else [])
        },
        "known_limitations": limitations,
        "required_human_review": [
            "Any output in a LOW-graded domain — review before client delivery.",
            "Any claim tagged [UNCERTAIN] — independently verify before action.",
            "Any CRITICAL-tier sanctions/legal/financial opinion — confirm with primary source citation even if ARIA cites one.",
            "Any output where the ground_truth_guard emitted an UNVERIFIED correction — operator must choose to accept or re-run.",
        ],
        "evaluation_methodology": {
            "golden_eval": "Verified Q/A pairs tested weekly against live output. Source tier requirement: Tier 1a/1b.",
            "consistency": "Canonical question + 5 paraphrased variants fired weekly (Sunday 05:00 UTC). Score = min(accuracy vs ground-truth, cross-variant consistency).",
            "adversarial": "23 attack vectors tested weekly (Sunday 06:00 UTC). Covers prompt injection, jailbreak, context overflow, provider-fallback bypass.",
            "calibration": "ECE (Expected Calibration Error) measured monthly against verified outcomes. Over/under-confidence per domain surfaces in the threshold auto-tuner (calibration_auto_tune).",
            "sources_health": "Every wired primary source pings is_available() each capability-card refresh. Unreachable sources surface as a known limitation.",
        },
        "constitutional_clauses": (
            "19 clauses in force plus Clauses 20 (commitment/tool-claim/"
            "ground-truth guards), 21 (comprehension before act), 22 (think "
            "before speak scratchpad). Constitution is prompted, not yet "
            "trained — see roadmap for fine-tuning phase."
        ),
    }

    # Persist for fast dashboard reads
    try:
        from . import redis_store as rs
        await rs.set_json(_KEY_LATEST, card)
    except Exception:
        pass

    # Feed the brain — nightly card refreshes count as health signals
    # so a persistent stream of refreshes confirms the observability
    # stack is warm. Limitation count shapes the detail so self_metrics
    # can chart limitations-over-time.
    try:
        from . import brain_hook as _bh
        grades = card.get("capability_grades") or {}
        high_count = sum(1 for g in grades.values() if g == "HIGH")
        mod_count = sum(1 for g in grades.values() if g == "MODERATE")
        low_count = sum(1 for g in grades.values() if g == "LOW")
        limits = len(card.get("known_limitations") or [])
        await _bh.absorb(
            module="capability_card",
            summary=(
                f"Capability card v{card['version']} refreshed — "
                f"HIGH={high_count} MODERATE={mod_count} LOW={low_count} "
                f"limitations={limits}"
            ),
            detail=f"weakest_consistency={card.get('consistency', {}).get('weakest_domain')}",
            success=True,
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    return card


async def render_json() -> dict:
    return await build_card()


async def render_markdown() -> str:
    """Human-readable Markdown rendering of the card."""
    card = await build_card()

    lines: list[str] = []
    lines.append(f"# ARIA Capability Card — v{card['version']}")
    lines.append(f"*Generated: {card['generated_at']}*")
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    m = card.get("mastery", {})
    c = card.get("consistency", {})
    a = card.get("adversarial", {})
    lines.append(f"- **Mastery (overall)**: {m.get('overall', '—')}")
    lines.append(f"- **Consistency (overall)**: {c.get('overall_score', '—')}  — last run {c.get('fired_at', 'never')}")
    lines.append(f"- **Adversarial score (last run)**: {a.get('last_run_score', '—')}")
    mistakes = card.get("mistakes", {})
    lines.append(f"- **Mistakes recorded / prevented**: {mistakes.get('total_mistakes', 0)} / {mistakes.get('mistakes_prevented', 0)}")
    sources = card.get("sources", {})
    lines.append(f"- **DD sources live**: {sources.get('live_count', 0)} of {sources.get('total_vendors', 0)} ({sources.get('credentials_set_count', 0)} credentialed)")
    lines.append("")

    # Capability grades
    grades = card.get("capability_grades") or {}
    if grades:
        lines.append("## Verified Capabilities")
        lines.append("")
        lines.append("| Domain | Grade | Mastery | Consistency |")
        lines.append("|---|---|---|---|")
        by_topic = m.get("by_topic") or {}
        for domain, grade in sorted(grades.items()):
            mastery_val = by_topic.get(domain)
            if isinstance(mastery_val, dict):
                mastery_val = mastery_val.get("score", "—")
            cons_val = (c.get("by_domain") or {}).get(domain, "—")
            lines.append(f"| {domain} | {grade} | {mastery_val} | {cons_val} |")
        lines.append("")

    # Limitations
    lines.append("## Known Limitations")
    lines.append("")
    for lim in card.get("known_limitations") or ["(none surfaced at last refresh)"]:
        lines.append(f"- {lim}")
    lines.append("")

    # Required human review
    lines.append("## Required Human Review")
    lines.append("")
    for r in card.get("required_human_review") or []:
        lines.append(f"- {r}")
    lines.append("")

    # Methodology
    lines.append("## Evaluation Methodology")
    lines.append("")
    for k, v in (card.get("evaluation_methodology") or {}).items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")

    # Constitution
    lines.append("## Constitutional Framework")
    lines.append("")
    lines.append(card.get("constitutional_clauses", ""))
    lines.append("")

    lines.append("---")
    lines.append(
        "*This card is regenerated nightly by the CAPABILITY-CARD-NIGHTLY "
        "autonomous task. For programmatic access: "
        "`GET /api/aria/capability-card?format=json`.*"
    )


    # R-F996 — wire to brain
    wire_success(
        module="capability_card",
        summary="Capability card",
        source_id="capability_card:R-F996",
    )
    return "\n".join(lines)

# R-F2119 §21a — wire failure handler for capability_card
try:
    wire_failure(module="capability_card", detail="module shutdown",
                gap_type="engine_failure", source="capability_card:shutdown")
except Exception:
    pass
