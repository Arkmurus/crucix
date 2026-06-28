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

# 2026-04-25: self-introspection detection moved to the shared
# self_infra_detector module so the three layers (chat router, retrieval,
# reasoning router) read from one canonical regex. Stage 0 bypass below
# forces self-infra questions to escalate to cloud LLM, where the layer-
# side quarantine in aria_engine._build_7_layer_context kicks in.
from . import self_infra_detector as _sid
from .wire import fail_wire  # R-F1788 §21 brain-wiring
_SELF_INFRA_INTROSPECTION_RE = _sid.SELF_INFRA_INTROSPECTION_RE


# ── Stats tracking ──────────────────────────────────────────────────────────

_stats_cache: dict | None = None

# R-F268 (2026-05-11) — no-scaffold-write rule. Stats are scaffolded with
# zero-counter defaults when the backend is empty. Without this guard, the
# scaffolded zeros would overwrite real counter data on the destination
# backend across a flip. Same pattern as R-F267 _mastery_dirty.
_stats_dirty: bool = False


def _mark_stats_dirty() -> None:
    global _stats_dirty
    _stats_dirty = True


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
    """R-F268: persist only when actual routing has occurred since the last load."""
    global _stats_dirty
    if _stats_cache is None or not _stats_dirty:
        return
    await rs.set_json(ROUTER_STATS_KEY, _stats_cache, ex=30 * 86400)
    _stats_dirty = False

async def _record_routing(source: str) -> None:
    """Record a routing decision to local stats + brain."""
    stats = await _load_stats()
    stats["total_queries"] = stats.get("total_queries", 0) + 1
    by_source = stats.setdefault("by_source", {})
    by_source[source] = by_source.get(source, 0) + 1
    stats["last_query"] = time.time()
    _mark_stats_dirty()  # R-F268 — actual routing observation
    await _save_stats()
    # R-F1169 — wire routing decision to brain
    try:
        from .engine_wiring import wire_success
        wire_success(
            module="reasoning_router",
            summary=f"Routed to {source}",
            source_id=f"reasoning_router:{source}",
        )
    except Exception:
        pass


# ── Public API ──────────────────────────────────────────────────────────────

