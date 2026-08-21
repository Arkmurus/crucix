"""R-F732 (2026-05-20) — structured chat response source extraction.

Pre-R-F732 chat citations were inline-only: `[from dd_orchestrate:run_id]`
markers and bare URLs embedded in the prose. A consumer wanting to
build a "Sources" rail / chip strip / footnote popover had to regex
the text — there was no structured `sources[]` field in the chat
response JSON.

This module extracts citations from the LLM response text and
optional tool_context block, returning a deduplicated list of
typed source dicts suitable for the frontend to render as
clickable chips.

Detection patterns (best-effort, non-blocking):
  * `[from <tool>:<run_id>]`             → type=tool
  * `[from <tool>]`                      → type=tool (no run_id)
  * `http(s)://...`                       → type=url
  * `[SANCTIONS LIVE CHECK ...]` blocks  → type=primary_source
  * RAG-emitted `[source: <path-or-url>]` → type=rag

The output is capped at 50 sources per response to keep the
frontend rail manageable.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import re
from typing import Any

# Inline tool-result citation. dd_orchestrator + several other tools
# emit prose like "...findings [from dd_orchestrate:abc123]". The run
# ID is the trace id callers can use to drill into the run record.
_TOOL_CITE_RE = re.compile(
    r"\[from\s+([a-z_][a-z0-9_]*)(?:\s*:\s*([A-Za-z0-9_\-]+))?\s*\]",
    re.IGNORECASE,
)

# Bare URL pattern. Strict enough to avoid trailing punctuation
# (matches the same shape used by routes/aria.py:_URL_RE for the
# investigate tool).
_URL_RE = re.compile(
    r"https?://[^\s<>\"\'\)\]\}]+",
    re.IGNORECASE,
)

# R-F4219 / C-199 — ARIA'S OWN INDEX. web_explorer emits memory-first hits with
# `memory://<id>` (and `rag://<id>`) provenance. `_URL_RE` above is anchored on
# http(s) and the RAG branch below wants a `[source: ...]` shape, so neither could
# see them: a fact recalled from ARIA's own corpus contributed NOTHING to the
# reported evidence, and an answer built entirely from her own index read
# `Sources: 0`. §15/§27e make that index the moat, so the better her memory got,
# the more ungrounded she looked.
#
# Counted, never disguised. R-F3183 already ruled on this in dd_orchestrator —
# "a memory:// URL is ARIA'S OWN RAG, not an external source ... Tier by the
# SOURCE, not by the code path that fetched it" — so these get their own type and
# NO url, and can never be rendered as a third-party link.
_MEMORY_URI_RE = re.compile(
    r"\b(memory|rag)://([A-Za-z0-9_\-]+)",
    re.IGNORECASE,
)

# Primary-source sanctions block emitted by sanctions_claim_guard.
# Shape: "[SANCTIONS LIVE CHECK ...]" or "[SANCTIONS LIVE CHECK — DEGRADED]"
_PRIMARY_SANCTIONS_RE = re.compile(
    r"\[SANCTIONS LIVE CHECK(?:\s*—\s*[A-Z ]+)?\]",
)

# RAG inline citation pattern. The retrieval layer formats source
# paths as "[source: <path>]" / "[ref: <id>]" when injecting into
# the context block.
_RAG_CITE_RE = re.compile(
    r"\[(?:source|ref|cite)\s*:\s*([^\]]+)\]",
    re.IGNORECASE,
)

# Hard cap so a pathological response can't generate thousands of
# source entries.
MAX_SOURCES = 50

# URLs that aren't really useful citations — strip these.
_URL_BLOCKLIST = (
    "example.com",
    "localhost",
    "127.0.0.1",
)


def _looks_like_real_url(url: str) -> bool:
    low = url.lower()
    return not any(blocked in low for blocked in _URL_BLOCKLIST)


def extract(response_text: str, tool_context: str = "") -> list[dict[str, Any]]:
    """Extract typed source citations from response text + optional
    tool_context. Returns a deduplicated list ordered by first
    appearance. Each entry has shape:

        {
          "type":  "tool" | "url" | "primary_source" | "rag",
          "label": str,            # human-readable
          "url":   str | None,     # if applicable
          "tool":  str | None,     # for type=tool
          "run_id": str | None,    # for type=tool with trace
        }

    The function is fail-soft: any regex failure returns whatever
    has been collected so far (never raises).
    """
    if not response_text and not tool_context:
        return []

    seen_keys: set[str] = set()
    sources: list[dict[str, Any]] = []
    combined = (response_text or "") + "\n" + (tool_context or "")

    try:
        # 1. Inline tool citations — `[from tool:run_id]`
        for m in _TOOL_CITE_RE.finditer(combined):
            tool, run_id = m.group(1).lower(), m.group(2)
            key = f"tool:{tool}:{run_id or ''}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            sources.append({
                "type":   "tool",
                "label":  f"{tool}" + (f" run {run_id[:8]}" if run_id else ""),
                "url":    None,
                "tool":   tool,
                "run_id": run_id,
            })
            if len(sources) >= MAX_SOURCES:
                return sources

        # 2. Bare URLs
        for m in _URL_RE.finditer(combined):
            url = m.group(0).rstrip(".,;:!?\")]}>'»”")
            if not _looks_like_real_url(url):
                continue
            key = f"url:{url}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            # Use the host as the label if available
            label = url
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                if parsed.hostname:
                    host = parsed.hostname.lstrip("www.")
                    label = host
            except Exception:
                pass
            sources.append({
                "type":   "url",
                "label":  label,
                "url":    url,
                "tool":   None,
                "run_id": None,
            })
            if len(sources) >= MAX_SOURCES:
                return sources

        # 3. Primary-source sanctions blocks
        for m in _PRIMARY_SANCTIONS_RE.finditer(combined):
            key = f"primary:sanctions_live_check"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            sources.append({
                "type":   "primary_source",
                "label":  "Sanctions live check (OFAC / EU / OFSI)",
                "url":    None,
                "tool":   "sanctions_claim_guard",
                "run_id": None,
            })

        # 3b. ARIA's own index (R-F4219). Deliberately BEFORE the generic RAG
        # branch: these arrive as bare `URL: memory://<id>` lines, not as
        # `[source: ...]`, which is why they were invisible.
        for m in _MEMORY_URI_RE.finditer(combined):
            scheme, ident = m.group(1).lower(), m.group(2)
            key = f"memory:{ident}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            sources.append({
                "type":   "memory",
                "label":  f"ARIA memory {ident}",
                # No url, ever: a self-citation must not be published as a
                # clickable third-party source (R-F3183).
                "url":    None,
                "tool":   scheme,
                "run_id": ident,
            })
            if len(sources) >= MAX_SOURCES:
                return sources

        # 4. RAG inline citations
        for m in _RAG_CITE_RE.finditer(combined):
            ref = m.group(1).strip()[:200]
            key = f"rag:{ref}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            # If the ref looks like a URL, surface it as one
            looks_url = ref.lower().startswith(("http://", "https://"))
            sources.append({
                "type":   "rag",
                "label":  ref if not looks_url else (ref.split("/")[2] if "/" in ref else ref),
                "url":    ref if looks_url else None,
                "tool":   None,
                "run_id": None,
            })
            if len(sources) >= MAX_SOURCES:
                return sources

    except Exception:
        # Fail-soft: extraction must never break the chat response.
        from .engine_wiring import wire_failure as _wf
        _wf(
            module="chat_sources",
            detail="extract failed in chat_sources",
            gap_type="engine_failure",
            source="chat_sources:extract",
        )
        return sources

    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="chat_sources",
        summary="Extract",
        source_id="chat_sources:R-F1001",
    )

    return sources

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.


# ── R-F4221 / C-201 — the tool block, and the ONE marker that identifies it ──
# Tool output does not reach the engine as an argument: routes/aria.py folds it
# INTO the message via _wrap_tool_block(), whose own docstring calls itself "the
# ONLY place tool output should enter message_for_llm". Inside the engine,
# `context` is the 7-layer KNOWLEDGE context — a different thing — so
# extract(response_text, tool_context=context) was reading a context that never
# held the tool's URLs, while the block that did sat unread in `message`.
#
# Measured live 2026-08-21 (aria-intel 69ec8684): a /chat/stream turn emitted
# tool_running x6, tool_done and a "Tools: deep web search" footer — a tool
# demonstrably ran — and produced no `sources` event at all.
#
# The marker lives HERE, not in routes, so the engine can find the block without
# importing routes (routes imports the engine — the reverse is circular) and
# without a copied literal that could drift apart from it (R-F2639: one measure).
TOOL_BLOCK_PREAMBLE = (
    "[I have already run the appropriate tool on your request. "
    "Use the data below to answer comprehensively, cite specific findings, "
    "and end with a clear recommendation.]"
)


def tool_block_from(message: str | None) -> str:
    """Return the trusted tool block embedded in a message, or "" if none.

    Deliberately returns only the text AFTER the preamble: everything before it
    is the user's own message, and a URL the user pasted is not ARIA's evidence.
    Absence reads as absence — a message with no tool block yields "", never a
    guess at what a tool might have said.
    """
    if not message:
        return ""
    idx = message.find(TOOL_BLOCK_PREAMBLE)
    if idx < 0:
        return ""
    return message[idx + len(TOOL_BLOCK_PREAMBLE):]
