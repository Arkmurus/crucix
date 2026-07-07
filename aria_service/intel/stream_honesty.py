"""R-F448 (2026-05-13) — post-response honesty pass for /chat/stream.

Mirrors the 7-guard chain that has run on /chat non-stream since
2026-04-18 (routes/aria.py:6854-7149) but was never wired to the
streaming path. Because WhatsApp uses /chat/stream as its default,
every Clause 13/17/20 enforcement, every response_verifier inline tag,
and every officeholder demotion has been observer-only for ~6 months.
See memory/stream_bypass_pattern.md.

R-F448 closes that gap by running the same guards synchronously on
the buffered stream response. The route emits the rewritten text as a
correction chunk just before the confidence footer (R-F412) and the
done event, so:

  • WhatsApp users see the corrected version naturally bundled with
    the original — they can compare and learn to trust the correction.
  • Web /chat/stream users see the original tokens stream live, then
    a clearly-marked correction banner if guards rewrote anything.

This module exists separately from routes/aria.py so the chain is
testable in isolation and so a future R-number can refactor the
duplicated /chat non-stream block to call into here too.
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import asyncio
import logging
from typing import Any

logger = logging.getLogger("aria.stream_honesty")

# Hard per-guard timeout — 8 seconds. Guards that hang must not block
# the stream from closing. Picked to match the existing per-layer DD
# budget pattern (network calls allowed to be slow, but not unbounded).
_PER_GUARD_TIMEOUT_S = 8.0

# Total honesty-pass timeout — 25 seconds. If the whole chain runs
# slower than this, we emit what we have and skip the rest.
_TOTAL_TIMEOUT_S = 25.0


async def apply_stream_honesty(
    *,
    response_text: str,
    user_message: str,
    tool_context: str = "",
    tool_used: str | None = None,
    session_id: str = "",
    user_id: str = "",
    chat_id: str = "",
    verification: dict | None = None,
    write_to_mistake_ledger: bool = True,
) -> dict[str, Any]:
    """Run the 7-guard post-response honesty chain on `response_text`.

    Returns a dict:
      {
        "rewritten":      <str>    — text after all guards run
        "changed":        <bool>   — True if any guard rewrote
        "changes":        <list[(guard_name, count)]>
        "details":        <dict[guard_name, dict]>  — per-guard result blob
        "skipped":        <list[str]>  — guards that errored or timed out
      }

    Always returns — never raises. Failures are logged and the partial
    result is returned so the stream route can still emit the done event.
    """
    if not response_text:
        return {
            "rewritten": response_text,
            "changed": False,
            "changes": [],
            "details": {},
            "skipped": ["empty_response"],
        }

    rewritten = response_text
    changes: list[tuple[str, int]] = []
    details: dict[str, dict] = {}
    skipped: list[str] = []

    async def _guard_with_timeout(name: str, coro_or_fn):
        """Run a guard with timeout. Async coroutine OR sync callable."""
        try:
            if asyncio.iscoroutine(coro_or_fn):
                return await asyncio.wait_for(coro_or_fn, timeout=_PER_GUARD_TIMEOUT_S)
            return coro_or_fn()
        except asyncio.TimeoutError:
            logger.warning("R-F448 guard %s timed out at %ss", name, _PER_GUARD_TIMEOUT_S)
            skipped.append(f"{name}:timeout")
            return None
        except Exception as e:
            logger.debug("R-F448 guard %s errored (non-fatal): %s", name, e)
            skipped.append(f"{name}:error")
            return None

    async def _run_all() -> None:
        nonlocal rewritten

        # 1. officeholder_guard — demote stale CEO/CFO/etc. claims older
        # than 12 months and append a visible WARNING block. Sync call.
        try:
            from . import officeholder_guard
            res = await _guard_with_timeout(
                "officeholder_guard",
                lambda: officeholder_guard.review_response(rewritten, verification),
            )
            if res is not None:
                r, demotions = res
                if demotions:
                    rewritten = r
                    changes.append(("officeholder_guard", len(demotions)))
                details["officeholder_guard"] = {"demotions": len(demotions)}
        except Exception as e:
            logger.debug("R-F448 officeholder_guard import/call failed: %s", e)
            skipped.append("officeholder_guard:import")

        # 2. citation_injector — auto-cite claims that match a URL in
        # the tool_context. Sync call.
        try:
            from . import response_verifier as _rv_inj
            res = await _guard_with_timeout(
                "citation_injector",
                lambda: _rv_inj.inject_inline_citations(
                    response_text=rewritten,
                    tool_context=tool_context or "",
                ),
            )
            if res is not None and not res.get("unchanged"):
                rewritten = res["rewritten"]
                changes.append(("citation_injector", int(res.get("claims_cited") or 0)))
                details["citation_injector"] = {
                    "claims_total":     res.get("claims_total"),
                    "claims_cited":     res.get("claims_cited"),
                    "claims_uncitable": res.get("claims_uncitable"),
                }
        except Exception as e:
            logger.debug("R-F448 citation_injector import/call failed: %s", e)
            skipped.append("citation_injector:import")

        # 3. response_verifier — inline [VERIFIED]/[UNVERIFIED]/
        # [CONTRADICTED] tags on every confidence-tagged claim. Async call.
        try:
            from . import response_verifier as _rv
            res = await _guard_with_timeout(
                "response_verifier",
                _rv.verify_and_tag_response(
                    response_text=rewritten,
                    tool_context=tool_context or "",
                    session_id=session_id,
                ),
            )
            if res is not None and not res.get("unchanged"):
                rewritten = res["tagged"]
                unv = int(res.get("unverified") or 0) + int(res.get("contradicted") or 0)
                changes.append(("response_verifier", unv))
                details["response_verifier"] = {
                    "claims_checked": res.get("claims_checked"),
                    "verified":       res.get("verified"),
                    "unverified":     res.get("unverified"),
                    "contradicted":   res.get("contradicted"),
                }
        except Exception as e:
            logger.debug("R-F448 response_verifier import/call failed: %s", e)
            skipped.append("response_verifier:import")

        # 4. commitment_guard — Clause 20 enforcement: detect and rewrite
        # fabricated "Within 24 hours I will…" commitments. Sync call.
        try:
            from . import commitment_guard
            res = await _guard_with_timeout(
                "commitment_guard",
                lambda: commitment_guard.guard_commitments(rewritten),
            )
            if res is not None and res.get("changed"):
                rewritten = res["guarded"]
                v = int(res.get("violations_found") or 0)
                changes.append(("commitment_guard", v))
                details["commitment_guard"] = {"violations": v}
                if write_to_mistake_ledger and v:
                    await _record_mistakes(
                        category="fabrication", task_type="chat", domain="clause_20",
                        items=res.get("violations") or [],
                        what_fn=lambda x: f"Fabricated commitment: {x.get('pattern_type', '?')}",
                        why_fn=lambda x: f"Match: {(x.get('match') or '')[:150]}",
                        fix="Clause 20 correction appended inline (stream path R-F448)",
                    )
        except Exception as e:
            logger.debug("R-F448 commitment_guard import/call failed: %s", e)
            skipped.append("commitment_guard:import")

        # 5. tool_claim_guard — Clause 20(f): detect present-tense tool
        # claims when no tool actually fired this turn. Async call.
        try:
            from . import tool_claim_guard
            res = await _guard_with_timeout(
                "tool_claim_guard",
                tool_claim_guard.guard(
                    rewritten,
                    tool_used=tool_used,
                    user_message=user_message,
                    user_id=user_id or "",
                    chat_id=chat_id or "",
                ),
            )
            if res is not None and res.get("changed"):
                rewritten = res["guarded"]
                v = int(res.get("violations_found") or 0)
                changes.append(("tool_claim_guard", v))
                details["tool_claim_guard"] = {"violations": v}
                if write_to_mistake_ledger and v:
                    await _record_mistakes(
                        category="hallucination", task_type="chat", domain="tool_fabrication",
                        items=res.get("violations") or [],
                        what_fn=lambda x: f"Fabricated tool claim: {x.get('pattern_type', '?')}",
                        why_fn=lambda x: (
                            f"Match: {(x.get('match') or '')[:150]} (no tool fired, "
                            f"pending_action={x.get('action_id', '?')})"
                        ),
                        fix="Clause 20(f) correction appended inline + pending_action (stream path R-F448)",
                        what_class="tool_fabrication",
                    )
        except Exception as e:
            logger.debug("R-F448 tool_claim_guard import/call failed: %s", e)
            skipped.append("tool_claim_guard:import")

        # 6. propaganda_guard — Clause 13: tag uncited current-events
        # claims and downgrade propaganda-tier sources. Sync call.
        try:
            from . import propaganda_guard
            res = await _guard_with_timeout(
                "propaganda_guard",
                lambda: propaganda_guard.guard(rewritten),
            )
            if res is not None and not res.get("unchanged"):
                rewritten = res["rewritten"]
                downgrades = int(res.get("propaganda_downgrades") or 0)
                uncited = int(res.get("current_uncited") or 0)
                changes.append(("propaganda_guard", uncited + downgrades))
                details["propaganda_guard"] = {
                    "current_uncited": uncited,
                    "propaganda_downgrades": downgrades,
                }
                if write_to_mistake_ledger and downgrades:
                    try:
                        from . import mistake_ledger
                        await mistake_ledger.record(
                            category="fabrication", task_type="chat", domain="clause_13",
                            what=(
                                f"LLM cited TIER-D propaganda source with "
                                f"high-confidence tag (stream path R-F448)"
                            ),
                            why=f"Tags downgraded: {(res.get('tags_added') or [])[:3]}",
                            fix="propaganda_guard rewrote tag to ASSESSED + named channel",
                            what_class="propaganda_elevation",
                            severity="HIGH",
                        )
                    except Exception as _mle:
                        logger.debug("R-F448 mistake_ledger record failed: %s", _mle)
        except Exception as e:
            logger.debug("R-F448 propaganda_guard import/call failed: %s", e)
            skipped.append("propaganda_guard:import")

        # 7. ground_truth_guard — verify CONFIRMED jurisdiction/HQ/etc.
        # claims against tool_context. Async call.
        try:
            from . import ground_truth_guard
            res = await _guard_with_timeout(
                "ground_truth_guard",
                ground_truth_guard.verify(
                    rewritten,
                    tool_context=tool_context or "",
                    user_message=user_message,
                ),
            )
            if res is not None and res.get("changed"):
                rewritten = res["guarded"]
                v = int(res.get("violations_found") or 0)
                changes.append(("ground_truth_guard", v))
                details["ground_truth_guard"] = {"violations": v}
                if write_to_mistake_ledger and v:
                    await _record_mistakes(
                        category="hallucination", task_type="chat", domain="ground_truth_violation",
                        items=res.get("violations") or [],
                        what_fn=lambda x: (
                            f"Unverifiable {x.get('slot', '?')} claim: "
                            f"{(x.get('claim') or '')[:100]}"
                        ),
                        why_fn=lambda x: (
                            f"extract actually mentions: {', '.join(x.get('extract_has_contradictory') or [])}"
                            if x.get("extract_has_contradictory")
                            else "not in extract"
                        ),
                        fix="Inline UNVERIFIED correction (stream path R-F448)",
                        what_class_fn=lambda x: f"gtg_{x.get('slot', '?')}",
                    )
        except Exception as e:
            logger.debug("R-F448 ground_truth_guard import/call failed: %s", e)
            skipped.append("ground_truth_guard:import")

    # Total timeout. If the whole chain runs slow, give up and return
    # what we have. Stream never blocks indefinitely.
    try:
        await asyncio.wait_for(_run_all(), timeout=_TOTAL_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning("R-F448 honesty pass total timeout (%ss)", _TOTAL_TIMEOUT_S)
        skipped.append("chain:total_timeout")

    return {
        "rewritten": rewritten,
        "changed": rewritten != response_text,
        "changes": changes,
        "details": details,
        "skipped": skipped,
    }


async def _record_mistakes(
    *,
    category: str,
    task_type: str,
    domain: str,
    items: list[dict],
    what_fn,
    why_fn,
    fix: str,
    what_class: str | None = None,
    what_class_fn=None,
) -> None:
    """Write up to 3 mistake-ledger entries from a guard's violation list.

    Best-effort. Errors swallowed and logged at debug. The /chat
    non-stream path caps at 3 entries per guard call — R-F448 mirrors
    that exactly so stream traffic doesn't double-fill the ledger.
    """
    try:
        from . import mistake_ledger
        for v in items[:3]:
            wc = what_class
            if what_class_fn:
                try:
                    wc = what_class_fn(v)
                except Exception:
                    wc = None
            await mistake_ledger.record(
                category=category,
                task_type=task_type,
                domain=domain,
                what=what_fn(v),
                why=why_fn(v),
                fix=fix,
                **({"what_class": wc} if wc else {}),
            )
    except Exception as e:
        logger.debug("R-F448 _record_mistakes failed: %s", e)


def build_correction_banner(result: dict) -> str | None:
    """Build a user-facing banner showing what guards rewrote.

    Called by the stream route when `result["changed"]` is True. Emits
    a structured block followed by the corrected text so WhatsApp
    users get a clean revised version bundled with the original.
    Returns None when nothing changed.
    """
    if not result.get("changed"):
        return None
    changes = result.get("changes") or []
    rewritten = result.get("rewritten") or ""
    if not changes or not rewritten:
        return None

    lines = [f"  • {name}: {count}" for name, count in changes]
    header = (
        "\n\n──────────\n"
        "🛡 **Honesty note** (R-F448 output guards):\n"
        + "\n".join(lines)
    )

    # R-F1713 — only re-print the FULL revised answer when a guard SUBSTANTIVELY
    # rewrote content (removed a fabricated commitment / tool claim / officeholder
    # error). For tag-only passes (response_verifier / citation / propaganda just
    # ANNOTATE claims with [UNVERIFIED]/[PENDING CORROBORATION]), re-printing the
    # entire response made the user read the whole thing twice — operator-reported
    # on the NDA review. Those get a concise note instead of a full re-print.
    _SUBSTANTIVE_GUARDS = {"commitment_guard", "tool_claim_guard", "officeholder_guard"}
    substantive = any((name in _SUBSTANTIVE_GUARDS) and count for name, count in changes)

    if not substantive:
        return (
            header
            + "\n\nSome claims above are flagged inline as [UNVERIFIED] / "
            "[PENDING CORROBORATION] — they could not be independently corroborated. "
            "Treat them as unconfirmed and verify before relying on them.\n──────────"
        )

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="stream_honesty",
                     summary="stream_honesty module active",
                     source_id="stream_honesty:init")
    except Exception:
        try:
            wire_failure(module="stream_honesty", detail="module init failed",
                        gap_type="engine_failure", source="stream_honesty:init")
        except Exception:
            pass

    return header + "\n──────────\n\n**Revised response:**\n\n" + rewritten