@fail_wire(module="reasoning_router", gap_type="engine_failure")
async def try_local_reasoning(question: str, *, silent: bool = False,
                              exclude_topic: str | None = None) -> dict:
    """Try every LOCAL reasoning source. Returns the first confident answer
    or a {"answered": False} signal to escalate.

    This function does NOT call any cloud LLM. It is the gatekeeper that
    decides whether the cloud is even needed.

    Args:
        silent: when True, suppress side effects that would otherwise pollute
            the brain ledgers — currently routes through to symbolic_reasoner's
            silent_brain_hook flag (F53 2026-04-28). Set by
            student.compare_local_silently which re-runs the same query
            purely to score local-vs-cloud divergence.
        exclude_topic: when set, the RAG retrieval in local_brain will exclude
            facts matching this topic prefix. Used by self_quiz to prevent
            quiz-gaming (retrieving the quizzed case's own answer).
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

    # ── Stage 0.5: Self-capability question bypass (R-F599) ───────────────
    # Capability-overview / "what can you do" / "how many tasks" questions
    # must escalate to cloud LLM so the R-F595 self_introspect_guard can
    # inject live /api/aria/health/perf data into context.
    #
    # Past incident 2026-05-16 18:45 WhatsApp: operator asked "share an
    # overview of your full system capabilities" → reasoning_library cache
    # hit served a stale pre-R-F595 reply with "34 autonomous tasks",
    # "48/49 sources OK", "Officeholder Verification 18-month TTL" —
    # all hallucinations contradicting live /health/perf (real values
    # 78 tasks, 431 sources, no TTL). The R-F594 post-scan guard caught
    # the violations and appended the warning block, but the cached
    # response was already delivered.
    #
    # This bypass forces the LLM call so R-F595 fires first and the
    # response is composed against real numbers instead of cached fiction.
    # Same shape as Stage 0 — soft bypass, returns answered=False so the
    # caller escalates to cloud LLM.
    try:
        from . import self_introspect_guard as _sig
        if _sig.detect_self_capability_question(question):
            trace.append({
                "stage": "self_capability_bypass",
                "reason": "capability question — forcing cloud LLM so R-F595 self_introspect injects live counts",
            })
            return {
                "answered": False,
                "reason": "self_capability_bypass",
                "trace": trace,
                "duration_ms": int((time.time() - started) * 1000),
            }
    except Exception as _sig_err:
        # Detector failure must NEVER block the chat turn. Log and continue
        # to the next stage; the post-scan R-F401/R-F594 guard still fires.
        logger.debug("R-F599 capability-question detector failed: %s", _sig_err)

    # ── Stage 1: Symbolic reasoner (rules engine) ─────────────────────────
    try:
        sym = symbolic_reasoner.reason(question, silent_brain_hook=silent)
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
        # R-F1745 — wire failure to brain
        try:
            from .engine_wiring import wire_failure as _wf
            _wf(module="reasoning_router", detail=f"symbolic_reasoner: {e}",
                gap_type="no_symbolic_rule", source="reasoning_router:try_local_reasoning")
        except Exception:
            pass

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
        # R-F1745 — wire failure to brain
        try:
            from .engine_wiring import wire_failure as _wf
            _wf(module="reasoning_router", detail=f"reasoning_library: {e}",
                gap_type="source_failure", source="reasoning_router:try_local_reasoning")
        except Exception:
            pass

    # ── Stage 3: Local brain (rule-based intent router) ──────────────────
    try:
        local = await local_brain.try_local_response(question, exclude_topic=exclude_topic)
        trace.append({
            "stage": "local_brain",
            "matched": local.get("answered", False),
            "intent": local.get("intent"),
        })
        if local.get("answered"):
            await _record_routing("local_brain")
            result = {
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
            # R-F1743: propagate rag_context for quiz-mode reasoning
            if local.get("rag_context"):
                result["rag_context"] = local["rag_context"]
            return result
    except Exception as e:
        logger.warning("local_brain failed: %s", e)
        trace.append({"stage": "local_brain", "error": str(e)})
        # R-F1745 — wire failure to brain
        try:
            from .engine_wiring import wire_failure as _wf
            _wf(module="reasoning_router", detail=f"local_brain: {e}",
                gap_type="source_failure", source="reasoning_router:try_local_reasoning")
        except Exception:
            pass

    # R-F1047 -- try grounded reasoner BEFORE cloud LLM escalation.
    # The grounded reasoner decomposes the question, gathers evidence from
    # all available sources, reasons over verified evidence only, and
    # ground-or-abstains. If it produces a grounded answer, serve it.
    try:
        from .grounded_reasoner import reason as _gr_reason
        _gr_result = await _gr_reason(question)
        if _gr_result and not _gr_result.abstained and _gr_result.answer:
            trace.append({
                "stage": "grounded_reasoner",
                "matched": True,
                "claims": len(_gr_result.claims),
                "grounded": sum(1 for c in _gr_result.claims if c.grounded),
            })
            await _record_routing("grounded_reasoner")
            return {
                "answered": True,
                "response": _gr_result.answer,
                "source": "grounded_reasoner",
                "confidence": (
                    _gr_result.claims[0].confidence if _gr_result.claims else 0.8
                ),
                "intent": "grounded_reasoning",
                "trace": trace,
                "duration_ms": _gr_result.duration_ms,
                "independent": True,
                "llm_calls_avoided": 1,
            }
        trace.append({
            "stage": "grounded_reasoner",
            "matched": False,
            "abstained": getattr(_gr_result, 'abstained', True),
        })
    except Exception as _gr_err:
        logger.debug("Grounded reasoner failed (continuing escalation): %s", _gr_err)
        trace.append({"stage": "grounded_reasoner", "error": str(_gr_err)[:120]})

    # ── Stage 4: Company investigator (R-F1061) ──────────────────────────
    # When the question is about investigating a specific company/entity,
    # run the full OSINT pipeline: web search, crawl, registry, sanctions,
    # news, social media, tech stack, SSL/DNS, procurement.
    try:
        # R-F1613 — §22a query-misroute fix. Removed the INTERROGATIVES
        # (who.?is / what.?is / tell.?me.?about) from this keyword set: they
        # matched ordinary questions like "who is the CEO of BAE Systems? use
        # web search" -> has_investigate + has_company ('systems') -> Stage 4
        # fired investigate_company -> "No findings", silently dropping the
        # explicit web-search instruction. Only imperatives remain — an
        # interrogative or an explicit web-search request must NOT trigger the
        # OSINT pipeline (see _force_web below).
        _investigate_keywords = (
            r"\b(?:investigate|research|background.?check|due.?diligence|"
            r"deep.?search|"
            r"analyse?|analyze|screen|vet|check.?out|look.?up|"
            r"find.?info(?:rmation)?.?on|search.?for)\b"
        )
        _company_keywords = (
            r"\b(?:company|corp(?:oration)?|ltd|llc|inc|gmbh|sa\b|"
            r"pty|plc|group|holdings|enterprise|industries|"
            r"technologies|solutions|systems|defence|defense|"
            r"aerospace|manufacturing|trading|contracting)\b"
        )
        import re as _re
        _q_lower = question.lower()
        has_investigate = _re.search(_investigate_keywords, _q_lower)
        has_company = _re.search(_company_keywords, _q_lower)
        has_url = "http" in _q_lower or ".com" in _q_lower or ".co." in _q_lower
        # R-F1327 — never run the company OSINT pipeline when the user attached a
        # document. Mirror routes/aria.py §22a / R-F1326: a request like "do a deep
        # analysis of this NDA" with the NDA text attached matches investigate
        # ("analysis") + company ("Systems"/"LLC" from the doc) keywords, and the
        # prefix-strippers don't catch it, so the WHOLE message was passed to
        # investigate_company -> "Input rejected as non-company" (live 2026-06-04).
        # This path bypassed _detect_tool_intent's R-F1326 guard entirely. An
        # attached-document request belongs to the grounded/LLM doc-review path, not
        # the company investigator — so skip Stage 4 whenever the doc marker is present.
        _has_attached_doc = "[attached document" in _q_lower

        # R-F1613 — web-search / interrogative override. When the user asks a
        # plain question (interrogative) OR explicitly requests a web search,
        # the company OSINT pipeline must NOT fire — the request belongs to the
        # web-search / LLM path, not investigate_company. Without this, "who is
        # the CEO of BAE Systems? use web search" matched has_company
        # ('systems') and got routed to investigate_company -> "No findings",
        # silently discarding the web-search instruction.
        _q_stripped = _q_lower.lstrip().lstrip("\"'(").lstrip()
        _interrogative = bool(_re.match(
            r"(?:who|what|when|where|why|how|which|is|are|does|do)\b",
            _q_stripped,
        ))
        _explicit_web = (
            "use web search" in _q_lower
            or "search the web" in _q_lower
            or "web search" in _q_lower
        )
        _force_web = _interrogative or _explicit_web

        if not _has_attached_doc and not _force_web and has_investigate and (has_company or has_url):
            from .company_investigator import investigate_company as _ic
            # Extract company name from question (everything after the
            # investigation keyword, up to punctuation or end)
            _company_name = question
            # R-F1613 — dropped "tell me about "/"who is "/"what is " prefix-
            # strippers; their keywords were removed from _investigate_keywords
            # so they can no longer reach this branch (interrogatives now route
            # to the web-search/LLM path).
            for _prefix in ["investigate ", "research ", "deep search on ",
                            "analyse ", "analyze ", "screen ", "vet ",
                            "check out ", "look up ", "search for "]:
                if _prefix in _q_lower:
                    _idx = _q_lower.find(_prefix) + len(_prefix)
                    _company_name = question[_idx:].strip().rstrip(".?!,")
                    break

            _ic_result = await _ic(_company_name)
            if _ic_result and (_ic_result.findings or _ic_result.summary):
                _response = _ic_result.summary or (
                    f"Investigation of {_company_name} completed. "
                    f"Found {len(_ic_result.findings)} data points across "
                    f"{len(set(f.category for f in _ic_result.findings))} categories."
                )
                trace.append({
                    "stage": "company_investigator",
                    "matched": True,
                    "findings": len(_ic_result.findings),
                    "categories": list(set(f.category for f in _ic_result.findings)),
                    "risk_indicators": len(_ic_result.risk_indicators),
                })
                await _record_routing("company_investigator")
                return {
                    "answered": True,
                    "response": _response,
                    "source": "company_investigator",
                    "confidence": 0.85 if _ic_result.findings else 0.5,
                    "intent": "company_investigation",
                    "trace": trace,
                    "duration_ms": _ic_result.duration_ms,
                    "independent": True,
                    "llm_calls_avoided": 1,
                    "investigation": {
                        "entity": _ic_result.entity_name,
                        "canonical": _ic_result.canonical_name,
                        "findings_count": len(_ic_result.findings),
                        "risk_indicators": _ic_result.risk_indicators[:5],
                        "sources": _ic_result.sources_cited[:10],
                    },
                }
            trace.append({
                "stage": "company_investigator",
                "matched": False,
                "findings": len(_ic_result.findings) if _ic_result else 0,
            })
    except Exception as _ic_err:
        logger.debug("Company investigator failed (continuing escalation): %s", _ic_err)
        trace.append({"stage": "company_investigator", "error": str(_ic_err)[:120]})

    # ── Stage 5: Local Ollama reasoning model ────────────────────────────
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

    # ── Stage 6: Escalate to cloud LLM ───────────────────────────────────
    # R-F1169 — wire escalation to brain so ARIA tracks cloud dependency
    try:
        from .engine_wiring import wire_success
        wire_success(
            module="reasoning_router",
            summary=f"Escalating to cloud LLM (local sources exhausted)",
            source_id="reasoning_router:escalate_to_cloud",
        )
    except Exception:
        pass
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


@fail_wire(module="reasoning_router", gap_type="engine_failure")
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


@fail_wire(module="reasoning_router", gap_type="engine_failure")
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

    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="reasoning_router",
        summary="Get Independence Report",
        source_id="reasoning_router:R-F996",
    )

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

# R-F2119 §21a — wire failure handler for reasoning_router
try:
    wire_failure(module="reasoning_router", detail="module shutdown",
                gap_type="engine_failure", source="reasoning_router:shutdown")
except Exception:
    pass
