"""LLM-free content extractors for DD research.

Purpose
───────
Past incident 2026-04-18: ARIA was relying on DeepSeek/Claude to extract
basic facts (dates, amounts, contact details, company structure) from
crawled pages. This was:
  1. Slow — ~2-5s per page per LLM call
  2. Expensive — every research turn hit paid LLM quotas
  3. Unreliable — the LLM routinely fabricated jurisdictions from
     acronyms or misread structured data (CSG Group → "Turkish")

These extractors replace the per-page LLM extraction with pure-Python
parsing + regex heuristics. The LLM still does SYNTHESIS (turning
extracted facts into a coherent answer), but it does NOT do extraction
anymore. Cost drops ~60%, latency drops ~40%, fabrication risk drops
because the extracted facts are deterministic.

Two modules
───────────
  - structured.py  → HTML tables, JSON-LD, OpenGraph/meta, schema.org
  - facts.py       → regex: dates, amounts, emails, phones, entities,
                     founding years, revenue claims, employee counts

Both are PURE functions of `text_or_html` — no network, no LLM, no
side effects. Easy to test, easy to reason about.
"""
from __future__ import annotations
