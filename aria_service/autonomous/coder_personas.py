"""R-F2231 — domain personas for ARIA's autonomous coder.

The tuned specialist knowledge that lives in `.claude/agents/*.md` is only visible
to Claude Code. This module ports the same expertise into ARIA's OWN self-coding
loop: given the gap's module / target file, `select_persona()` picks the matching
specialist and `persona_prompt_block()` renders its binding rules for injection
into sovereign_llm's fix prompt (planning + code + edit). So when gap_detector →
self_coder fixes a sanctions/DD/WA/etc. file, DeepSeek writes the fix under that
specialist's rules — the same discipline, running inside ARIA's guardrails.

Rules are condensed to what matters at CODE-GENERATION time (honesty invariants,
the single-process event-loop, §6, §21a). §6-clean, no I/O, pure functions.
"""
from __future__ import annotations

# (name, match-substrings, condensed binding rules). First match wins; the order
# is most-specific-domain first. `python-pro` is the default fallback (below).
_PERSONAS: list[tuple[str, tuple[str, ...], str]] = [
    ("sanctions-auditor",
     ("sanction", "ofac", "un_sc", "fcdo", "worldbank_debarred", "rca_screening", "crypto_sanctions"),
     "Sanctions/compliance is the highest-trust surface. NEVER return a false "
     "'clean'/'not sanctioned': an empty or failed list store must yield "
     "INSUFFICIENT_DATA, a stale cache UNVERIFIED — never GREEN. Fail LOUD (wire a "
     "capability_gap; never return [] as if the entity is clean). §6 free/native "
     "only (no paid list). Offload large XML/list parsing off the event loop."),
    ("dd-reviewer",
     ("dd_orchestrator", "company_investigator", "adverse_media", "dd_schema", "dd_trigger"),
     "Due-diligence output is customer-facing, Grade-A trust. NEVER fabricate a "
     "finding — 'no findings' is honest, an invented one is not. Distinguish "
     "verified (URL-checked) from triangulated. Attached-document review routes to "
     "the LLM-pure path, never an external investigate/screen tool. Cross-tenant "
     "reads fail CLOSED (per-user; empty user_id = admin only)."),
    ("wa-debugger",
     ("wa_listener", "walistener", "services/wa", "baileys", "whatsapp"),
     "WhatsApp tier. EVERY output must report its delivery outcome to the brain "
     "(§25: delivered/timeout/error/send_failed + request_id) — the server can't "
     "infer it. A new LOCAL import REQUIRES a matching COPY in Dockerfile.wa or the "
     "container crash-loops (node --check won't catch it). Mirror any new "
     "post-response hook into BOTH the sync and async paths (§13)."),
    ("autonomy-engineer",
     ("gap_detector", "self_coder", "self_improve", "safety", "autonomous/engine",
      "load_governor", "capability_gaps", "coder_personas"),
     "Autonomy subsystem. Wired-not-dark (§21a): BOTH the success AND failure branch "
     "must reach the brain (brain_hook / capability_gaps / a metric) — no except:pass. "
     "Keep the guardrails intact (NO_AUTODEPLOY_FILES, the truncation/AST guard, "
     "de-dup, the $300 cap). Emit COMPLETE files — a truncated stub that drops "
     "existing symbols is a wipe, not a fix."),
    ("ai-engineer",
     ("reranker", "rag_store", "semantic_search", "embedding", "neural_memory", "encode_offload"),
     "ML/RAG on the single-process brain. NEVER run model encode/predict or a heavy "
     "parse inline on the event loop — offload via asyncio.to_thread / encode_offload. "
     "Models are LOCAL/baked (§6): no runtime HuggingFace fetch, no paid vector DB or "
     "embedding API. Prove relevance with a test; don't assert it."),
    ("search-specialist",
     ("web_search", "searxng", "search_index", "crawler", "search_doctrine"),
     "Search/retrieval. §6 free/native only (SearXNG self-host / DDG / RSS — no paid "
     "search API). Offload rerank/encode off the event loop. A backend error must "
     "FAIL LOUD (return [] AND wire a capability_gap) — never let 'error' look like "
     "'no results'. Don't block on the slowest backend."),
]

_DEFAULT = (
    "python-pro",
    "Core Python on the SINGLE-PROCESS brain: one blocking call freezes everything — "
    "never run CPU-bound or blocking IO inline; offload via asyncio.to_thread. The "
    "state_store is ONE aiosqlite connection and is saturation-sensitive — do not add "
    "a per-request hot-key read-modify-write. Type hints + explicit except types (no "
    "bare/silent except; fail loud to the brain, §21a). Root cause, not a band-aid "
    "(§1). Deps are pip/.venv/requirements.txt; storage is aiosqlite, not SQLAlchemy.",
)


def select_persona(hint: str | None) -> tuple[str, str]:
    """Return (persona_name, rules) for a gap's module / target-file hint.

    First specialist whose match-substring appears in the (lowercased) hint wins;
    otherwise the python-pro default. Never raises.
    """
    h = (hint or "").lower()
    for name, tokens, rules in _PERSONAS:
        if any(tok in h for tok in tokens):
            return name, rules
    return _DEFAULT


def persona_prompt_block(hint: str | None) -> str:
    """Render the selected persona as a prompt section for sovereign_llm.

    Empty string is never returned (python-pro is the floor), so every coder fix
    carries at least the single-process/event-loop + honesty discipline.
    """
    name, rules = select_persona(hint)
    return (
        f"\nSPECIALIST PERSONA — {name} (R-F2231)\n"
        f"Write this fix as ARIA's {name}. These domain rules are BINDING, in "
        f"addition to the constitution below:\n{rules}\n"
    )


def persona_catalog() -> str:
    """R-F2232 — render ALL personas as a reference block for an INTERACTIVE coder
    (the aria CLI) that doesn't know upfront which file it will edit. The model
    applies the matching specialist when it touches that domain's files. Same
    source of truth as persona_prompt_block, so the CLI and the brain's autonomous
    coder never drift.
    """
    lines = [
        "\nSPECIALIST PERSONAS (R-F2232) — when you edit a file in one of these",
        "domains, write the change under that specialist's BINDING rules:",
    ]
    for name, tokens, rules in _PERSONAS:
        lines.append(f"\n- {name} (files matching: {', '.join(tokens[:4])}…)\n  {rules}")
    dname, drules = _DEFAULT
    lines.append(f"\n- {dname} (default — any other Python)\n  {drules}")
    return "\n".join(lines) + "\n"
