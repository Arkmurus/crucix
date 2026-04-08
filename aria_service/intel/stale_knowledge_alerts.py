"""ARIA stale-knowledge alerts.

A small registry of recent disruptive events (elections, coups, cabinet
reshuffles) that invalidate pre-event leadership / officeholder knowledge.
When ARIA is asked about a country with a known disruptive event in the
recent past, this module injects a system-prompt addendum warning the LLM
that ITS TRAINING-DATA KNOWLEDGE for that country is potentially stale and
must not be used to answer "current officeholder" questions without a fresh
tool call.

Why this exists
═══════════════
The LLM's knowledge cutoff is some date in the past. Anything political
that changed AFTER the cutoff is structurally invisible to the model. The
constitution clause and the post-process officeholder guard catch the
*symptom* (a fabricated current minister); this addendum tries to prevent
the *cause* (the LLM having no idea the cabinet has changed).

Round-4 incident: ARIA confidently named Dominic Nitiwul as Ghana's current
defence minister; he served under Akufo-Addo. Mahama's December 2024
election replaced the entire cabinet. The model had no signal that this
event had happened.

Bootstrapping
═════════════
The registry starts small and grows as more events surface. Each entry is
a single dict with country, event, date, summary, and a "stale_topics"
list of subjects whose pre-event answers should be treated as suspect.

Behind ARIA_STALE_KNOWLEDGE_ALERTS env var (default ON).
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("aria.stale_knowledge_alerts")

# Registry of known disruptive events. Each entry is keyed by ISO-loose
# country name (lowercase) so the lookup is fast and case-insensitive.
# Add to this as new disruptive events surface — elections, coups, cabinet
# reshuffles, regime changes, ministerial resignations, etc.
#
# IMPORTANT: an entry should ONLY be added when the event materially
# invalidates an entire class of questions (cabinet, leadership, policy
# direction). Don't add minor cabinet shuffles.
_STALE_EVENTS: list[dict] = [
    {
        "country": "ghana",
        "event_date": "2024-12",
        "event": "Presidential election — John Mahama (NDC) defeated Mahamudu Bawumia (NPP)",
        "stale_topics": [
            "defence minister", "minister of defence", "minister for defence",
            "foreign minister", "interior minister", "finance minister",
            "cabinet", "all ministerial appointments",
            "national security", "director of national intelligence",
        ],
        "summary": (
            "John Mahama returned as President of Ghana following the December 2024 election, "
            "defeating Mahamudu Bawumia. The entire cabinet under Nana Akufo-Addo was replaced. "
            "Any ARIA training-data knowledge of Ghana's ministers or senior officials from before "
            "January 2025 is OBSOLETE."
        ),
    },
    # Add more entries here as field-test surfaces them.
]


def is_enabled() -> bool:
    """Feature flag — default ON. Set ARIA_STALE_KNOWLEDGE_ALERTS=0 to disable."""
    val = os.getenv("ARIA_STALE_KNOWLEDGE_ALERTS", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


def relevant_alerts(message: str) -> list[dict]:
    """Return stale-knowledge entries that match the user message.

    Match logic: country name is mentioned in the message (substring match,
    word-boundary aware). We don't gate on stale_topics matching — the
    presence of the country alone is enough to inject the warning, because
    the LLM might mention an officeholder in passing without the user
    having explicitly asked.
    """
    if not is_enabled() or not message:
        return []
    msg_lower = message.lower()
    matches: list[dict] = []
    for entry in _STALE_EVENTS:
        country = entry["country"]
        # Word-boundary aware match to avoid "ghana" matching "afghanistan"
        if re.search(r"\b" + re.escape(country) + r"\b", msg_lower):
            matches.append(entry)
    return matches


def addendum_for(alerts: list[dict]) -> str:
    """Build the system-prompt addendum from a list of relevant alerts."""
    if not alerts:
        return ""
    lines = [
        "STALE-KNOWLEDGE ALERTS — read this BEFORE answering",
        "",
        "The following disruptive events have occurred in countries mentioned in the user's question. "
        "Your training-data knowledge of these countries' leadership / officeholders / cabinet is "
        "potentially OBSOLETE and must NOT be used to answer 'current officeholder' questions without "
        "a fresh tool call. If a relevant tool has not run in this turn, you MUST flag any officeholder "
        "claim as [UNCERTAIN — last verified pre-<event_date>, post-<event> cabinet not in training data].",
        "",
    ]
    for entry in alerts:
        lines.append(f"• {entry['country'].upper()} ({entry['event_date']}): {entry['event']}")
        lines.append(f"  Stale topics: {', '.join(entry['stale_topics'])}")
        lines.append(f"  {entry['summary']}")
        lines.append("")
    lines.append(
        "Do NOT name a current officeholder for these countries unless a [TOOL: ...] block in this "
        "request explicitly contains a fresh source. Instead, name the POSITION and flag the gap."
    )
    return "\n".join(lines)
