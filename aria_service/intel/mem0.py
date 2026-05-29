"""ARIA MEM0 — personal notebook auto-summariser.

The mental-module spec from Antonio (2026-04-09):

    TIERS = university education (corpus_registry, RAG, structured ingests)
    WEB SEARCH = daily newspaper (sweep cycle, telegram sources, crawl tools)
    AUTONOMOUS RESEARCH = her own investigations (proactive, scheduled tasks)
    MEM0 = her personal notebook (grows with every conversation)
    OUTCOME TRACKING = professional development (feedback, eval, mastery)

Of those five layers, four had clear implementations already:
  TIERS               -> corpus_registry.yaml + ingest_tier_a.py + chromadb
  WEB SEARCH          -> apis/sources/* + sweep cycle + extract_url
  AUTONOMOUS RESEARCH -> research_tasks + proactive (env-gated during freeze)
  OUTCOME TRACKING    -> feedback + eval + source_verifier + honesty_judge

MEM0 was the missing piece. ARIA had:
  - knowledge.py (facts learned via /teach + correction_learner)
  - neural_memory.py (concept neurons + activation)
  - reasoning_library.py (cached Q-A pairs for distillation)
  - training_data.py (conversation log for fine-tuning)

But none of these are a "personal notebook" — a per-turn summary of
what the conversation revealed that she should remember and surface
on future related queries. Knowledge facts are too fine-grained,
neural neurons are too abstract, reasoning library is for query
matching not recall, training data is write-only.

What MEM0 does
══════════════
On every substantive chat reply, fire a small background LLM call
that asks: "In ONE concise sentence, what new fact, decision, or
commitment from this conversation should be remembered for future
turns? Reply 'NONE' if there's nothing worth retaining."

If the LLM returns a sentence (not "NONE"), it gets stored as a
knowledge fact with source `mem0:session_<id>:<ts>`. The existing
knowledge.search_knowledge() retrieval automatically picks it up
on relevant future queries via the standard chat context layer.

Why a separate module instead of extending knowledge.py
═══════════════════════════════════════════════════════
- Different LLM call (small / fast / cheap, not the full chat model)
- Different gating (only fires on substantive replies, not trivial ones)
- Different metadata (session_id, turn number, source role)
- Behind ARIA_MEM0_ENABLED env var so it can be disabled standalone
- Cost-tracked separately under feature='mem0' for visibility

RETENTION (R-F406, 2026-05-13) — PERMANENT, no eviction
═══════════════════════════════════════════════════════
This module DOES NOT EVICT, OVERWRITE, COMPRESS, OR PRUNE entries.
Storage is delegated to knowledge.store_fact() with a `mem0:session_*`
source prefix; retrieval is a filtered knowledge.search() over the
same prefix. Mem0 entries therefore inherit knowledge's permanent-
memory policy (knowledge.py:64 — "no TTL, no eviction"; R-F238
reversed R-F173 prune to enforce this).

Past hallucination 2026-05-13 07:27 (WhatsApp to operator): ARIA stated
"MEM0 Notebook ... each new entry can overwrite/compress older ones."
That claim is FALSE. There is no overwrite logic, no compression logic,
no eviction logic in this module. The retrieval-side caps
(_MAX_MEM0_RECALL_CHARS, _MAX_MEM0_RECALL_FACTS at lines 297-298) are
WHAT-TO-INJECT limits per query — they do not delete anything from
the store. Adding eviction would violate operator directive
aria_infinite_memory.md. See R-F406 capability test for the pin.

Cost discipline
═══════════════
Skip the summariser when:
- The reply is shorter than 200 chars (trivial / refusal / error)
- The reply contains a `[TOOL: ... — FETCH/EXTRACTION FAILED]` block
  (no point summarising a failure, the failure itself is the lesson)
- The reply starts with "I cannot" or "I refuse" or similar refusals
  per clauses 9, 12, 14 (the refusal is correct behaviour and we
  don't want the notebook to encourage future refusals on similar
  prompts)
- The MEM0 feature flag is off

Behind ARIA_MEM0_ENABLED env var (default ON).
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import TYPE_CHECKING

logger = logging.getLogger("aria.mem0")

if TYPE_CHECKING:
    from ..llm.provider import LLMProvider


# ── Feature flag ────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    """Default ON. Set ARIA_MEM0_ENABLED=0 to disable."""
    val = os.getenv("ARIA_MEM0_ENABLED", "1") or "1"
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="mem0",
        summary="Is Enabled",
        source_id="mem0:R-F1001",
    )

    return val.strip().lower() not in ("0", "false", "no", "off")


# ── Eligibility filter ──────────────────────────────────────────────────────

_REFUSAL_PREFIXES = (
    "i cannot",
    "i can't",
    "i can not",
    "i refuse",
    "i must refuse",
    "i must decline",
    "i'm unable",
    "i am unable",
    "i don't have",
    "i do not have",
    "i lack",
    "no usable data",
    "insufficient data",
    "i could not access",
    "i could not retrieve",
)


def _looks_like_refusal(response: str) -> bool:
    if not response:
        return True
    head = response.lstrip("*_ -•\n").lower()[:200]
    if any(head.startswith(p) for p in _REFUSAL_PREFIXES):
        return True
    # Tool extraction failure markers (clause 9 / 12 / 14 enforcement)
    if "[TOOL:" in response and "FETCH/EXTRACTION FAILED" in response:
        return True
    if "PARSE FAILED" in response and "ATTACHED DOCUMENT" in response:
        return True
    return False


def _is_substantive(response: str) -> bool:
    if not response or len(response) < 200:
        return False
    if _looks_like_refusal(response):
        return False
    return True


# ── Summarisation prompt ────────────────────────────────────────────────────

_SUMMARISER_SYSTEM = (
    "You are MEM0, ARIA's personal-notebook subsystem. You read a single "
    "conversation turn and decide whether anything in it should be "
    "remembered for future turns."
)

_SUMMARISER_USER_TEMPLATE = """Below is one user message and ARIA's reply.

