"""ARIA confidence-tagged reply foot
er.

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

from .engine_wiring import wire_success, wire_failure
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
#
# R-F765 (2026-05-20) — also matches `[CONFIRMED from ATTACHED DOCUMENT]`
# and `[ASSESSED via OFSI lookup]` patterns where the separator between
# the tag word and the caveat is whitespace + a preposition ("from", "via",
# "based on") rather than the em-dash. Live transcript 2026-05-20 found
# a turn whose body had [CONFIRMED from ATTACHED DOCUMENT] inline but
# the footer headline still read "(no tag — treat as unverified)" —
# the regex missed the space-separated caveat. The new optional-group
# matches whitespace + ANY non-`]` continuation, NOT just dash-prefixed.
# Backwards-compatible — every previously-matched form (no caveat,
# em-dash caveat, en-dash caveat, hyphen caveat) still matches.
_TAG_RE = re.compile(
    r"\[(CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN|SPECULATIVE)"
    r"(?:\s+[^\]]*)?\]",
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
    """Return the frequency-weighted dominant confidence tag.

    Evolution of this function:

    V1 (pre-Phase-3): returned the STRONGEST tag. Oversold confidence
    when one CONFIRMED section was mixed with UNCERTAIN gaps. Caused
    Modirum / ARK-SER-01 incidents where headline said 95% but body
    had critical UNCERTAIN caveats.

    V2 (2026-04-09): returned the WEAKEST tag. Corrected oversell but
    over-corrected: one minor ASSESSED caveat in a 20-CONFIRMED
    response dragged the headline to 60%. Caused the Hanwha Redback
    research (2026-04-11) to report "Confidence: 60% [ASSESSED]"
    despite 8 grounded citations and 3 CONFIRMED sections.

    V3 (current): frequency-weighted average. Each tag instance
    contributes its confidence value; the arithmetic mean is mapped
    back to the nearest tag level. Also penalised by a floor: if any
    SPECULATIVE/UNCERTAIN tag is present the result cannot exceed
    PROBABLE — a serious gap must still be visible in the headline.
    """
    if not response_text:
        return None
    # Match all tags (not just unique — frequency matters).
    all_tags = [t.upper() for t in _TAG_RE.findall(response_text)]
    if not all_tags:
        return None

    # Weighted average of confidence values
    total = sum(_TAG_TO_CONFIDENCE.get(t, 0.5) for t in all_tags)
    avg = total / len(all_tags)

    # Hard floor: if any SPECULATIVE / UNCERTAIN tag is present, the
    # headline cannot exceed UNCERTAIN. A serious gap (data we do NOT
    # have) must be reflected in the headline — otherwise the reader
    # sees a confident-looking banner on top of a reply that actually
    # admits it doesn't know something. V2 floored too aggressively
    # (any ASSESSED → WEAKEST); V3 over-corrected by keeping UNCERTAIN
    # near ASSESSED. The correct rule: ASSESSED is normal rigour and
    # averages in; UNCERTAIN is a named data gap and hard-floors here.
    present = set(all_tags)
    if "SPECULATIVE" in present:
        avg = min(avg, _TAG_TO_CONFIDENCE["UNCERTAIN"])
    elif "UNCERTAIN" in present:
        avg = min(avg, _TAG_TO_CONFIDENCE["UNCERTAIN"])

    # Map the weighted confidence back to the nearest tag level.
    # Nearest-threshold by absolute distance.
    best_tag = None
    best_dist = 99.0
    for tag, conf in _TAG_TO_CONFIDENCE.items():
        d = abs(avg - conf)
        if d < best_dist:
            best_dist = d
            best_tag = tag
    return best_tag


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
    *,
    tools_used: list[str] | str | None = None,
    build_rev: str | None = None,
    trace_id: str | None = None,
    document_grounded: bool = False,
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
    tools_used:
        R-F403-tactical (2026-05-13): name(s) of tools that fired in the
        current turn (e.g. "sanctions_screen", "deep_research",
        "self_introspect"). Surfaces in the footer as a "Tools:" line so
        the team can see ARIA actually USED a tool — closes the "did she
        check or just guess" question that hurt team trust at MVP launch.
        Accepts a list of strings, a single string, or None/empty.
    build_rev:
        R-F403-tactical: the live ARIA_BUILD_REV slug. Helps the team
        know which deploy produced the answer (single-line truncated to
        the R-number marker for brevity).
    trace_id:
        R-F403-tactical: the trace_stream UUID for this turn. Operator
        can pull the full DD lifecycle via /api/aria/trace/{trace_id}.
        Surfaces as a short prefix so any team member can quote it back
        for follow-up.
    """
    if not is_enabled():
        return ""
    if not response_text:
        return ""

    tag = _dominant_tag(response_text)
    has_verification = bool(verification)
    has_rag = rag_sources_count > 0

    # R-F544 (2026-05-15, renumbered from R-F536 — R-F534 collision) — signal-aware short-reply guard. Pre-R-F544
    # we blanket-skipped any reply <80 chars to avoid decorating
    # greetings. But a substantive 1-line DD verdict like "The director
    # is Joe Bloggs [PROBABLE — single Companies House filing]." (71
    # chars) deserves a footer; the [PROBABLE] tag is meaningful. Skip
    # only when there's truly nothing to surface: no tag, no
    # verification, no RAG hit, no tools attribution, no R-F401
    # violation. The full _violations scan happens below; for the
    # short-reply path we use a cheap signal-presence check.
    if len(response_text) < 80:
        _short_has_signal = bool(
            tag or has_verification or has_rag
            or tools_used or build_rev or trace_id
        )
        if not _short_has_signal:
            return ""

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
        tool_refs = int(v.get("tool_refs", 0) or 0)
        grounded = cited - unverified
        # R-F1168: when the source verifier itself says "grounded" (which
        # happens when tool_refs > 0 — clause-15 markers from crawl_website,
        # deep_research, etc.), don't override it. The verifier already
        # checked that the response cites real tool output. Demoting a
        # verifier-grounded response to ASSESSED is a false negative that
        # makes ARIA look less reliable than she is.
        if verdict == "grounded" and tool_refs > 0:
            pass  # verifier says grounded — trust it
        elif verdict in ("no_tool", "no_citations") or grounded <= 0:
            logger.debug(
                "Demoting footer tag %s -> ASSESSED (verdict=%s grounded=%d)",
                tag, verdict, grounded,
            )
            tag = "ASSESSED"

    # GROUNDING BONUS: when verification passed with 100% grounded rate
    # AND the body has ≥3 citations, promote one tier. Reward for
    # actually citing every claim. Hanwha Redback (2026-04-11) had 8
    # grounded / 0 unverified (100%) but the weakest-tag rule still
    # reported 60% ASSESSED — the weighted-average fix above brings
    # it to ~85%, and this bonus takes the 3-CONFIRMED + 1-ASSESSED
    # case all the way to CONFIRMED where it belongs.
    if has_verification and tag in ("ASSESSED", "PROBABLE"):
        v = verification or {}
        verdict = str(v.get("verdict") or "").lower()
        rate = v.get("grounded_rate")
        cited = int(v.get("cited", 0) or 0)
        if (
            verdict == "grounded"
            and isinstance(rate, (int, float))
            and float(rate) >= 0.99
            and cited >= 3
        ):
            promoted = {"ASSESSED": "PROBABLE", "PROBABLE": "CONFIRMED"}[tag]
            logger.debug(
                "Grounding bonus: promoting %s -> %s (cited=%d rate=%.2f)",
                tag, promoted, cited, rate,
            )
            tag = promoted

    # R-F544 (2026-05-15, renumbered from R-F536 — R-F534 collision) — pre-compute the R-F401 self-claim guard
    # violations once and reuse below. Pre-R-F544 this scan ran only
    # inside the late R-F407 block, AFTER a "nothing to say"
    # early-return that dropped the entire footer when verification=None
    # and the body had no inline [CONFIRMED] tag. Net effect: an R-F401
    # invariant violation like "ARIA has 18-month TTL on facts" passed
    # silently through the most common chat surface. The fix is to (a)
    # scan once up here, and (b) drop the early-return entirely — every
    # substantive response (>80 chars, gated above) now gets a footer
    # with at least the "Tools: (none — from memory / training)"
    # honesty line, even if no tool, verification, or RAG hit fired.
    # Live evidence: 7 R-F403 tests failing pre-R-F536 with `'*Tools:*'
    # in ''` and `R-F403 CRITICAL: response contained 18-month TTL
    # hallucination but the R-F401 guard block didn't appear in the
    # footer`.
    _violations = []
    _had_introspect = False
    _web_tool_ran = False
    if tools_used:
        # Normalise to a single lowercase string for keyword checks.
        _tools_str = (tools_used.lower() if isinstance(tools_used, str)
                      else " ".join(str(t).lower() for t in tools_used))
        _had_introspect = "self_introspect" in _tools_str
        # R-F890 — did a web/search tool actually fire this turn? Gates the
        # fabricated-external-verification check (BLOCK if no web tool ran).
        _web_tool_ran = any(t in _tools_str for t in (
            "deep_research", "web_search", "search", "investigate", "crawl",
            "brave", "web_explorer", "researcher",
        ))
    try:
        from .self_claim_guard import scan_response as _pre_scan
        _violations = _pre_scan(
            response_text, self_introspect_ran=_had_introspect,
            web_tool_ran=_web_tool_ran,
        ) or []
    except Exception:
        _violations = []

    lines: list[str] = ["", "─────"]

    # ── Headline: confidence percentage + dominant tag ──
    if tag:
        pct = int(round(_TAG_TO_CONFIDENCE[tag] * 100))
        head = f"*Confidence:* {pct}% [{tag}]"
    else:
        head = "*Confidence:* (no tag — treat as unverified)"

    # ── Sources / verification line ──
    if document_grounded:
        # R-F965 — the answer is grounded in an attached document. The
        # verification dict counts EXTERNAL source citations, which don't apply
        # to a document the operator handed us; reporting "0 grounded / NO_TOOL"
        # read as "ungrounded / from memory" and undersold a full document review
        # (live 2026-05-28 contract review). Lead with the document as the
        # grounding; only add external counts if she ALSO cited external sources.
        # R-F1384 — when grounded_rate=0.0 and sources=0, say "Reviewing attached
        # document" instead of claiming it's grounded (honest footer).
        if has_verification:
            v = verification or {}
            cited = int(v.get("cited", 0) or 0)
            unverified = int(v.get("unverified", 0) or 0)
            grounded = cited - unverified
            rate = v.get("grounded_rate")
            if isinstance(rate, (int, float)) and float(rate) <= 0.0 and grounded <= 0:
                head += "  ·  *Reviewing attached document*"
            else:
                head += "  ·  *Grounded in:* attached document"
                if cited > 0:
                    head += f" + {cited - unverified} external grounded / {unverified} unverified"
        else:
            head += "  ·  *Reviewing attached document*"
    elif has_verification:
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

    # ── R-F403-tactical (2026-05-13): proof line ──
    # Surfaces tools + build + trace_id so the team can SEE that ARIA
    # actually ran something, on which deploy, and how to inspect it.
    # The "did she check or just guess" question is the single biggest
    # MVP-launch trust problem; this line answers it inline on every
    # substantive reply.
    proof_bits: list[str] = []
    _tools_ran = False
    if tools_used:
        if isinstance(tools_used, str):
            tools_list = [t.strip() for t in tools_used.replace(",", " ").split() if t.strip()]
        else:
            tools_list = [str(t).strip() for t in tools_used if str(t).strip()]
        # Dedupe + cap to keep the line short on WhatsApp
        seen_tools: set[str] = set()
        unique_tools: list[str] = []
        for t in tools_list:
            if t.lower() in seen_tools:
                continue
            seen_tools.add(t.lower())
            unique_tools.append(t)
        if unique_tools:
            _tools_ran = True
            # R-F1170 — human-readable tool names
            _tool_labels = {
                "crawl_website": "website crawl",
                "deep_research": "deep web search",
                "web_search": "web search",
                "sanctions_screen": "sanctions screening",
                "dd_orchestrate": "due diligence",
                "investigate": "investigation",
                "extract_url_deep": "page extraction",
                "read_article": "article read",
                "brave_answer": "web lookup",
            }
            _readable = []
            for t in unique_tools[:5]:
                _readable.append(_tool_labels.get(t.lower(), t))
            proof_bits.append(f"*Tools:* {' · '.join(_readable)}")
        elif document_grounded:
            # R-F885 — the answer was grounded in an attached document, not a
            # tool. Saying "from memory / training" here is a flat-out
            # mis-label (live 2026-05-25: a full document-grounded contract
            # review footer read "from memory / training · 0 grounded").
            proof_bits.append("*Source:* attached document (grounded)")
        else:
            # Explicitly say no tool when none ran — operator's "did she
            # actually check" question gets an honest "no" instead of silence.
            proof_bits.append("*Tools:* (none — from memory / training)")
    elif document_grounded:
        # R-F885 — no tool fired, but the reply quotes an attached document.
        proof_bits.append("*Source:* attached document (grounded)")
    else:
        proof_bits.append("*Tools:* (none — from memory / training)")
    if build_rev:
        # Keep only the leading R-number cluster for brevity.
        br = str(build_rev).strip()
        rnum = re.match(r"(R-F\d+(?:\.\.F\d+|\+\d+)?)", br)
        if rnum:
            br = rnum.group(1)
        proof_bits.append(f"*Build:* {br}")
    if trace_id:
        # R-F4364 (C-310) — PRINT THE WHOLE ID; A PREFIX IDENTIFIES NOTHING.
        #
        # This rendered `tid[:8]`. Trace ids are `tr_{ms}_{uuid6}`
        # (trace_stream.py:132), and in this era `int(time.time()*1000)` begins
        # `17877` — so every answer for roughly three years printed the SAME
        # token. Measured from the operator's live WhatsApp transcript: Estonia
        # country risk, a Lagos security assessment and "what about DD?" all
        # carried `tr_17877`, hours apart.
        #
        # The entropy is in the SUFFIX; truncating from the front keeps only the
        # part every id shares. And this docstring promises the reader can "pull
        # the full DD lifecycle via /api/aria/trace/{trace_id}", which
        # `trace_stream.get_trace` resolves by EXACT key — so a shortened token
        # is unresolvable by construction. A token that looks actionable and is
        # not is worse than printing none: when a bad answer is reported there is
        # no way back to its lifecycle.
        tid = str(trace_id).strip()
        if tid:
            proof_bits.append(f"*Trace:* `{tid}`")
    if proof_bits:
        lines.append("  ·  ".join(proof_bits))

    # R-F1170 — proactive next-step suggestion when tools ran
    # After delivering intelligence, offer a natural next action so the
    # conversation keeps moving forward instead of ending at the footer.
    if _tools_ran and tag and tag not in ("UNCERTAIN", "SPECULATIVE"):
        _next_steps = []
        if any(t in str(tools_used or "").lower() for t in ("crawl", "research", "investigate")):
            _next_steps.append("Want me to check sanctions or corporate registries on this entity?")
        if any(t in str(tools_used or "").lower() for t in ("sanctions", "screen")):
            _next_steps.append("Want a full due diligence report? Just say /dd [entity name].")
        if _next_steps:
            lines.append("")
            lines.append(f"💡 {_next_steps[0]}")

    # ── Assumptions block (only if the LLM flagged any) ──
    assumptions = _extract_assumptions(response_text)
    if assumptions:
        lines.append("⚠ *Assumptions to validate:*")
        for a in assumptions:
            lines.append(f"  • {a}")

    # ── R-F403-tactical / R-F919: post-response hallucination scan ──
    # Run the R-F401 self_claim_guard on the FINAL response. The guard's
    # internal block (pattern ids, "[R-F401 SELF-CLAIM GUARD …]", "call
    # self_introspect and cite real numbers") is OPERATOR/team scaffolding —
    # it must NEVER reach an end user. Live incident 2026-05-26: that block
    # leaked into a WhatsApp answer mid-demo. R-F919 stops appending it to the
    # user-facing footer; the team still sees every trip via the WARNING log,
    # the R-F407 Redis metrics, and (R-F921) a brain self_monitor signal.
    # When a BLOCK-severity violation fires we add a SHORT, user-appropriate
    # honesty caveat instead of the raw guard dump.
    # R-F407 (2026-05-13): fire-and-forget records the violation to Redis
    # counters so the operator dashboard panel can show 24h totals.
    try:
        from .self_claim_guard import (
            record_violations, record_turn_observed,
        )
        # R-F544 (2026-05-15, renumbered from R-F536 — R-F534 collision) — reuse the violations already computed by
        # the early-return guard above. Pre-R-F544 this block re-ran
        # scan_response, which (a) was wasted work and (b) ran AFTER the
        # nothing-to-say return, so violations on tag-less / no-verify
        # responses were silently dropped.
        violations = _violations
        if violations:
            _block_hits = [v for v in violations if getattr(v, "severity", "") == "BLOCK"]
            logger.warning(
                "R-F401 guard fired: %d violation(s) (%d BLOCK) on response (trace=%s) — "
                "NOT leaked to user (R-F919): %s",
                len(violations), len(_block_hits), trace_id or "n/a",
                "; ".join(f"{v.pattern_id}:{v.phrase!r}" for v in violations[:4]),
            )
            # R-F919 — user-facing honesty caveat for BLOCK severity only.
            # Never the internal guard text; just a confidence signal.
            if _block_hits:
                lines.append(
                    "\n_⚠ One or more statements about my own system in this "
                    "answer couldn't be self-verified — treat them as unconfirmed._"
                )
            # R-F921 — wire the guard trip to ARIA's brain so she SEES her own
            # hallucination-guard events and can reason about / fix the cause.
            try:
                import asyncio as _aio921
                from . import brain_hook as _bh921
                _loop921 = _aio921.get_event_loop()
                if _loop921.is_running():
                    _loop921.create_task(_bh921.observe_self_event(
                        "self_claim_guard_trip",
                        {"violations": [v.pattern_id for v in violations],
                         "block": len(_block_hits), "trace": trace_id or ""},
                        gap_type="hallucination_guard",
                    ))
            except Exception:
                pass
        # R-F407: fire-and-forget metrics. Run inside asyncio.create_task
        # to avoid making build_footer async — keeps the call site simple.
        import asyncio as _aio
        try:
            _loop = _aio.get_event_loop()
            if _loop.is_running():
                _loop.create_task(record_turn_observed())
                if violations:
                    _loop.create_task(
                        record_violations(
                            violations,
                            response_preview=(response_text or "")[:200],
                            trace_id=trace_id or "",
                        )
                    )
        except Exception as _e:
            logger.debug("self_claim_guard metrics fire-and-forget failed: %s", _e)
    except Exception as e:
        logger.debug("self_claim_guard scan failed (non-fatal): %s", e)


    # R-F996 — wire to brain
    wire_success(
        module="confidence_footer",
        summary="Confidence footer",
        source_id="confidence_footer:R-F996",
    )
    return "\n".join(lines)

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
