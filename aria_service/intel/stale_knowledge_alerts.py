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
from .engine_wiring import wire_failure

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
    {
        "country": "nigeria",
        "event_date": "2023-05",
        "event": "Presidential inauguration — Bola Tinubu (APC) replaced Muhammadu Buhari",
        "stale_topics": [
            "defence minister", "minister of defence", "chief of defence staff",
            "chief of army staff", "chief of naval staff", "chief of air staff",
            "national security adviser", "cabinet", "all ministerial appointments",
        ],
        "summary": (
            "Bola Tinubu inaugurated May 2023, replacing Buhari. Full cabinet and military "
            "leadership replaced. Multiple CDS/service chief rotations since. Any pre-May 2023 "
            "officeholder data is OBSOLETE. Tinubu also reshuffled cabinet in Oct 2024."
        ),
    },
    {
        "country": "turkey",
        "event_date": "2023-06",
        "event": "Cabinet reshuffle — Erdogan's third-term cabinet formed after May 2023 election",
        "stale_topics": [
            "defence minister", "minister of national defence",
            "foreign minister", "finance minister", "cabinet",
            "head of defence industries", "SSB president",
        ],
        "summary": (
            "Erdogan formed new cabinet June 2023 after re-election. Yasar Guler became Minister "
            "of National Defence. Haluk Gorgun became SSB president. Any pre-June 2023 Turkish "
            "defence leadership data needs verification."
        ),
    },
    {
        "country": "brazil",
        "event_date": "2023-01",
        "event": "Lula inauguration — full cabinet change from Bolsonaro era",
        "stale_topics": [
            "defence minister", "minister of defence", "commander of the army",
            "commander of the navy", "commander of the air force",
            "cabinet", "all ministerial appointments",
        ],
        "summary": (
            "Lula da Silva took office January 2023, replacing Bolsonaro. José Múcio became "
            "Minister of Defence. Complete military command rotation. Any Bolsonaro-era officeholder "
            "data is OBSOLETE."
        ),
    },
    {
        "country": "angola",
        "event_date": "2022-09",
        "event": "João Lourenço re-elected — cabinet continuity with rotations",
        "stale_topics": [
            "defence minister", "minister of national defence",
            "chief of general staff", "FAA commander",
        ],
        "summary": (
            "Lourenço re-elected August 2022. Some cabinet continuity but defence portfolio "
            "has seen rotations. Verify current Minister of National Defence and FAA leadership "
            "before any briefing — do NOT assume continuity from 2022."
        ),
    },
    {
        "country": "mozambique",
        "event_date": "2024-10",
        "event": "Daniel Chapo elected president — first post-Frelimo-founder transition",
        "stale_topics": [
            "president", "defence minister", "minister of national defence",
            "FADM commander", "cabinet", "all ministerial appointments",
        ],
        "summary": (
            "Daniel Chapo (Frelimo) won October 2024 election amid protests. New cabinet expected. "
            "Any Nyusi-era officeholder data needs verification. FADM leadership may change."
        ),
    },
    {
        "country": "kenya",
        "event_date": "2022-09",
        "event": "William Ruto inaugurated — cabinet change from Kenyatta era",
        "stale_topics": [
            "defence minister", "cabinet secretary for defence",
            "chief of defence forces", "cabinet",
        ],
        "summary": (
            "William Ruto took office September 2022, replacing Uhuru Kenyatta. Aden Duale became "
            "Cabinet Secretary for Defence. Multiple reshuffles since. Verify current appointments."
        ),
    },
    {
        "country": "saudi arabia",
        "event_date": "2024-03",
        "event": "Defence ministry restructuring under MBS modernisation",
        "stale_topics": [
            "deputy defence minister", "chief of general staff",
            "GAMI leadership", "defence procurement authority",
        ],
        "summary": (
            "Saudi Arabia has been restructuring defence procurement under Vision 2030. GAMI "
            "(General Authority for Military Industries) leadership changes. Verify current "
            "procurement authority chain before any engagement."
        ),
    },
    {
        "country": "ukraine",
        "event_date": "2024-09",
        "event": "Defence Minister Umerov appointed (replaced Reznikov Sept 2023) + ongoing wartime reshuffles",
        "stale_topics": [
            "defence minister", "commander-in-chief",
            "chief of general staff", "military leadership",
        ],
        "summary": (
            "Rustem Umerov replaced Reznikov as Defence Minister September 2023. Syrskyi replaced "
            "Zaluzhnyi as Commander-in-Chief February 2024. Wartime reshuffles ongoing — ALL "
            "Ukrainian military leadership data must be verified before use."
        ),
    },
    {
        "country": "senegal",
        "event_date": "2024-03",
        "event": "Bassirou Diomaye Faye elected president — opposition victory",
        "stale_topics": [
            "president", "prime minister", "defence minister", "cabinet",
            "all ministerial appointments",
        ],
        "summary": (
            "Bassirou Diomaye Faye won March 2024 election, defeating Macky Sall's coalition. "
            "Complete cabinet change. Any Sall-era officeholder data is OBSOLETE."
        ),
    },
    {
        "country": "indonesia",
        "event_date": "2024-10",
        "event": "Prabowo Subianto inaugurated as president",
        "stale_topics": [
            "president", "defence minister", "minister of defence",
            "TNI commander", "cabinet", "all ministerial appointments",
        ],
        "summary": (
            "Prabowo Subianto (former Defence Minister) became President October 2024. "
            "Full cabinet change including new Defence Minister. Any Jokowi-era officeholder "
            "data needs verification."
        ),
    },
]


def is_enabled() -> bool:
    """Feature flag — default ON. Set ARIA_STALE_KNOWLEDGE_ALERTS=0 to disable."""
    val = os.getenv("ARIA_STALE_KNOWLEDGE_ALERTS", "1") or "1"
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="stale_knowledge_alerts",
        summary="Is Enabled",
        source_id="stale_knowledge_alerts:R-F1001",
    )

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

# R-F2119 §21a — wire failure handler for stale_knowledge_alerts
try:
    wire_failure(module="stale_knowledge_alerts", detail="module shutdown",
                gap_type="engine_failure", source="stale_knowledge_alerts:shutdown")
except Exception:
    pass