USER ({user_label}): {user_message}

ARIA REPLY: {response}

In ONE concise sentence (max 200 chars), state the SINGLE most important new fact, decision, commitment, or working assumption from this turn that ARIA should remember for future related queries. The fact should be:
- Concrete and specific (a name, a number, a relationship, a deal stage, a constraint, a counterparty preference)
- Useful for future retrieval (something a future query might benefit from knowing)
- NOT generic ("user is interested in defence" is useless)
- NOT an action item (action items go to a TODO list, not a notebook)
- NOT a refusal or error (those are not facts to remember)

If there is nothing worth retaining, reply with the single word: NONE

Examples of good summaries:
- "Antonio is preparing a Vision International ammunition RFQ response; end user is TBA which is a compliance blocker"
- "Modirum Gespi is an AI-powered defence systems integrator (NOT a consultancy as previously framed)"
- "Ghana defence minister identity is unconfirmed post-Dec-2024 election; Nitiwul is OBSOLETE"
- "Counterparty XYZ Trading prefers FOB terms over CIF for stock items"

Examples of NONE:
- A trivial greeting reply
- A refusal under constitution clauses 9/12/14
- A general explanation of compliance principles with no specific entity

Reply with ONLY the one-sentence summary or "NONE". No prefix, no quotes, no markdown."""


# ── Public: summarise + store ───────────────────────────────────────────────

async def summarise_and_store(
    user_message: str,
    aria_response: str,
    session_id: str,
    llm: "LLMProvider | None",
    user_label: str = "user",
) -> dict:
    """Run the MEM0 summariser on a turn and store the result as a knowledge fact.

    Fire-and-forget pattern: callers should NOT await this in the chat
    response path; spawn it as a background task. Failure is logged but
    never raised.

    Returns a dict with:
        ok: bool
        skipped: bool — True if not eligible (trivial, refusal, off)
        skipped_reason: str
        summary: str — the one-sentence fact stored, if any
        fact_id: str — the knowledge.add_fact id, if stored
        duration_ms: int
    """
    t0 = time.time()
    out = {
        "ok": False, "skipped": False, "skipped_reason": "",
        "summary": "", "fact_id": "", "duration_ms": 0,
    }

    if not is_enabled():
        out["skipped"] = True
        out["skipped_reason"] = "feature_flag_off"
        return out

    if not _is_substantive(aria_response):
        out["skipped"] = True
        out["skipped_reason"] = "not_substantive"
        return out

    if not llm or not getattr(llm, "is_configured", False):
        out["skipped"] = True
        out["skipped_reason"] = "no_llm"
        return out

    # Cap inputs so the summariser prompt stays small (cost discipline)
    user_msg_capped = (user_message or "")[:1500]
    response_capped = (aria_response or "")[:3000]

    prompt = _SUMMARISER_USER_TEMPLATE.format(
        user_label=user_label,
        user_message=user_msg_capped,
        response=response_capped,
    )

    try:
        # Attribute LLM cost to mem0 feature so /cost can show it separately
        from . import cost_tracker
        with cost_tracker.feature("mem0"):
            result = await llm.complete(
                _SUMMARISER_SYSTEM,
                prompt,
                max_tokens=100,
                timeout=20.0,
            )
        summary = (getattr(result, "text", "") or "").strip()
    except Exception as e:
        logger.debug("MEM0 summariser LLM call failed: %s", e)
        out["skipped"] = True
        out["skipped_reason"] = f"llm_error: {str(e)[:80]}"
        out["duration_ms"] = int((time.time() - t0) * 1000)
        return out

    # Strip common LLM wrapper patterns
    summary = summary.strip().strip('"').strip("'").strip()
    summary = re.sub(r"^(summary|fact|note):\s*", "", summary, flags=re.IGNORECASE)
    summary = summary[:300]  # hard cap

    if not summary or summary.upper() == "NONE" or len(summary) < 15:
        out["skipped"] = True
        out["skipped_reason"] = "summariser_returned_none"
        out["duration_ms"] = int((time.time() - t0) * 1000)
        return out

    # Persist as a knowledge fact with a distinctive source so it's
    # findable, separable, and cleanable
    try:
        from . import knowledge
        ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        source = f"mem0:session_{session_id[:32]}:{ts_iso}"
        # Topic = first 60 chars of the summary (used for keyword search)
        topic = summary[:60]
        result = await knowledge.store_fact(
            topic=topic,
            content=summary,
            source=source,
            confidence="ASSESSED",
        )
        out["ok"] = True
        out["summary"] = summary
        out["fact_id"] = (result or {}).get("fact_id", "")
    except Exception as e:
        logger.debug("MEM0 fact persist failed: %s", e)
        out["skipped"] = True
        out["skipped_reason"] = f"persist_error: {str(e)[:80]}"

    out["duration_ms"] = int((time.time() - t0) * 1000)
    return out


# ── Public: retrieval for chat-time prompt injection ────────────────────────
#
# Phase 3 cherry-pick from aria_research_architecture.py 2026-04-09:
# the proposal's run_aria_research() does mem0 retrieval BEFORE the LLM
# call so prior conversational facts can shape the next reply. ARIA already
# stores mem0 facts via knowledge.store_fact() with source `mem0:session_*`,
# and the chat-time `search_knowledge()` layer already retrieves them as
# part of the generic KNOWLEDGE BASE block. The problem with that path:
# the LLM cannot tell which facts came from a verified /teach call vs
# which came from a prior chat summary, which matters for confidence
# tagging.
#
# This function returns a SEPARATE context block tagged "MEM0 NOTEBOOK
# RECALL" so the LLM sees mem0 facts distinctly from KNOWLEDGE BASE
# facts. Same retrieval (still hits the same knowledge.facts cache) but
# filtered to mem0-tagged facts only and presented with explicit
# provenance. Used by aria_engine._build_7_layer_context() as a new
# layer slot.

# Conservative size cap so the recall block can never blow up the
# context window — even with hundreds of mem0 facts in the cache, only
# the top-N by keyword overlap make it into the prompt.
_MAX_MEM0_RECALL_CHARS = 1200
_MAX_MEM0_RECALL_FACTS = 6


def retrieve_for_query(query: str) -> str:
    """Pull mem0-stored notebook facts that look relevant to `query` and
    return them as a formatted prompt-injection block.

    Returns the empty string when:
      - The feature flag is off
      - The knowledge cache is empty / unloaded
      - No mem0 facts match the query
      - mem0 facts exist but none score above the keyword threshold

    The function is intentionally synchronous and side-effect free —
    same shape as `knowledge.search_knowledge()` so it can drop into the
    layer-fns list in aria_engine._build_7_layer_context() without any
    new async wiring.
    """
    if not is_enabled() or not query or not isinstance(query, str):
        return ""

    try:
        from . import knowledge
    except Exception:
        return ""

    cache = getattr(knowledge, "_cache", None)
    if not cache:
        return ""

    facts = cache.get("facts", []) if isinstance(cache, dict) else []
    if not facts:
        return ""

    # Word-overlap scorer mirroring search_knowledge() but filtered to
    # mem0-tagged facts only. Keep the scoring identical so behaviour is
    # predictable for the LLM consumer.
    words = [w.lower() for w in query.split() if len(w) > 2]
    if not words:
        return ""

    scored: list[tuple[float, dict]] = []
    for f in facts:
        source = (f.get("source") or "")
        if not source.startswith("mem0:"):
            continue
        text = f"{f.get('topic', '')} {f.get('content', '')}".lower()
        score = sum(3 for w in words if w in text)
        if score > 0:
            scored.append((score, f))

    if not scored:
        return ""

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:_MAX_MEM0_RECALL_FACTS]

    lines = ["\n[MEM0 NOTEBOOK RECALL — facts captured from prior conversations]"]
    total = len(lines[0])
    for _, f in top:
        # Trim source down to a short provenance marker so the LLM
        # sees "from a prior turn on date X" without leaking the full
        # session id
        source = f.get("source") or ""
        date_marker = ""
        # source format: "mem0:session_<id>:<iso8601>"
        if source.startswith("mem0:session_"):
            try:
                ts_part = source.split(":", 2)[2] if source.count(":") >= 2 else ""
                date_marker = ts_part[:10] if ts_part else ""
            except Exception:
                date_marker = ""
        prov = f" (mem0 {date_marker})" if date_marker else " (mem0)"
        line = f"- {f.get('content', '')[:200]}{prov}"
        if total + len(line) + 1 > _MAX_MEM0_RECALL_CHARS:
            break
        lines.append(line)
        total += len(line) + 1

    if len(lines) <= 1:
        return ""

    lines.append(
        "(These are notebook facts from past chats — useful for continuity but"
        " always re-verify with a tool before tagging [CONFIRMED])"
    )
    block = "\n".join(lines)

    # Quarantine filter (2026-04-17 21:45 fix). Scrubs citations of
    # DD runs known to be defective (e.g. tonight's dd_30477701e537 /
    # dd_adc7c7f87e4a / dd_07f45b072b9f which ran on malformed entity
    # "https"). Replaces the citation with [CITATION-QUARANTINED] so
    # the LLM sees the recall but knows the prior evidence cannot be
    # relied on.
    try:
        from . import run_quarantine
        block, quarantined_found = run_quarantine.filter_citations_sync(block)
        if quarantined_found:
            block += (
                f"\n\n⚠️ [QUARANTINE] {len(set(quarantined_found))} prior "
                f"DD run(s) cited in this recall were DEFECTIVE "
                f"(malformed entity names). Do NOT cite their findings "
                f"as evidence — re-run DD on the corrected entity name "
                f"if you need a verdict."
            )
    except Exception:
        # Quarantine module missing / Redis down — fail open (better to
        # show unfiltered recall than break the chat layer entirely).
        pass

    return block
