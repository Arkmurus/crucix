"""
ARIA Reasoning Router — the orchestrator that gradually replaces DeepSeek.

This is the BRAIN OF ARIA's REASONING. Every chat call routes through here
before touching any LLM. The router tries each reasoning source in order of
cost (cheapest first), stops at the first high-confidence answer, and only
escalates to a cloud LLM as the last resort.

Routing pipeline
════════════════
    User question
        ↓
    1. SYMBOLIC REASONER     — pure rules, instant, free, deterministic
        ↓ (no match)
    2. REASONING LIBRARY     — past Q→A pairs with semantic match
        ↓ (no match above threshold)
    3. LOCAL_BRAIN           — rule-based intent router for common queries
        ↓ (no match)
    4. LOCAL OLLAMA          — if installed, qwen2.5:7b / deepseek-r1-distill
        ↓ (unavailable or low confidence)
    5. CLOUD LLM             — DeepSeek (or fallback chain)
        ↓
    Response + DISTILLATION HOOK
        - If answered by 1, 2, 3 → no LLM cost, no data leak
        - If answered by 4 → ARIA's own local model
        - If answered by 5 → captured into the library for next time

Independence trajectory
═══════════════════════
On day 1 of using the router:
    Symbolic + library hit-rate: ~10%   (basic patterns + cold cache)
    Local Ollama hit-rate: 0%           (no model loaded yet)
    Cloud LLM hit-rate: 90%             (everything else)

After 100 conversations:
    Symbolic + library: ~30%            (cache warming up)
    Local Ollama: 20%                   (handles routine reasoning)
    Cloud LLM: 50%                      (only complex novel queries)

After 1000 conversations + ARIA-LLM v1 fine-tune:
    Symbolic + library: ~40%
    ARIA-LLM (local): 50%               (her own fine-tuned model)
    Cloud LLM: 10%                      (only true frontier reasoning)

The router is what makes that trajectory POSSIBLE — without it, every
query would hit the cloud forever.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Optional

from . import symbolic_reasoner
from . import reasoning_library
from . import local_brain
from . import redis_store as rs

logger = logging.getLogger("aria.reasoning_router")

ROUTER_STATS_KEY = "crucix:aria:router:stats"

# Confidence thresholds — below this we escalate to the next stage
SYMBOLIC_MIN_CONFIDENCE = 0.80
LIBRARY_MIN_CONFIDENCE = 0.78
LOCAL_BRAIN_MIN_CONFIDENCE = 0.65
OLLAMA_MIN_CONFIDENCE = 0.60

# 2026-04-25: Self-introspection bypass for the local reasoning chain.
# Mirrors `_SELF_INFRA_INTROSPECTION_RE` in aria_engine.py and
# `_BRAVE_QA_SELF_INFRA_RE` in routes/aria.py — keep all three in sync if
# extended. Background: 2026-04-24 OpenClaw incident proved that
# fabricated self-infra answers can be permanently absorbed into ANY of
# the local reasoning sources (mem0, knowledge facts, RAG, AND the
# reasoning library). After quarantining mem0/RAG/knowledge in
# aria_engine._build_7_layer_context (94a94b6), the SAME fabrication
# came back from the reasoning library cache (`Retrieved from ARIA's
# reasoning library, used 3x`). The router runs BEFORE _build_7_layer_context,
# so the layer-side guard never fires for cached answers.
#
# Fix: when the question is self-introspective, force escalation to the
# cloud LLM (skip stages 1-4). The cloud path then runs through
# _build_7_layer_context which has the absorbed-knowledge quarantine
# AND the [SELF-INFRA QUARANTINE] note that names "OpenClaw" /
# "openclaw doctor" / "Arkmurus platform" as forbidden tokens. Net cost:
# self-infra questions always hit cloud — but they're rare (operator
# troubleshooting, not routine) and accuracy matters more than cache hits.
_SELF_INFRA_INTROSPECTION_RE = re.compile(
    r"(?:why|what'?s)\s+(?:is|are|isn'?t|aren'?t|won'?t|can'?t|doesn'?t|"
    r"wrong\s+with|broken\s+(?:in|with))\s+"
    r"(?:my|our|this|the|you|aria|baileys|"
    r"(?:wa|whatsapp)[\s_-]?(?:listener|gateway|bridge)?|"
    r"(?:fly|seenode|backend|brain|chat|stream|sweep|deploy(?:ment)?|"
    r"stack|infra(?:structure)?|service|process|gateway|listener))\b",
    re.IGNORECASE,
)


# ── Stats tracking ──────────────────────────────────────────────────────────

_stats_cache: dict | None = None

async def _load_stats() -> dict:
    global _stats_cache
    if _stats_cache is not None:
        return _stats_cache
    raw = await rs.get_json(ROUTER_STATS_KEY)
    _stats_cache = raw if isinstance(raw, dict) else {
        "total_queries": 0,
        "by_source": {
            "symbolic_reasoner": 0,
            "reasoning_library": 0,
            "local_brain": 0,
            "local_ollama": 0,
            "cloud_llm": 0,
            "no_answer": 0,
        },
        "born": time.time(),
    }
    return _stats_cache

async def _save_stats() -> None:
    if _stats_cache is not None:
        await rs.set_json(ROUTER_STATS_KEY, _stats_cache, ex=30 * 86400)

async def _record_routing(source: str) -> None:
    stats = await _load_stats()
    stats["total_queries"] = stats.get("total_queries", 0) + 1
    by_source = stats.setdefault("by_source", {})
    by_source[source] = by_source.get(source, 0) + 1
    stats["last_query"] = time.time()
    await _save_stats()


# ── Public API ──────────────────────────────────────────────────────────────

async def try_local_reasoning(question: str) -> dict:
    """Try every LOCAL reasoning source. Returns the first confident answer
    or a {"answered": False} signal to escalate.

    This function does NOT call any cloud LLM. It is the gatekeeper that
    decides whether the cloud is even needed.
    """
    if not question or len(question.strip()) < 5:
        return {"answered": False, "reason": "empty query"}

    trace: list[dict] = []
    started = time.time()

    # ── Stage 0: Self-infra introspection bypass ──────────────────────────
    # Skip every local reasoning source for questions about the operator's
    # own deployment. Forces escalation to cloud LLM, which runs through
    # _build_7_layer_context's absorbed-knowledge quarantine. Prevents the
    # OpenClaw-class memory poisoning from re-surfacing from any cached
    # local source (reasoning_library especially — the reason for this fix).
    if _SELF_INFRA_INTROSPECTION_RE.search(question):
        trace.append({
            "stage": "self_infra_bypass",
            "reason": "self-introspection question — forcing cloud LLM with quarantined context",
        })
        return {
            "answered": False,
            "reason": "self_infra_bypass",
            "trace": trace,
            "duration_ms": int((time.time() - started) * 1000),
        }

    # ── Stage 1: Symbolic reasoner (rules engine) ─────────────────────────
    try:
        sym = symbolic_reasoner.reason(question)
        trace.append({
            "stage": "symbolic_reasoner",
            "matched": sym.get("confident", False),
            "confidence": sym.get("confidence", 0),
            "intent": sym.get("intent"),
        })
        if sym.get("confident") and sym.get("confidence", 0) >= SYMBOLIC_MIN_CONFIDENCE:
            await _record_routing("symbolic_reasoner")
            return {
                "answered": True,
                "response": sym["response"],
                "source": "symbolic_reasoner",
                "confidence": sym["confidence"],
                "intent": sym.get("intent"),
                "trace": trace,
                "duration_ms": int((time.time() - started) * 1000),
                "independent": True,
                "llm_calls_avoided": 1,
            }
    except Exception as e:
        logger.warning("symbolic_reasoner failed: %s", e)
        trace.append({"stage": "symbolic_reasoner", "error": str(e)})

    # ── Stage 2: Reasoning library (case-based retrieval) ────────────────
    try:
        lib = await reasoning_library.find_match(question, threshold=LIBRARY_MIN_CONFIDENCE)
        trace.append({
            "stage": "reasoning_library",
            "matched": lib.get("match", False),
            "confidence": lib.get("score", 0),
            "method": lib.get("method"),
        })
        if lib.get("match") and lib.get("case"):
            case = lib["case"]
            response = case.get("response", "")
            if response:
                # Add a tiny provenance note so the user knows it's from the library
                provenance = (
                    f"\n\n_↻ Retrieved from ARIA's reasoning library "
                    f"(prior {case.get('source_brain','llm')}, "
                    f"confidence {case.get('confidence_tag','?')}, "
                    f"used {case.get('access_count', 0)+1}x)._"
                )
                await _record_routing("reasoning_library")
                return {
                    "answered": True,
                    "response": response + provenance,
                    "source": "reasoning_library",
                    "confidence": lib["score"],
                    "library_case_id": case.get("id"),
                    "intent": case.get("intent"),
                    "trace": trace,
                    "duration_ms": int((time.time() - started) * 1000),
                    "independent": True,
                    "llm_calls_avoided": 1,
                }
    except Exception as e:
        logger.warning("reasoning_library failed: %s", e)
        trace.append({"stage": "reasoning_library", "error": str(e)})

    # ── Stage 3: Local brain (rule-based intent router) ──────────────────
    try:
        local = await local_brain.try_local_response(question)
        trace.append({
            "stage": "local_brain",
            "matched": local.get("answered", False),
            "intent": local.get("intent"),
        })
        if local.get("answered"):
            await _record_routing("local_brain")
            return {
                "answered": True,
                "response": local["response"],
                "source": "local_brain",
                "confidence": LOCAL_BRAIN_MIN_CONFIDENCE,
                "intent": local.get("intent"),
                "trace": trace,
                "duration_ms": int((time.time() - started) * 1000),
                "independent": True,
                "llm_calls_avoided": 1,
            }
    except Exception as e:
        logger.warning("local_brain failed: %s", e)
        trace.append({"stage": "local_brain", "error": str(e)})

    # ── Stage 4: Local Ollama reasoning model ────────────────────────────
    # Only attempt if Ollama is reachable AND a reasoning model is loaded.
    # The actual LLM call happens in aria_engine — we just signal "try local".
    ollama_ready = await _check_ollama_reasoning()
    if ollama_ready:
        return {
            "answered": False,
            "escalate_to": "local_ollama",
            "ollama_model": ollama_ready,
            "trace": trace,
            "duration_ms": int((time.time() - started) * 1000),
        }

    # ── Stage 5: Escalate to cloud LLM ───────────────────────────────────
    return {
        "answered": False,
        "escalate_to": "cloud_llm",
        "trace": trace,
        "duration_ms": int((time.time() - started) * 1000),
    }


_OLLAMA_REASONING_MODELS = [
    # Ordered by quality for defence/security reasoning
    "deepseek-r1:14b", "deepseek-r1:7b", "deepseek-r1",
    "qwen2.5:14b", "qwen2.5:7b", "qwen2.5",
    "llama3.1:8b", "llama3.1",
    "mistral:7b", "mistral",
]
_ollama_reasoning_model: str | None = None
_ollama_reasoning_checked: float = 0


async def _check_ollama_reasoning() -> str | None:
    """Detect if Ollama has a reasoning model installed locally. Cached 5 min."""
    global _ollama_reasoning_model, _ollama_reasoning_checked
    now = time.time()
    if _ollama_reasoning_model is not None and (now - _ollama_reasoning_checked) < 300:
        return _ollama_reasoning_model
    if _ollama_reasoning_checked > 0 and (now - _ollama_reasoning_checked) < 60 and _ollama_reasoning_model is None:
        return None  # recent failure, don't retry yet

    ollama_url = (os.getenv("OLLAMA_URL", "http://localhost:11434") or "").rstrip("/")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code != 200:
                _ollama_reasoning_checked = now
                return None
            data = resp.json()
            installed = {m.get("name", "") for m in data.get("models", [])}
            for candidate in _OLLAMA_REASONING_MODELS:
                if candidate in installed:
                    _ollama_reasoning_model = candidate
                    _ollama_reasoning_checked = now
                    logger.info("Ollama reasoning model detected: %s", candidate)
                    return candidate
    except Exception as e:
        logger.debug("Ollama reasoning detection failed: %s", e)

    _ollama_reasoning_checked = now
    _ollama_reasoning_model = None
    return None


async def record_cloud_llm_response(
    question: str,
    response: str,
    *,
    intent: str = "",
    context_keys: list[str] | None = None,
    source_brain: str = "deepseek",
) -> dict:
    """Distillation hook — capture every successful cloud LLM response into
    the reasoning library so the next similar query can be answered locally.

    This is the engine of ARIA's slow detachment from cloud reasoning.

    2026-04-25: skip distillation for self-infra introspection questions.
    Background: 2026-04-24 OpenClaw incident — a cloud LLM answer about
    ARIA's own infrastructure became a [CONFIRMED] fast-path entry that
    re-surfaced even after the upstream Brave route was blocked. Self-
    infra answers are inherently risky to cache because (a) the
    underlying infrastructure changes, (b) any fabrication propagates
    permanently, and (c) the operator's diagnostic tooling is the
    authoritative source, not a cached LLM answer.
    """
    if _SELF_INFRA_INTROSPECTION_RE.search(question):
        await _record_routing("cloud_llm")
        return {
            "recorded": False,
            "reason": "self_infra_skip_distillation",
        }
    await _record_routing("cloud_llm")
    try:
        return await reasoning_library.record_response(
            question, response,
            intent=intent,
            source_brain=source_brain,
            context_keys=context_keys,
        )
    except Exception as e:
        logger.warning("distillation failed: %s", e)
        return {"recorded": False, "error": str(e)}


async def get_independence_report() -> dict:
    """Compute the independence ratio: fraction of queries answered locally."""
    stats = await _load_stats()
    library_stats = await reasoning_library.get_stats()
    sym_caps = symbolic_reasoner.get_capability_surface()
    local_caps = local_brain.get_capability_surface()
    ollama_model = await _check_ollama_reasoning()

    by_source = stats.get("by_source", {})
    total = stats.get("total_queries", 0) or 1

    local_count = (
        by_source.get("symbolic_reasoner", 0)
        + by_source.get("reasoning_library", 0)
        + by_source.get("local_brain", 0)
        + by_source.get("local_ollama", 0)
    )
    cloud_count = by_source.get("cloud_llm", 0)
    independence_ratio = round(local_count / max(total, 1), 3)

    return {
        "total_queries": total,
        "by_source": by_source,
        "independence_ratio": independence_ratio,
        "local_count": local_count,
        "cloud_count": cloud_count,
        "trajectory": _trajectory_label(independence_ratio),
        "components": {
            "symbolic_reasoner": sym_caps,
            "reasoning_library": library_stats,
            "local_brain": local_caps,
            "local_ollama": {
                "available": ollama_model is not None,
                "model": ollama_model,
            },
        },
        "born": stats.get("born"),
        "age_days": round((time.time() - stats.get("born", time.time())) / 86400, 1),
    }


def _trajectory_label(ratio: float) -> str:
    if ratio >= 0.7: return "INDEPENDENT — primarily reasoning locally"
    if ratio >= 0.4: return "MATURING — local layer carrying half the load"
    if ratio >= 0.15: return "WARMING — local layer growing"
    if ratio > 0:    return "EARLY — most queries still hit the cloud"
    return "COLD START — no queries routed yet"
